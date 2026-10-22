//! Run results in SQLite (tester/backend/specs/data-model.md).

use crate::{
    compare::{Comparison, Invoice},
    validate::Validation,
};
use anyhow::Result;
use chrono::Utc;
use serde::Serialize;
use sqlx::{
    Row, SqlSafeStr, SqlitePool,
    migrate::{Migration, MigrationType, Migrator},
    sqlite::{SqliteConnectOptions, SqliteJournalMode, SqlitePoolOptions},
};
use std::{path::Path, str::FromStr};

pub fn now() -> String {
    Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true)
}

pub async fn open(path: &Path) -> Result<SqlitePool> {
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    let opts = SqliteConnectOptions::from_str(&format!("sqlite://{}", path.display()))?
        .create_if_missing(true)
        .journal_mode(SqliteJournalMode::Wal)
        .foreign_keys(true);
    let pool = SqlitePoolOptions::new().max_connections(8).connect_with(opts).await?;
    migrator().run(&pool).await?;
    Ok(pool)
}

/// Migrations embedded in the binary. Not `sqlx::migrate!`: its `macros` feature changes shared
/// dependency features and forces a full rebuild of the bundled DuckDB.
fn migrator() -> Migrator {
    let m = |version, description: &'static str, sql: &'static str| {
        Migration::new(version, description.into(), MigrationType::Simple, sql.into_sql_str(), false)
    };
    Migrator::with_migrations(vec![
        m(1, "runs", include_str!("migrations/0001_runs.sql")),
        m(2, "data sources", include_str!("migrations/0002_data_sources.sql")),
    ])
}

#[derive(Debug, Clone, Serialize)]
pub struct Run {
    pub id: String,
    pub source: String,
    pub dataset: String,
    /// `synthetic` or `documents` (sources::Kind).
    pub kind: String,
    pub parallelism: i64,
    pub status: String,
    pub documents_total: i64,
    pub started_at: String,
    pub finished_at: Option<String>,
    pub overlapped: bool,
}

fn run_from(r: &sqlx::sqlite::SqliteRow) -> Run {
    Run {
        id: r.get("id"),
        source: r.get("source"),
        dataset: r.get("dataset"),
        kind: r.get("kind"),
        parallelism: r.get("parallelism"),
        status: r.get("status"),
        documents_total: r.get("documents_total"),
        started_at: r.get("started_at"),
        finished_at: r.get("finished_at"),
        overlapped: r.get::<i64, _>("overlapped") != 0,
    }
}

const RUN_COLUMNS: &str = "SELECT id, source, dataset, kind, parallelism, status, documents_total, started_at, finished_at, overlapped FROM runs";

pub struct NewRun<'a> {
    pub id: &'a str,
    pub source: &'a str,
    pub dataset: &'a str,
    pub kind: &'a str,
    pub parallelism: i64,
}

pub async fn create_run(db: &SqlitePool, run: NewRun<'_>, invoice_ids: &[String]) -> Result<()> {
    let mut tx = db.begin().await?;
    // Another run active at the same time: both runs' durations are not comparable (overview, Starting).
    let active: i64 = sqlx::query_scalar("SELECT count(*) FROM runs WHERE status IN ('running', 'paused')")
        .fetch_one(&mut *tx)
        .await?;
    sqlx::query("UPDATE runs SET overlapped = 1 WHERE status IN ('running', 'paused')").execute(&mut *tx).await?;
    sqlx::query(
        "INSERT INTO runs (id, source, dataset, kind, parallelism, status, documents_total, started_at, overlapped) VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?)",
    )
    .bind(run.id)
    .bind(run.source)
    .bind(run.dataset)
    .bind(run.kind)
    .bind(run.parallelism)
    .bind(invoice_ids.len() as i64)
    .bind(now())
    .bind((active > 0) as i64)
    .execute(&mut *tx)
    .await?;
    for invoice_id in invoice_ids {
        sqlx::query("INSERT INTO run_documents (run_id, invoice_id, status) VALUES (?, ?, 'pending')")
            .bind(run.id)
            .bind(invoice_id)
            .execute(&mut *tx)
            .await?;
    }
    tx.commit().await?;
    Ok(())
}

