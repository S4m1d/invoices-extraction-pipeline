//! Completion: rows in PostgreSQL, failure messages, run endings; no per-document timeout
//! (tester/backend/specs/overview.md, Completion; ADR 0016).

use crate::{
    analytics,
    app::App,
    compare::{self, Invoice, Line},
    sources::RequiredFields,
    store, validate,
};
use anyhow::Result;
use futures::StreamExt;
use lapin::{
    Connection, ConnectionProperties,
    options::{BasicAckOptions, BasicConsumeOptions, BasicQosOptions},
    types::FieldTable,
    uri::AMQPUri,
};
use serde::Deserialize;
use sqlx::Row;
use std::{collections::HashMap, sync::Arc, time::{Duration, Instant}};
use tokio::sync::Mutex;

const TICK: Duration = Duration::from_secs(2);
/// A failure may arrive before the upload response was stored; it waits this long for its document.
const UNMATCHED_GRACE: Duration = Duration::from_secs(120);

#[derive(Debug, Clone, Deserialize)]
pub struct Failure {
    pub document_id: String,
    pub stage: String,
    pub error_code: String,
    #[serde(default)]
    pub error_message: String,
}

pub type Unmatched = Arc<Mutex<Vec<(Failure, Instant)>>>;

/// Consumes documents.failed; reconnects on errors.
pub async fn consume_failures(app: Arc<App>, unmatched: Unmatched) {
    loop {
        if let Err(e) = consume_once(&app, &unmatched).await {
            app.failures_connected.store(false, std::sync::atomic::Ordering::Relaxed);
            tracing::error!(error = %e, "documents.failed consumer lost, reconnecting");
        }
        tokio::time::sleep(Duration::from_secs(5)).await;
    }
}

/// Parses AMQP_URL. The pipeline's URLs end in `/` (`amqp://user:pass@rabbitmq:5672/`), which the
/// other services' clients read as vhost `/`; the AMQP URI spec, and lapin, read it as the empty
/// vhost. Empty becomes `/` so the tester matches them.
fn amqp_uri(url: &str) -> Result<AMQPUri> {
    let mut uri: AMQPUri = url.parse().map_err(|e: String| anyhow::anyhow!("invalid AMQP_URL: {e}"))?;
    if uri.vhost.is_empty() {
        uri.vhost = "/".into();
    }
    Ok(uri)
}

async fn consume_once(app: &Arc<App>, unmatched: &Unmatched) -> Result<()> {
    let conn = Connection::connect_uri(amqp_uri(&app.cfg.amqp_url)?, ConnectionProperties::default()).await?;
    let ch = conn.create_channel().await?;
    ch.basic_qos(16, BasicQosOptions::default()).await?;
    let mut consumer = ch
        .basic_consume("documents.failed".into(), "tester".into(), BasicConsumeOptions::default(), FieldTable::default())
        .await?;
    app.failures_connected.store(true, std::sync::atomic::Ordering::Relaxed);
    tracing::info!("consuming documents.failed");
    while let Some(delivery) = consumer.next().await {
        let delivery = delivery?;
        match serde_json::from_slice::<Failure>(&delivery.data) {
            Ok(f) => {
                if !apply_failure(app, &f).await? {
                    unmatched.lock().await.push((f, Instant::now()));
                }
            }
            Err(e) => tracing::warn!(error = %e, "unreadable failure message dropped"),
        }
        delivery.ack(BasicAckOptions::default()).await?;
    }
    anyhow::bail!("consumer stream ended")
}

/// Applies a failure to its document. Returns false if no run knows the document (yet).
async fn apply_failure(app: &App, f: &Failure) -> Result<bool> {
    let Some((run_id, invoice_id, status)) = store::find_by_document(&app.db, &f.document_id).await? else {
        return Ok(false);
    };
    if status != "uploaded" {
        return Ok(true); // already finished
    }
    if f.error_code == "duplicate_document" && row_exists(app, &f.document_id).await? {
        tracing::info!(document_id = %f.document_id, "duplicate_document ignored: row exists (ADR 0012)");
        return Ok(true);
    }
    store::mark_error(&app.db, &run_id, &invoice_id, &f.stage, &f.error_code, &f.error_message).await?;
    tracing::info!(run_id = %run_id, invoice_id = %invoice_id, stage = %f.stage, error_code = %f.error_code, "document failed in pipeline");
    Ok(true)
}

async fn row_exists(app: &App, document_id: &str) -> Result<bool> {
    let n: i64 = sqlx::query_scalar("SELECT count(*) FROM invoices WHERE document_id::text = $1").bind(document_id).fetch_one(&app.pg).await?;
    Ok(n > 0)
}

/// Every 2 s: find rows, match late failures, end runs. Documents are tracked until they finish,
/// however long they wait in the pipeline's queues.
pub async fn track(app: Arc<App>, unmatched: Unmatched) {
    loop {
        tokio::time::sleep(TICK).await;
        if let Err(e) = tick(&app, &unmatched).await {
            tracing::error!(error = %e, "tracker tick failed");
        }
    }
}

