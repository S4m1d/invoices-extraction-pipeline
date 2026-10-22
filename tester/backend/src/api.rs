//! HTTP API (tester/backend/specs/api.md). Paths under /api; the frontend is served from /.

use crate::{app::App, datasets, objects, runs, sources::Kind, store};
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
        .route("/api/sources", get(list_sources))
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

async fn list_sources(State(app): AppState) -> Json<Value> {
    Json(json!(app
        .sources
        .iter()
        .map(|s| {
            let unavailable = s.unavailable();
            json!({"name": s.name, "kind": s.kind, "connection_type": s.connection_type,
                   "available": unavailable.is_none(), "reason": unavailable, "required_fields": s.required_fields})
        })
        .collect::<Vec<_>>()))
}

#[derive(Deserialize)]
struct DatasetQuery {
    source: Option<String>,
}

async fn list_datasets(State(app): AppState, Query(q): Query<DatasetQuery>) -> ApiResult<Json<Value>> {
    let cost = app.cfg.cost_per_document_usd;
    let mut out = Vec::new();
    for source in app.sources.iter().filter(|s| q.source.as_deref().is_none_or(|n| n == s.name)) {
        if source.unavailable().is_some() {
            continue; // shown with its reason in /api/sources
        }
        for d in datasets::list(source)? {
            let mut v = serde_json::to_value(&d).map_err(anyhow::Error::from)?;
            v["estimated_cost_usd"] = (d.total_documents as f64 * cost).into();
            out.push(v);
        }
    }
    Ok(Json(json!(out)))
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
    source: String,
    dataset: String,
    #[serde(default = "default_parallelism")]
    parallelism: i64,
}

fn default_parallelism() -> i64 {
    4
}

async fn start_run(State(app): AppState, Json(req): Json<StartRun>) -> ApiResult<(StatusCode, Json<store::Run>)> {
    let source = app.source(&req.source).map_err(|_| not_found("source"))?;
    let known = datasets::valid_name(&req.dataset)
        && source.unavailable().is_none()
        && datasets::list(source)?.iter().any(|d| d.name == req.dataset);
    if !known {
        return Err(not_found("dataset"));
    }
    if !(1..=runs::MAX_PARALLELISM).contains(&req.parallelism) {
        return Err(ApiError(StatusCode::BAD_REQUEST, "invalid_parallelism", format!("parallelism must be 1..{}", runs::MAX_PARALLELISM)));
    }
    Ok((StatusCode::CREATED, Json(runs::start(&app, &req.source, &req.dataset, req.parallelism).await?)))
}

#[derive(Deserialize)]
struct RunQuery {
    kind: Option<String>,
}

async fn list_runs(State(app): AppState, Query(q): Query<RunQuery>) -> ApiResult<Json<Value>> {
    let mut out = Vec::new();
    for run in store::list_runs(&app.db).await?.into_iter().filter(|r| q.kind.as_deref().is_none_or(|k| k == r.kind)) {
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
    // template and currency: golden metadata for synthetic runs, extracted currency for documents runs
    let meta: std::collections::HashMap<String, (String, String)> = if run.kind == Kind::Documents.as_str() {
        store::extracted_currencies(&app.db, &id).await?.into_iter().map(|(k, c)| (k, (String::new(), c))).collect()
    } else {
        app.golden(&run.source, &run.dataset).await?.iter().map(|(k, g)| (k.clone(), (g.template_id.clone(), g.currency.clone()))).collect()
    };
    let mut docs: Vec<Value> = Vec::new();
    for d in store::documents(&app.db, &id).await? {
        let (template, currency) = meta.get(&d.invoice_id).cloned().unwrap_or_default();
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
    let r = store::document_results(&app.db, &id, &invoice_id).await?;
    let extracted = r.extracted.and_then(|s| serde_json::from_str::<Value>(&s).ok());
    let artifacts = available_artifacts(&app, doc.document_id.as_deref()).await;
    if run.kind == Kind::Documents.as_str() {
        return Ok(Json(json!({
            "kind": run.kind,
            "document": doc,
            "currency": extracted.as_ref().and_then(|e| e.get("currency").cloned()),
            "fields": r.fields,
            "checks": r.checks,
            "extracted": extracted,
            "artifacts": artifacts,
        })));
    }
    let golden = app.golden(&run.source, &run.dataset).await?;
    let g = golden.get(&invoice_id);
    Ok(Json(json!({
        "kind": run.kind,
        "document": doc,
        "template_id": g.map(|g| g.template_id.clone()),
        "currency": g.map(|g| g.currency.clone()),
        "language": g.map(|g| g.language.clone()),
        "fields": r.fields,
        "lines": r.lines,
        "extracted": extracted,
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
        let source = app.source(&run.source).map_err(|_| not_found("source"))?;
        let path = datasets::document_path(source, &run.dataset, &invoice_id).map_err(|_| not_found("document"))?;
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
    // Shape: /specs/pipeline-contracts.md, Health endpoint.
    if failing.is_empty() {
        (StatusCode::OK, Json(json!({"status": "ok"}))).into_response()
    } else {
        (StatusCode::SERVICE_UNAVAILABLE, Json(json!({"status": "unavailable", "failing": failing, "errors": errors}))).into_response()
    }
}