pub async fn get_run(db: &SqlitePool, id: &str) -> Result<Option<Run>> {
    Ok(sqlx::query(sqlx::AssertSqlSafe(format!("{RUN_COLUMNS} WHERE id = ?"))).bind(id).fetch_optional(db).await?.map(|r| run_from(&r)))
}

pub async fn list_runs(db: &SqlitePool) -> Result<Vec<Run>> {
    Ok(sqlx::query(sqlx::AssertSqlSafe(format!("{RUN_COLUMNS} ORDER BY started_at DESC"))).fetch_all(db).await?.iter().map(run_from).collect())
}

pub async fn runs_with_status(db: &SqlitePool, statuses: &[&str]) -> Result<Vec<Run>> {
    let all = list_runs(db).await?;
    Ok(all.into_iter().filter(|r| statuses.contains(&r.status.as_str())).collect())
}

pub async fn set_run_status(db: &SqlitePool, id: &str, status: &str) -> Result<()> {
    sqlx::query("UPDATE runs SET status = ? WHERE id = ?").bind(status).bind(id).execute(db).await?;
    Ok(())
}

pub async fn finish_run(db: &SqlitePool, id: &str, status: &str, summary: &serde_json::Value) -> Result<()> {
    sqlx::query("UPDATE runs SET status = ?, finished_at = ?, summary_json = ? WHERE id = ?")
        .bind(status)
        .bind(now())
        .bind(summary.to_string())
        .bind(id)
        .execute(db)
        .await?;
    Ok(())
}

pub async fn summary(db: &SqlitePool, id: &str) -> Result<Option<serde_json::Value>> {
    let s: Option<String> = sqlx::query_scalar("SELECT summary_json FROM runs WHERE id = ?").bind(id).fetch_optional(db).await?.flatten();
    Ok(s.and_then(|s| serde_json::from_str(&s).ok()))
}

/// On startup: running runs become paused (overview, Stop, pause and resume).
pub async fn pause_running_runs(db: &SqlitePool) -> Result<u64> {
    Ok(sqlx::query("UPDATE runs SET status = 'paused' WHERE status = 'running'").execute(db).await?.rows_affected())
}

pub async fn counts(db: &SqlitePool, run_id: &str) -> Result<serde_json::Map<String, serde_json::Value>> {
    let rows = sqlx::query("SELECT status, count(*) AS n FROM run_documents WHERE run_id = ? GROUP BY status")
        .bind(run_id)
        .fetch_all(db)
        .await?;
    let mut m = serde_json::Map::new();
    for s in ["pending", "uploaded", "passed", "failed", "error", "timeout"] {
        m.insert(s.into(), 0.into());
    }
    for r in rows {
        m.insert(r.get::<String, _>("status"), r.get::<i64, _>("n").into());
    }
    Ok(m)
}

pub async fn pending(db: &SqlitePool, run_id: &str) -> Result<Vec<String>> {
    Ok(sqlx::query_scalar("SELECT invoice_id FROM run_documents WHERE run_id = ? AND status = 'pending' ORDER BY invoice_id")
        .bind(run_id)
        .fetch_all(db)
        .await?)
}

pub async fn mark_uploaded(db: &SqlitePool, run_id: &str, invoice_id: &str, document_id: &str, correlation_id: &str) -> Result<()> {
    sqlx::query("UPDATE run_documents SET status = 'uploaded', document_id = ?, correlation_id = ?, uploaded_at = ? WHERE run_id = ? AND invoice_id = ?")
        .bind(document_id)
        .bind(correlation_id)
        .bind(now())
        .bind(run_id)
        .bind(invoice_id)
        .execute(db)
        .await?;
    Ok(())
}