async fn tick(app: &Arc<App>, unmatched: &Unmatched) -> Result<()> {
    // Late failures first, so a document is not compared while its failure waits.
    {
        let mut pending = unmatched.lock().await;
        let mut keep = Vec::new();
        for (f, at) in pending.drain(..) {
            if !apply_failure(app, &f).await? && at.elapsed() < UNMATCHED_GRACE {
                keep.push((f, at));
            }
        }
        *pending = keep;
    }

    let tracked = store::tracked(&app.db).await?;
    if !tracked.is_empty() {
        let ids: Vec<String> = tracked.iter().map(|t| t.document_id.clone()).collect();
        let found = extracted(app, &ids).await?;
        for t in &tracked {
            if let Some((invoice, model, prompt)) = found.get(&t.document_id) {
                if t.kind == "documents" {
                    let required = match app.source(&t.source) {
                        Ok(s) => s.required_fields.clone().unwrap_or_default(),
                        Err(e) => {
                            tracing::warn!(run_id = %t.run_id, error = %e, "validating with the default required fields");
                            RequiredFields::default()
                        }
                    };
                    let v = validate::validate(invoice, &required);
                    store::store_validation(&app.db, t, invoice, model.clone(), prompt.clone(), &v).await?;
                    continue;
                }
                let golden = app.golden(&t.source, &t.dataset).await?;
                match golden.get(&t.invoice_id) {
                    Some(g) => {
                        let c = compare::compare(invoice, &g.invoice, g.unit_price_printed);
                        store::store_comparison(&app.db, t, invoice, model.clone(), prompt.clone(), &c).await?;
                    }
                    None => store::mark_error(&app.db, &t.run_id, &t.invoice_id, "tester", "no_golden_record", "invoice not in golden set").await?,
                }
            }
        }
    }
    end_runs(app).await
}

/// Invoices (with lines) for the given document ids that are in PostgreSQL.
async fn extracted(app: &App, ids: &[String]) -> Result<HashMap<String, (Invoice, Option<String>, Option<String>)>> {
    let rows = sqlx::query(
        "SELECT id::text AS id, document_id::text AS document_id, supplier_name, recipient_name, invoice_number,
                issue_date::text AS issue_date, due_date::text AS due_date, currency::text AS currency,
                subtotal::text AS subtotal, tax_total::text AS tax_total, grand_total::text AS grand_total, tax_rate::text AS tax_rate,
                extraction_model, prompt_version
         FROM invoices WHERE document_id::text = ANY($1)",
    )
    .bind(ids)
    .fetch_all(&app.pg)
    .await?;
    let mut out = HashMap::new();
    for r in rows {
        let id: String = r.get("id");
        let lines = sqlx::query(
            "SELECT line_no, description, quantity::text AS quantity, unit_price::text AS unit_price, amount::text AS amount
             FROM invoice_lines WHERE invoice_id::text = $1 ORDER BY line_no",
        )
        .bind(&id)
        .fetch_all(&app.pg)
        .await?
        .iter()
        .map(|l| Line {
            line_no: l.get::<i32, _>("line_no") as i64,
            description: l.get("description"),
            quantity: l.get("quantity"),
            unit_price: l.get("unit_price"),
            amount: l.get("amount"),
        })
        .collect();
        let invoice = Invoice {
            supplier_name: r.get("supplier_name"),
            recipient_name: r.get("recipient_name"),
            invoice_number: r.get("invoice_number"),
            issue_date: r.get("issue_date"),
            due_date: r.get("due_date"),
            currency: r.get("currency"),
            subtotal: r.get("subtotal"),
            tax_total: r.get("tax_total"),
            grand_total: r.get("grand_total"),
            tax_rate: r.get("tax_rate"),
            lines,
        };
        out.insert(r.get("document_id"), (invoice, r.get("extraction_model"), r.get("prompt_version")));
    }
    Ok(out)
}

/// Running runs with everything finished become completed. Stopped runs are finished by Stop itself
/// (runs::control); this catches one whose Stop did not get that far.
async fn end_runs(app: &Arc<App>) -> Result<()> {
    for run in store::runs_with_status(&app.db, &["running", "stopped"]).await? {
        if run.finished_at.is_some() {
            continue;
        }
        let (pending, uploaded) = store::unfinished(&app.db, &run.id).await?;
        let done = uploaded == 0 && (pending == 0 || run.status == "stopped");
        if !done {
            continue;
        }
        let status = if run.status == "stopped" { "stopped" } else { "completed" };
        let summary = analytics::summary(app, &run).await?;
        store::finish_run(&app.db, &run.id, status, &summary).await?;
        tracing::info!(run_id = %run.id, status, "run finished");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn amqp_uri_vhost() {
        assert_eq!(amqp_uri("amqp://u:p@rabbitmq:5672/").unwrap().vhost, "/");
        assert_eq!(amqp_uri("amqp://u:p@rabbitmq:5672").unwrap().vhost, "/");
        assert_eq!(amqp_uri("amqp://u:p@rabbitmq:5672/%2f").unwrap().vhost, "/");
        assert_eq!(amqp_uri("amqp://u:p@rabbitmq:5672/test").unwrap().vhost, "test");
    }
}
