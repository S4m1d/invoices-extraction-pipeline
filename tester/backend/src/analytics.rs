//! Run summary (tester/backend/specs/overview.md, Analytics; documents runs: validation.md, Summary),
//! computed with DuckDB over the run's results, joined with the golden metadata for synthetic runs.

use crate::{app::App, store};
use anyhow::Result;
use duckdb::{Connection, params};
use serde_json::{Value, json};
use sqlx::Row;
use std::sync::Arc;

/// Header-only document accuracy (comparison.md, Metrics): compared documents with every header field `match`.
const HEADER_DOCUMENTS: &str = "SELECT count(*) AS compared,
        count_if(NOT EXISTS (SELECT 1 FROM fields f WHERE f.invoice_id = d.invoice_id AND NOT f.is_line AND f.outcome <> 'match')) AS correct
    FROM docs d WHERE status IN ('passed', 'failed')";

pub async fn summary(app: &Arc<App>, run: &store::Run) -> Result<Value> {
    if run.kind == "documents" {
        return documents_summary(app, run).await;
    }
    let golden = app.golden(&run.source, &run.dataset).await?;
    let docs = sqlx::query(
        "SELECT invoice_id, status, error_stage, error_code, extraction_model, prompt_version,
                (julianday(finished_at) - julianday(uploaded_at)) * 86400.0 AS seconds
         FROM run_documents WHERE run_id = ?",
    )
    .bind(&run.id)
    .fetch_all(&app.db)
    .await?;
    let fields = sqlx::query("SELECT invoice_id, position IS NOT NULL AS is_line, field, outcome FROM run_field_results WHERE run_id = ?")
        .bind(&run.id)
        .fetch_all(&app.db)
        .await?;
    let lines = sqlx::query("SELECT outcome FROM run_line_results WHERE run_id = ?").bind(&run.id).fetch_all(&app.db).await?;

    let wall = wall_clock(app, &run.id).await?;

    let conn = Connection::open_in_memory()?;
    conn.execute_batch(
        "CREATE TABLE docs (invoice_id TEXT, status TEXT, error_stage TEXT, error_code TEXT, model TEXT, prompt TEXT,
                            seconds DOUBLE, template_id TEXT, currency TEXT, language TEXT);
         CREATE TABLE fields (invoice_id TEXT, is_line BOOLEAN, field TEXT, outcome TEXT);
         CREATE TABLE lines (outcome TEXT);",
    )?;
    {
        let mut a = conn.appender("docs")?;
        for d in &docs {
            let id: String = d.get("invoice_id");
            let g = golden.get(&id);
            a.append_row(params![
                id,
                d.get::<String, _>("status"),
                d.get::<Option<String>, _>("error_stage"),
                d.get::<Option<String>, _>("error_code"),
                d.get::<Option<String>, _>("extraction_model"),
                d.get::<Option<String>, _>("prompt_version"),
                d.get::<Option<f64>, _>("seconds"),
                g.map(|g| g.template_id.clone()),
                g.map(|g| g.currency.clone()),
                g.map(|g| g.language.clone()),
            ])?;
        }
        let mut a = conn.appender("fields")?;
        for f in &fields {
            a.append_row(params![f.get::<String, _>("invoice_id"), f.get::<bool, _>("is_line"), f.get::<String, _>("field"), f.get::<String, _>("outcome")])?;
        }
        let mut a = conn.appender("lines")?;
        for l in &lines {
            a.append_row(params![l.get::<String, _>("outcome")])?;
        }
    }

    let rows = |sql: &str| query_json(&conn, sql);
    let field_stats = "count_if(outcome = 'match') AS match, count_if(outcome = 'mismatch') AS mismatch,
                       count_if(outcome = 'missing') AS missing, count_if(outcome = 'unexpected') AS unexpected";
    let breakdown = |dim: &str| -> Result<Vec<Value>> {
        rows(&format!(
            "SELECT d.{dim} AS value, count(DISTINCT d.invoice_id) AS documents,
                    count_if(d.status = 'passed') AS passed,
                    (SELECT count(*) FROM fields f JOIN docs x USING (invoice_id) WHERE x.{dim} IS NOT DISTINCT FROM d.{dim} AND f.outcome = 'match') AS field_match,
                    (SELECT count(*) FROM fields f JOIN docs x USING (invoice_id) WHERE x.{dim} IS NOT DISTINCT FROM d.{dim}) AS field_total
             FROM docs d GROUP BY d.{dim} ORDER BY d.{dim}"
        ))
    };
    let mut out = common(&conn, run, wall)?;
    let mut fields = rows(&format!("SELECT field, bool_or(is_line) AS line_field, {field_stats} FROM fields GROUP BY field ORDER BY bool_or(is_line), field"))?;
    fields.iter_mut().for_each(with_scores);
    let group = |is_line: bool| -> Result<Value> {
        let mut v = rows(&format!("SELECT {field_stats} FROM fields WHERE is_line = {is_line}"))?.pop().unwrap_or_default();
        with_scores(&mut v);
        Ok(v)
    };
    let header_docs = rows(HEADER_DOCUMENTS)?
    .pop()
    .unwrap_or_default();
    let header_accuracy = ratio(header_docs["correct"].as_i64().unwrap_or(0), header_docs["compared"].as_i64().unwrap_or(0));
    out.insert("metrics".into(), json!({
        "header": group(false)?,
        "line": group(true)?,
        "header_documents": {"compared": header_docs["compared"], "correct": header_docs["correct"], "accuracy": header_accuracy},
    }));
    out.insert("fields".into(), json!(fields));
    out.insert("lines".into(), json!(rows("SELECT count_if(outcome = 'compared') AS compared, count_if(outcome = 'missing') AS missing, count_if(outcome = 'extra') AS extra FROM lines")?.pop()));
    out.insert("by_template".into(), json!(breakdown("template_id")?));
    out.insert("by_currency".into(), json!(breakdown("currency")?));
    out.insert("by_language".into(), json!(breakdown("language")?));
    Ok(Value::Object(out))
}