pub async fn mark_error(db: &SqlitePool, run_id: &str, invoice_id: &str, stage: &str, code: &str, message: &str) -> Result<()> {
    sqlx::query("UPDATE run_documents SET status = 'error', error_stage = ?, error_code = ?, error_message = ?, finished_at = ? WHERE run_id = ? AND invoice_id = ? AND status IN ('pending', 'uploaded')")
        .bind(stage)
        .bind(code)
        .bind(message)
        .bind(now())
        .bind(run_id)
        .bind(invoice_id)
        .execute(db)
        .await?;
    Ok(())
}

#[derive(Debug, Clone)]
pub struct Tracked {
    pub run_id: String,
    pub source: String,
    pub dataset: String,
    pub kind: String,
    pub invoice_id: String,
    pub document_id: String,
    pub active_wait_seconds: f64,
}

/// Uploaded documents still waiting for their result, across all runs.
pub async fn tracked(db: &SqlitePool) -> Result<Vec<Tracked>> {
    let rows = sqlx::query(
        "SELECT d.run_id, r.source, r.dataset, r.kind, d.invoice_id, d.document_id, d.active_wait_seconds FROM run_documents d JOIN runs r ON r.id = d.run_id WHERE d.status = 'uploaded'",
    )
    .fetch_all(db)
    .await?;
    Ok(rows
        .iter()
        .map(|r| Tracked {
            run_id: r.get("run_id"),
            source: r.get("source"),
            dataset: r.get("dataset"),
            kind: r.get("kind"),
            invoice_id: r.get("invoice_id"),
            document_id: r.get("document_id"),
            active_wait_seconds: r.get("active_wait_seconds"),
        })
        .collect())
}

pub async fn find_by_document(db: &SqlitePool, document_id: &str) -> Result<Option<(String, String, String)>> {
    let row = sqlx::query("SELECT run_id, invoice_id, status FROM run_documents WHERE document_id = ?")
        .bind(document_id)
        .fetch_optional(db)
        .await?;
    Ok(row.map(|r| (r.get("run_id"), r.get("invoice_id"), r.get("status"))))
}

pub async fn add_wait(db: &SqlitePool, seconds: f64) -> Result<()> {
    sqlx::query("UPDATE run_documents SET active_wait_seconds = active_wait_seconds + ? WHERE status = 'uploaded'")
        .bind(seconds)
        .execute(db)
        .await?;
    Ok(())
}

pub async fn mark_timeout(db: &SqlitePool, run_id: &str, invoice_id: &str, reason: &str) -> Result<()> {
    sqlx::query("UPDATE run_documents SET status = 'timeout', timeout_reason = ?, finished_at = ? WHERE run_id = ? AND invoice_id = ? AND status = 'uploaded'")
        .bind(reason)
        .bind(now())
        .bind(run_id)
        .bind(invoice_id)
        .execute(db)
        .await?;
    Ok(())
}

/// Stores a comparison and the extracted copy; the document becomes passed or failed.
pub async fn store_comparison(
    db: &SqlitePool,
    t: &Tracked,
    extracted: &Invoice,
    model: Option<String>,
    prompt: Option<String>,
    c: &Comparison,
) -> Result<()> {
    let mut tx = db.begin().await?;
    for f in &c.fields {
        sqlx::query("INSERT INTO run_field_results (run_id, invoice_id, position, field, outcome, expected, actual) VALUES (?, ?, ?, ?, ?, ?, ?)")
            .bind(&t.run_id)
            .bind(&t.invoice_id)
            .bind(f.position)
            .bind(f.field)
            .bind(f.outcome.as_str())
            .bind(&f.expected)
            .bind(&f.actual)
            .execute(&mut *tx)
            .await?;
    }
    for l in &c.lines {
        sqlx::query("INSERT INTO run_line_results (run_id, invoice_id, position, golden_line_no, extracted_line_no, outcome) VALUES (?, ?, ?, ?, ?, ?)")
            .bind(&t.run_id)
            .bind(&t.invoice_id)
            .bind(l.position)
            .bind(l.golden_line_no)
            .bind(l.extracted_line_no)
            .bind(l.outcome.as_str())
            .execute(&mut *tx)
            .await?;
    }
    finish_document(&mut tx, t, extracted, model, prompt, c.passed).await?;
    tx.commit().await?;
    Ok(())
}

