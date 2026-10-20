//! Starting runs and uploading their documents (tester/backend/specs/overview.md, Runs).

use crate::{
    app::{App, Control},
    datasets, store,
};
use anyhow::{Result, bail};
use serde::Deserialize;
use std::{collections::HashMap, sync::Arc, time::Duration};
use tokio::sync::{Semaphore, watch};
use uuid::Uuid;

pub const MAX_PARALLELISM: i64 = 32;
const UPLOAD_BACKOFF: [u64; 4] = [1, 2, 4, 8];

pub async fn start(app: &Arc<App>, dataset: &str, parallelism: i64) -> Result<store::Run> {
    if !datasets::valid_name(dataset) {
        bail!("invalid dataset name");
    }
    if !(1..=MAX_PARALLELISM).contains(&parallelism) {
        bail!("parallelism must be 1..{MAX_PARALLELISM}");
    }
    let docs = datasets::documents(&app.cfg.datasets_dir, dataset)?;
    app.golden(dataset).await?; // fail early if the golden set cannot be read
    let id = Uuid::new_v4().to_string();
    let ids: Vec<String> = docs.iter().map(|d| d.invoice_id.clone()).collect();
    store::create_run(&app.db, &id, dataset, parallelism, &ids).await?;
    tracing::info!(run_id = %id, dataset, parallelism, documents = ids.len(), "run started");
    spawn_uploader(app.clone(), id.clone()).await;
    Ok(store::get_run(&app.db, &id).await?.expect("just created"))
}

/// Pause, resume or stop a run. Returns the new status, or an error message for an invalid transition.
pub async fn control(app: &Arc<App>, id: &str, action: &str) -> Result<Result<String, String>> {
    let Some(run) = store::get_run(&app.db, id).await? else { return Ok(Err("not_found".into())) };
    let next = match (action, run.status.as_str()) {
        ("pause", "running") => "paused",
        ("resume", "paused") => "running",
        ("stop", "running" | "paused") => "stopped",
        _ => return Ok(Err(format!("cannot {action} a {} run", run.status))),
    };
    store::set_run_status(&app.db, id, next).await?;
    let mut controls = app.controls.lock().await;
    match next {
        "running" => {
            drop(controls);
            spawn_uploader(app.clone(), id.to_string()).await;
        }
        _ => {
            if let Some(tx) = controls.remove(id) {
                let _ = tx.send(if next == "paused" { Control::Pause } else { Control::Stop });
            }
        }
    }
    tracing::info!(run_id = id, action, status = next, "run control");
    Ok(Ok(next.to_string()))
}

async fn spawn_uploader(app: Arc<App>, run_id: String) {
    let (tx, rx) = watch::channel(Control::Run);
    app.controls.lock().await.insert(run_id.clone(), tx);
    tokio::spawn(async move {
        if let Err(e) = upload_all(&app, &run_id, rx).await {
            tracing::error!(run_id = %run_id, error = %e, "uploader failed");
        }
    });
}

/// Uploads the run's pending documents, at most `parallelism` at a time, until done, paused or stopped.
async fn upload_all(app: &Arc<App>, run_id: &str, control: watch::Receiver<Control>) -> Result<()> {
    let Some(run) = store::get_run(&app.db, run_id).await? else { return Ok(()) };
    let paths: HashMap<String, std::path::PathBuf> = datasets::documents(&app.cfg.datasets_dir, &run.dataset)?
        .into_iter()
        .map(|d| (d.invoice_id, d.path))
        .collect();
    let slots = Arc::new(Semaphore::new(run.parallelism as usize));
    let mut tasks = tokio::task::JoinSet::new();
    for invoice_id in store::pending(&app.db, run_id).await? {
        let permit = slots.clone().acquire_owned().await?;
        if *control.borrow() != Control::Run {
            break; // paused or stopped: no new uploads; in-flight ones finish
        }
        let key = format!("{run_id}/{invoice_id}");
        if !app.inflight.lock().expect("inflight lock").insert(key.clone()) {
            continue; // still in flight from before a pause
        }
        let (app, run_id, path) = (app.clone(), run_id.to_string(), paths.get(&invoice_id).cloned());
        tasks.spawn(async move {
            let _permit = permit;
            let result = match path {
                Some(p) => upload(&app, &p).await,
                None => Err(UploadError::Permanent("document file not found".into())),
            };
            let stored = match result {
                Ok(r) => store::mark_uploaded(&app.db, &run_id, &invoice_id, &r.document_id, &r.correlation_id).await,
                Err(UploadError::Permanent(m) | UploadError::Retryable(m)) => {
                    tracing::warn!(run_id = %run_id, invoice_id = %invoice_id, error = %m, "upload failed");
                    store::mark_error(&app.db, &run_id, &invoice_id, "tester", "upload_failed", &m).await
                }
            };
            if let Err(e) = stored {
                tracing::error!(error = %e, "cannot store upload result");
            }
            app.inflight.lock().expect("inflight lock").remove(&key);
        });
    }
    while tasks.join_next().await.is_some() {}
    if *control.borrow() == Control::Run {
        app.controls.lock().await.remove(run_id);
    }
    Ok(())
}

#[derive(Deserialize)]
struct Accepted {
    document_id: String,
    correlation_id: String,
}

enum UploadError {
    Retryable(String),
    Permanent(String),
}

/// POST /documents with a new correlation_id; 503 and connection errors are retried (1, 2, 4, 8 s).
async fn upload(app: &App, path: &std::path::Path) -> Result<Accepted, UploadError> {
    let data = tokio::fs::read(path).await.map_err(|e| UploadError::Permanent(format!("read {}: {e}", path.display())))?;
    let name = path.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_else(|| "invoice.pdf".into());
    let mut last = String::new();
    for attempt in 0..=UPLOAD_BACKOFF.len() {
        if attempt > 0 {
            tokio::time::sleep(Duration::from_secs(UPLOAD_BACKOFF[attempt - 1])).await;
        }
        let correlation_id = Uuid::new_v4().to_string(); // one per request (contract)
        let form = reqwest::multipart::Form::new()
            .part("file", reqwest::multipart::Part::bytes(data.clone()).file_name(name.clone()).mime_str("application/pdf").expect("valid mime"))
            .text("correlation_id", correlation_id);
        match app.http.post(format!("{}/documents", app.cfg.ingestor_url)).multipart(form).timeout(Duration::from_secs(60)).send().await {
            Ok(resp) if resp.status().as_u16() == 202 => {
                return resp.json::<Accepted>().await.map_err(|e| UploadError::Permanent(format!("bad response: {e}")));
            }
            Ok(resp) if resp.status().as_u16() == 503 => last = format!("ingestor 503: {}", resp.text().await.unwrap_or_default()),
            Ok(resp) => {
                let status = resp.status();
                return Err(UploadError::Permanent(format!("ingestor {status}: {}", resp.text().await.unwrap_or_default())));
            }
            Err(e) => last = format!("ingestor unreachable: {e}"),
        }
    }
    Err(UploadError::Retryable(last))
}