/// Adds accuracy, precision, recall and F1 to an object with match / mismatch / missing / unexpected
/// counts (comparison.md, Metrics). A score whose denominator is 0 is null.
fn with_scores(v: &mut Value) {
    let n = |k: &str| v[k].as_i64().unwrap_or(0);
    let (matched, mismatch, missing, unexpected) = (n("match"), n("mismatch"), n("missing"), n("unexpected"));
    let precision = ratio(matched, matched + mismatch + unexpected);
    let recall = ratio(matched, matched + mismatch + missing);
    let f1 = match (precision, recall) {
        (Some(p), Some(r)) if p + r > 0.0 => Some(2.0 * p * r / (p + r)),
        (Some(_), Some(_)) => Some(0.0),
        _ => None,
    };
    let Value::Object(m) = v else { return };
    m.insert("accuracy".into(), json!(ratio(matched, matched + mismatch + missing + unexpected)));
    m.insert("precision".into(), json!(precision));
    m.insert("recall".into(), json!(recall));
    m.insert("f1".into(), json!(f1));
}

fn ratio(num: i64, den: i64) -> Option<f64> {
    (den > 0).then(|| num as f64 / den as f64)
}

/// The run's cached summary. A synthetic summary from before the headline metrics is recomputed and
/// the cache replaced; if that fails, the old one is returned (overview.md, Analytics).
pub async fn cached(app: &Arc<App>, run: &store::Run) -> Result<Option<Value>> {
    let Some(old) = store::summary(&app.db, &run.id).await? else { return Ok(None) };
    if run.kind == "documents" || old.get("metrics").is_some() {
        return Ok(Some(old));
    }
    match summary(app, run).await {
        Ok(new) => {
            store::set_summary(&app.db, &run.id, &new).await?;
            tracing::info!(run_id = %run.id, "summary recomputed with headline metrics");
            Ok(Some(new))
        }
        Err(e) => {
            tracing::warn!(run_id = %run.id, error = %e, "cannot recompute summary, keeping the old one");
            Ok(Some(old))
        }
    }
}