/// Stores a validation (documents runs) and the extracted copy; the document becomes passed or failed.
pub async fn store_validation(
    db: &SqlitePool,
    t: &Tracked,
    extracted: &Invoice,
    model: Option<String>,
    prompt: Option<String>,
    v: &Validation,
) -> Result<()> {
    let mut tx = db.begin().await?;
    for f in &v.fields {
        sqlx::query("INSERT INTO run_field_results (run_id, invoice_id, position, field, outcome, expected, actual) VALUES (?, ?, ?, ?, ?, NULL, ?)")
            .bind(&t.run_id)
            .bind(&t.invoice_id)
            .bind(f.position)
            .bind(&f.field)
            .bind(f.outcome)
            .bind(&f.actual)
            .execute(&mut *tx)
            .await?;
    }
    for c in &v.checks {
        sqlx::query("INSERT INTO run_check_results (run_id, invoice_id, \"check\", position, outcome, expected, actual) VALUES (?, ?, ?, ?, ?, ?, ?)")
            .bind(&t.run_id)
            .bind(&t.invoice_id)
            .bind(c.check)
            .bind(c.position)
            .bind(c.outcome)
            .bind(&c.expected)
            .bind(&c.actual)
            .execute(&mut *tx)
            .await?;
    }
    finish_document(&mut tx, t, extracted, model, prompt, v.passed).await?;
    tx.commit().await?;
    Ok(())
}

async fn finish_document(
    tx: &mut sqlx::Transaction<'_, sqlx::Sqlite>,
    t: &Tracked,
    extracted: &Invoice,
    model: Option<String>,
    prompt: Option<String>,
    passed: bool,
) -> Result<()> {
    sqlx::query("UPDATE run_documents SET status = ?, finished_at = ?, extracted_json = ?, extraction_model = ?, prompt_version = ? WHERE run_id = ? AND invoice_id = ? AND status = 'uploaded'")
        .bind(if passed { "passed" } else { "failed" })
        .bind(now())
        .bind(serde_json::to_string(extracted)?)
        .bind(model)
        .bind(prompt)
        .bind(&t.run_id)
        .bind(&t.invoice_id)
        .execute(&mut **tx)
        .await?;
    Ok(())
}

pub async fn unfinished(db: &SqlitePool, run_id: &str) -> Result<(i64, i64)> {
    let row = sqlx::query("SELECT sum(status = 'pending') AS pending, sum(status = 'uploaded') AS uploaded FROM run_documents WHERE run_id = ?")
        .bind(run_id)
        .fetch_one(db)
        .await?;
    Ok((row.get::<Option<i64>, _>("pending").unwrap_or(0), row.get::<Option<i64>, _>("uploaded").unwrap_or(0)))
}

#[derive(Debug, Clone, Serialize)]
pub struct DocumentRow {
    pub invoice_id: String,
    pub document_id: Option<String>,
    pub correlation_id: Option<String>,
    pub status: String,
    pub uploaded_at: Option<String>,
    pub finished_at: Option<String>,
    pub error_stage: Option<String>,
    pub error_code: Option<String>,
    pub error_message: Option<String>,
    pub timeout_reason: Option<String>,
    pub extraction_model: Option<String>,
    pub prompt_version: Option<String>,
}

