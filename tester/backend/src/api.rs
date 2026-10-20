//! HTTP API (tester/backend/specs/api.md). Paths under /api; the frontend is served from /.

use crate::{app::App, datasets, objects, runs, store};
use axum::{
    Json, Router,
    body::Body,
    extract::{Path, Query, State},
    http::{StatusCode, header},
    response::{IntoResponse, Response},
    routing::{get, post},
};
use serde::Deserialize;
use serde_json::{Value, json};
use std::sync::{Arc, atomic::Ordering};

type AppState = State<Arc<App>>;

pub struct ApiError(StatusCode, &'static str, String);

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (self.0, Json(json!({"error": {"code": self.1, "message": self.2}}))).into_response()
    }
}

impl From<anyhow::Error> for ApiError {
    fn from(e: anyhow::Error) -> Self {
        tracing::error!(error = %e, "request failed");
        ApiError(StatusCode::INTERNAL_SERVER_ERROR, "internal_error", e.to_string())
    }
}

type ApiResult<T> = Result<T, ApiError>;

fn not_found(what: &str) -> ApiError {
    ApiError(StatusCode::NOT_FOUND, "not_found", format!("{what} not found"))
}

pub fn router(app: Arc<App>) -> Router {
    Router::new()
        .route("/api/datasets", get(list_datasets))
        .route("/api/spend", get(spend))
        .route("/api/pipeline", get(pipeline))
        .route("/api/runs", get(list_runs).post(start_run))
        .route("/api/runs/{id}", get(get_run))
        .route("/api/runs/{id}/pause", post(|s, p| control(s, p, "pause")))
        .route("/api/runs/{id}/resume", post(|s, p| control(s, p, "resume")))
        .route("/api/runs/{id}/stop", post(|s, p| control(s, p, "stop")))
        .route("/api/runs/{id}/documents", get(list_documents))
        .route("/api/runs/{id}/documents/{invoice_id}", get(get_document))
        .route("/api/runs/{id}/documents/{invoice_id}/artifacts/{name}", get(artifact))
        .route("/healthz", get(healthz))
        .with_state(app)
}

async fn list_datasets(State(app): AppState) -> ApiResult<Json<Value>> {
    let list = datasets::list(&app.cfg.datasets_dir)?;
    let cost = app.cfg.cost_per_document_usd;
    Ok(Json(json!(list
        .iter()
        .map(|d| json!({"name": d.name, "total_documents": d.total_documents, "total_units": d.total_units,
                        "created_at": d.created_at, "estimated_cost_usd": d.total_documents as f64 * cost}))
        .collect::<Vec<_>>())))
}

async fn spend(State(app): AppState) -> ApiResult<Json<Value>> {
    let day = chrono::Utc::now().format("%Y-%m-%d").to_string();
    let spent = app.objects.spent_today(&day).await?;
    Ok(Json(json!({"day": day, "spent_usd": spent.to_string(), "daily_budget_usd": app.cfg.daily_budget_usd,
                   "cost_per_document_usd": app.cfg.cost_per_document_usd})))
}

async fn pipeline(State(app): AppState) -> Json<Value> {
    let p = app.pipeline.read().await;
    Json(json!({"services": p.services, "checked_at": p.checked_at, "waiting": p.waiting()}))
}

#[derive(Deserialize)]
struct StartRun {
    dataset: String,
    #[serde(default = "default_parallelism")]
    parallelism: i64,
}

fn default_parallelism() -> i64 {
    4
}

async fn start_run(State(app): AppState, Json(req): Json<StartRun>) -> ApiResult<(StatusCode, Json<store::Run>)> {
    if !datasets::valid_name(&req.dataset) || !app.cfg.datasets_dir.join(&req.dataset).join("manifest.json").exists() {
        return Err(ApiError(StatusCode::BAD_REQUEST, "unknown_dataset", format!("dataset {:?} not found", req.dataset)));
    }
    if !(1..=runs::MAX_PARALLELISM).contains(&req.parallelism) {
        return Err(ApiError(StatusCode::BAD_REQUEST, "invalid_parallelism", format!("parallelism must be 1..{}", runs::MAX_PARALLELISM)));
    }
    Ok((StatusCode::CREATED, Json(runs::start(&app, &req.dataset, req.parallelism).await?)))
}