/// Summary of a documents run: required fields, checks and the extracted currency instead of golden accuracy.
async fn documents_summary(app: &Arc<App>, run: &store::Run) -> Result<Value> {
    let docs = sqlx::query(
        "SELECT invoice_id, status, error_stage, error_code, extraction_model, prompt_version,
                (julianday(finished_at) - julianday(uploaded_at)) * 86400.0 AS seconds,
                json_extract(extracted_json, '$.currency') AS currency
         FROM run_documents WHERE run_id = ?",
    )
    .bind(&run.id)
    .fetch_all(&app.db)
    .await?;
    let fields = sqlx::query("SELECT position IS NOT NULL AS is_line, field, outcome FROM run_field_results WHERE run_id = ?")
        .bind(&run.id)
        .fetch_all(&app.db)
        .await?;
    let checks = sqlx::query("SELECT \"check\", outcome FROM run_check_results WHERE run_id = ?").bind(&run.id).fetch_all(&app.db).await?;
    let wall = wall_clock(app, &run.id).await?;

    let conn = Connection::open_in_memory()?;
    conn.execute_batch(
        "CREATE TABLE docs (invoice_id TEXT, status TEXT, error_stage TEXT, error_code TEXT, model TEXT, prompt TEXT,
                            seconds DOUBLE, currency TEXT);
         CREATE TABLE fields (is_line BOOLEAN, field TEXT, outcome TEXT);
         CREATE TABLE checks (name TEXT, outcome TEXT);",
    )?;
    {
        let mut a = conn.appender("docs")?;
        for d in &docs {
            a.append_row(params![
                d.get::<String, _>("invoice_id"),
                d.get::<String, _>("status"),
                d.get::<Option<String>, _>("error_stage"),
                d.get::<Option<String>, _>("error_code"),
                d.get::<Option<String>, _>("extraction_model"),
                d.get::<Option<String>, _>("prompt_version"),
                d.get::<Option<f64>, _>("seconds"),
                d.get::<Option<String>, _>("currency"),
            ])?;
        }
        let mut a = conn.appender("fields")?;
        for f in &fields {
            a.append_row(params![f.get::<bool, _>("is_line"), f.get::<String, _>("field"), f.get::<String, _>("outcome")])?;
        }
        let mut a = conn.appender("checks")?;
        for c in &checks {
            a.append_row(params![c.get::<String, _>("check"), c.get::<String, _>("outcome")])?;
        }
    }
    let rows = |sql: &str| query_json(&conn, sql);
    let mut out = common(&conn, run, wall)?;
    out.insert("required_fields".into(), json!(rows(
        "SELECT field, bool_or(is_line) AS line_field, count_if(outcome = 'present') AS present, count_if(outcome = 'missing') AS missing
         FROM fields GROUP BY field ORDER BY bool_or(is_line), field"
    )?));
    out.insert("checks".into(), json!(rows(
        "SELECT name AS \"check\", count_if(outcome = 'pass') AS pass, count_if(outcome = 'fail') AS fail,
                count_if(outcome = 'not_applicable') AS not_applicable
         FROM checks GROUP BY name ORDER BY name"
    )?));
    out.insert("by_currency".into(), json!(rows(
        "SELECT currency AS value, count(*) AS documents, count_if(status = 'passed') AS passed FROM docs GROUP BY currency ORDER BY currency"
    )?));
    Ok(Value::Object(out))
}

async fn wall_clock(app: &Arc<App>, run_id: &str) -> Result<Option<f64>> {
    Ok(sqlx::query_scalar::<_, Option<f64>>(
        "SELECT (julianday(coalesce(finished_at, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))) - julianday(started_at)) * 86400.0 FROM runs WHERE id = ?",
    )
    .bind(run_id)
    .fetch_one(&app.db)
    .await?)
}