pub async fn documents(db: &SqlitePool, run_id: &str) -> Result<Vec<DocumentRow>> {
    let rows = sqlx::query(
        "SELECT invoice_id, document_id, correlation_id, status, uploaded_at, finished_at, error_stage, error_code, error_message, timeout_reason, extraction_model, prompt_version FROM run_documents WHERE run_id = ? ORDER BY invoice_id",
    )
    .bind(run_id)
    .fetch_all(db)
    .await?;
    Ok(rows
        .iter()
        .map(|r| DocumentRow {
            invoice_id: r.get("invoice_id"),
            document_id: r.get("document_id"),
            correlation_id: r.get("correlation_id"),
            status: r.get("status"),
            uploaded_at: r.get("uploaded_at"),
            finished_at: r.get("finished_at"),
            error_stage: r.get("error_stage"),
            error_code: r.get("error_code"),
            error_message: r.get("error_message"),
            timeout_reason: r.get("timeout_reason"),
            extraction_model: r.get("extraction_model"),
            prompt_version: r.get("prompt_version"),
        })
        .collect())
}

/// Extracted currency per invoice_id of a run (documents runs have no golden currency).
pub async fn extracted_currencies(db: &SqlitePool, run_id: &str) -> Result<std::collections::HashMap<String, String>> {
    let rows = sqlx::query("SELECT invoice_id, json_extract(extracted_json, '$.currency') AS currency FROM run_documents WHERE run_id = ?")
        .bind(run_id)
        .fetch_all(db)
        .await?;
    Ok(rows.iter().filter_map(|r| Some((r.get("invoice_id"), r.get::<Option<String>, _>("currency")?))).collect())
}

#[derive(Debug, Clone, Serialize)]
pub struct FieldRow {
    pub position: Option<i64>,
    pub field: String,
    pub outcome: String,
    pub expected: Option<String>,
    pub actual: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct LineRow {
    pub position: i64,
    pub golden_line_no: Option<i64>,
    pub extracted_line_no: Option<i64>,
    pub outcome: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct CheckRow {
    pub check: String,
    pub position: Option<i64>,
    pub outcome: String,
    pub expected: Option<String>,
    pub actual: Option<String>,
}

pub struct DocumentResults {
    pub fields: Vec<FieldRow>,
    pub lines: Vec<LineRow>,
    pub checks: Vec<CheckRow>,
    pub extracted: Option<String>,
}

pub async fn document_results(db: &SqlitePool, run_id: &str, invoice_id: &str) -> Result<DocumentResults> {
    let fields = sqlx::query("SELECT position, field, outcome, expected, actual FROM run_field_results WHERE run_id = ? AND invoice_id = ? ORDER BY position IS NOT NULL, position, rowid")
        .bind(run_id)
        .bind(invoice_id)
        .fetch_all(db)
        .await?
        .iter()
        .map(|r| FieldRow { position: r.get("position"), field: r.get("field"), outcome: r.get("outcome"), expected: r.get("expected"), actual: r.get("actual") })
        .collect();
    let lines = sqlx::query("SELECT position, golden_line_no, extracted_line_no, outcome FROM run_line_results WHERE run_id = ? AND invoice_id = ? ORDER BY position")
        .bind(run_id)
        .bind(invoice_id)
        .fetch_all(db)
        .await?
        .iter()
        .map(|r| LineRow { position: r.get("position"), golden_line_no: r.get("golden_line_no"), extracted_line_no: r.get("extracted_line_no"), outcome: r.get("outcome") })
        .collect();
    let extracted: Option<String> = sqlx::query_scalar("SELECT extracted_json FROM run_documents WHERE run_id = ? AND invoice_id = ?")
        .bind(run_id)
        .bind(invoice_id)
        .fetch_optional(db)
        .await?
        .flatten();
    let checks = sqlx::query("SELECT \"check\", position, outcome, expected, actual FROM run_check_results WHERE run_id = ? AND invoice_id = ? ORDER BY position IS NOT NULL, position, rowid")
        .bind(run_id)
        .bind(invoice_id)
        .fetch_all(db)
        .await?
        .iter()
        .map(|r| CheckRow { check: r.get("check"), position: r.get("position"), outcome: r.get("outcome"), expected: r.get("expected"), actual: r.get("actual") })
        .collect();
    Ok(DocumentResults { fields, lines, checks, extracted })
}