async fn list_runs(State(app): AppState) -> ApiResult<Json<Value>> {
    let mut out = Vec::new();
    for run in store::list_runs(&app.db).await? {
        let counts = store::counts(&app.db, &run.id).await?;
        let summary = store::summary(&app.db, &run.id).await?;
        let models = summary.as_ref().and_then(|s| s.get("models").cloned());
        out.push(json!({"run": run, "counts": counts, "models": models}));
    }
    Ok(Json(json!(out)))
}

async fn get_run(State(app): AppState, Path(id): Path<String>) -> ApiResult<Json<Value>> {
    let run = store::get_run(&app.db, &id).await?.ok_or_else(|| not_found("run"))?;
    let counts = store::counts(&app.db, &id).await?;
    let summary = store::summary(&app.db, &id).await?;
    let waiting = app.pipeline.read().await.waiting();
    Ok(Json(json!({"run": run, "counts": counts, "waiting": waiting, "summary": summary})))
}

async fn control(State(app): AppState, Path(id): Path<String>, action: &'static str) -> ApiResult<Json<Value>> {
    match runs::control(&app, &id, action).await? {
        Ok(status) => Ok(Json(json!({"id": id, "status": status}))),
        Err(e) if e == "not_found" => Err(not_found("run")),
        Err(e) => Err(ApiError(StatusCode::CONFLICT, "invalid_transition", e)),
    }
}

#[derive(Deserialize)]
struct DocQuery {
    status: Option<String>,
    template_id: Option<String>,
    currency: Option<String>,
    #[serde(default)]
    offset: usize,
    #[serde(default = "default_limit")]
    limit: usize,
}

fn default_limit() -> usize {
    100
}

async fn list_documents(State(app): AppState, Path(id): Path<String>, Query(q): Query<DocQuery>) -> ApiResult<Json<Value>> {
    let run = store::get_run(&app.db, &id).await?.ok_or_else(|| not_found("run"))?;
    let golden = app.golden(&run.dataset).await?;
    let mut docs: Vec<Value> = Vec::new();
    for d in store::documents(&app.db, &id).await? {
        let g = golden.get(&d.invoice_id);
        let template = g.map(|g| g.template_id.clone()).unwrap_or_default();
        let currency = g.map(|g| g.currency.clone()).unwrap_or_default();
        if q.status.as_deref().is_some_and(|s| s != d.status)
            || q.template_id.as_deref().is_some_and(|t| t != template)
            || q.currency.as_deref().is_some_and(|c| c != currency)
        {
            continue;
        }
        let mut v = serde_json::to_value(&d).map_err(anyhow::Error::from)?;
        v["template_id"] = template.into();
        v["currency"] = currency.into();
        docs.push(v);
    }
    let total = docs.len();
    let page: Vec<Value> = docs.into_iter().skip(q.offset).take(q.limit.min(1000)).collect();
    Ok(Json(json!({"total": total, "documents": page})))
}

async fn get_document(State(app): AppState, Path((id, invoice_id)): Path<(String, String)>) -> ApiResult<Json<Value>> {
    let run = store::get_run(&app.db, &id).await?.ok_or_else(|| not_found("run"))?;
    let doc = store::documents(&app.db, &id).await?.into_iter().find(|d| d.invoice_id == invoice_id).ok_or_else(|| not_found("document"))?;
    let (fields, lines, extracted) = store::document_results(&app.db, &id, &invoice_id).await?;
    let golden = app.golden(&run.dataset).await?;
    let g = golden.get(&invoice_id);
    let artifacts = available_artifacts(&app, doc.document_id.as_deref()).await;
    Ok(Json(json!({
        "document": doc,
        "template_id": g.map(|g| g.template_id.clone()),
        "currency": g.map(|g| g.currency.clone()),
        "language": g.map(|g| g.language.clone()),
        "fields": fields,
        "lines": lines,
        "extracted": extracted.and_then(|s| serde_json::from_str::<Value>(&s).ok()),
        "golden": g.map(|g| &g.invoice),
        "artifacts": artifacts,
    })))
}