/// Parts of the summary shared by both kinds, from the `docs` table.
fn common(conn: &Connection, run: &store::Run, wall: Option<f64>) -> Result<serde_json::Map<String, Value>> {
    let rows = |sql: &str| query_json(conn, sql);
    let v = json!({
        "kind": run.kind,
        "documents": rows("SELECT count(*) AS total, count_if(status = 'passed') AS passed, count_if(status = 'failed') AS failed,
                                  count_if(status = 'error') AS error, count_if(status = 'cancelled') AS cancelled,
                                  count_if(status = 'timeout') AS timeout,
                                  count_if(status IN ('pending', 'uploaded')) AS unfinished FROM docs")?.pop(),
        "overlapped": run.overlapped,
        "errors": rows("SELECT error_stage AS stage, error_code AS code, count(*) AS documents FROM docs WHERE status = 'error' GROUP BY ALL ORDER BY documents DESC")?,
        "models": rows("SELECT model, prompt, count(*) AS documents FROM docs WHERE model IS NOT NULL GROUP BY ALL ORDER BY documents DESC")?,
        "durations": rows("SELECT median(seconds) AS median_s, quantile_cont(seconds, 0.95) AS p95_s, max(seconds) AS max_s FROM docs WHERE seconds IS NOT NULL")?.pop(),
        "wall_clock_s": wall,
    });
    let Value::Object(m) = v else { unreachable!() };
    Ok(m)
}

/// Runs sql and returns each row as a JSON object keyed by column name. Column names are read after
/// the query runs: duckdb panics when a prepared statement's columns are read before execution.
fn query_json(conn: &Connection, sql: &str) -> Result<Vec<Value>> {
    let mut stmt = conn.prepare(sql)?;
    let mut rows = stmt.query([])?;
    let names = rows.as_ref().map(|s| s.column_names()).unwrap_or_default();
    let mut out = Vec::new();
    while let Some(r) = rows.next()? {
        let mut m = serde_json::Map::new();
        for (i, n) in names.iter().enumerate() {
            let v: duckdb::types::Value = r.get(i)?;
            m.insert(n.clone(), to_json(v));
        }
        out.push(Value::Object(m));
    }
    Ok(out)
}

fn to_json(v: duckdb::types::Value) -> Value {
    use duckdb::types::Value as V;
    match v {
        V::Null => Value::Null,
        V::Boolean(b) => b.into(),
        V::TinyInt(i) => i.into(),
        V::SmallInt(i) => i.into(),
        V::Int(i) => i.into(),
        V::BigInt(i) => i.into(),
        V::HugeInt(i) => (i as i64).into(),
        V::UTinyInt(i) => i.into(),
        V::USmallInt(i) => i.into(),
        V::UInt(i) => i.into(),
        V::UBigInt(i) => i.into(),
        V::Float(f) => f.into(),
        V::Double(f) => f.into(),
        V::Text(s) => s.into(),
        other => format!("{other:?}").into(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn query_json_keys_rows_by_column_name() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch("CREATE TABLE t (name VARCHAR, n INTEGER); INSERT INTO t VALUES ('a', 1), ('b', 2)").unwrap();
        let rows = query_json(&conn, "SELECT name, n * 10 AS tens FROM t ORDER BY name").unwrap();
        assert_eq!(rows, vec![json!({"name": "a", "tens": 10}), json!({"name": "b", "tens": 20})]);
        assert_eq!(query_json(&conn, "SELECT * FROM t WHERE false").unwrap(), Vec::<Value>::new());
    }

    #[test]
    fn scores_follow_comparison_metrics() {
        let mut v = json!({"match": 8, "mismatch": 1, "missing": 1, "unexpected": 0});
        with_scores(&mut v);
        assert_eq!(v["accuracy"], json!(0.8));
        assert_eq!(v["precision"], json!(8.0 / 9.0));
        assert_eq!(v["recall"], json!(0.8));
        let f1 = v["f1"].as_f64().unwrap();
        assert!((f1 - 2.0 * (8.0 / 9.0) * 0.8 / (8.0 / 9.0 + 0.8)).abs() < 1e-12);

        let mut only_missing = json!({"match": 0, "mismatch": 0, "missing": 3, "unexpected": 0});
        with_scores(&mut only_missing);
        assert_eq!(only_missing["precision"], Value::Null); // nothing extracted
        assert_eq!(only_missing["recall"], json!(0.0));
        assert_eq!(only_missing["f1"], Value::Null);

        let mut all_wrong = json!({"match": 0, "mismatch": 2, "missing": 0, "unexpected": 0});
        with_scores(&mut all_wrong);
        assert_eq!(all_wrong["f1"], json!(0.0));

        let mut empty = json!({"match": 0, "mismatch": 0, "missing": 0, "unexpected": 0});
        with_scores(&mut empty);
        assert_eq!(empty["accuracy"], Value::Null);
    }

    #[test]
    fn header_document_accuracy_ignores_lines_and_uncompared_documents() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE docs (invoice_id TEXT, status TEXT);
             CREATE TABLE fields (invoice_id TEXT, is_line BOOLEAN, field TEXT, outcome TEXT);
             INSERT INTO docs VALUES ('a', 'passed'), ('b', 'failed'), ('c', 'failed'), ('d', 'error');
             INSERT INTO fields VALUES ('a', false, 'invoice_number', 'match'),
                                       ('b', false, 'invoice_number', 'match'), ('b', true, 'amount', 'mismatch'),
                                       ('c', false, 'due_date', 'missing');",
        )
        .unwrap();
        let rows = query_json(&conn, HEADER_DOCUMENTS).unwrap();
        assert_eq!(rows, vec![json!({"compared": 3, "correct": 2})]);
    }
}