const ARTIFACTS: [&str; 5] = ["original.pdf", "layout.md", "extracted.json", "text-extractor-failure.json", "fields-extractor-failure.json"];

/// (bucket, key) of an artifact in MinIO; original.pdf is served from the dataset instead.
fn artifact_location(document_id: &str, name: &str) -> Option<(&'static str, String)> {
    match name {
        "layout.md" | "extracted.json" => Some((objects::BUCKET, format!("documents/{document_id}/{name}"))),
        "text-extractor-failure.json" => Some(("text-extractor-failures", format!("{document_id}.json"))),
        "fields-extractor-failure.json" => Some(("fields-extractor-failures", format!("{document_id}.json"))),
        _ => None,
    }
}

async fn available_artifacts(app: &App, document_id: Option<&str>) -> Vec<&'static str> {
    let mut out = vec!["original.pdf"];
    if let Some(doc) = document_id {
        for name in &ARTIFACTS[1..] {
            if let Some((bucket, key)) = artifact_location(doc, name)
                && matches!(app.objects.get(bucket, &key).await, Ok(Some(_)))
            {
                out.push(name);
            }
        }
    }
    out
}

async fn artifact(State(app): AppState, Path((id, invoice_id, name)): Path<(String, String, String)>) -> ApiResult<Response> {
    let run = store::get_run(&app.db, &id).await?.ok_or_else(|| not_found("run"))?;
    if !ARTIFACTS.contains(&name.as_str()) {
        return Err(not_found("artifact"));
    }
    let content_type = match name.rsplit('.').next() {
        Some("pdf") => "application/pdf",
        Some("md") => "text/markdown; charset=utf-8",
        _ => "application/json",
    };
    let data = if name == "original.pdf" {
        let path = datasets::document_path(&app.cfg.datasets_dir, &run.dataset, &invoice_id).map_err(|_| not_found("document"))?;
        tokio::fs::read(path).await.map_err(anyhow::Error::from)?
    } else {
        let doc = store::documents(&app.db, &id).await?.into_iter().find(|d| d.invoice_id == invoice_id).ok_or_else(|| not_found("document"))?;
        let document_id = doc.document_id.ok_or_else(|| not_found("artifact"))?;
        let (bucket, key) = artifact_location(&document_id, &name).ok_or_else(|| not_found("artifact"))?;
        app.objects.get(bucket, &key).await?.ok_or_else(|| not_found("artifact"))?
    };
    Ok(([(header::CONTENT_TYPE, content_type)], Body::from(data)).into_response())
}

async fn healthz(State(app): AppState) -> Response {
    let mut failing = Vec::new();
    let mut errors = serde_json::Map::new();
    let mut fail = |name: &str, e: String| {
        failing.push(name.to_string());
        errors.insert(name.to_string(), e.into());
    };
    if let Err(e) = sqlx::query("SELECT 1").execute(&app.db).await {
        fail("sqlite", e.to_string());
    }
    if let Err(e) = sqlx::query("SELECT 1").execute(&app.pg).await {
        fail("postgres", e.to_string());
    }
    if !app.failures_connected.load(Ordering::Relaxed) {
        fail("rabbitmq", "documents.failed consumer not connected".into());
    }
    if let Err(e) = app.objects.check().await {
        fail("minio", e.to_string());
    }
    if let Err(e) = std::fs::read_dir(&app.cfg.datasets_dir) {
        fail("datasets", format!("{}: {e}", app.cfg.datasets_dir.display()));
    }
    // Shape: /specs/pipeline-contracts.md, Health endpoint.
    if failing.is_empty() {
        (StatusCode::OK, Json(json!({"status": "ok"}))).into_response()
    } else {
        (StatusCode::SERVICE_UNAVAILABLE, Json(json!({"status": "unavailable", "failing": failing, "errors": errors}))).into_response()
    }
}
