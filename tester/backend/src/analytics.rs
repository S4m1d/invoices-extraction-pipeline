//! Run summary (tester/backend/specs/overview.md, Analytics), computed with DuckDB over the run's
//! results joined with the golden metadata.

use crate::{app::App, store};
use anyhow::Result;
use duckdb::{Connection, params};
use serde_json::{Value, json};
use sqlx::Row;
use std::sync::Arc;

pub async fn summary(app: &Arc<App>, run: &store::Run) -> Result<Value> {
    let golden = app.golden(&run.dataset).await?;
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

    let wall = sqlx::query_scalar::<_, Option<f64>>(
        "SELECT (julianday(coalesce(finished_at, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))) - julianday(started_at)) * 86400.0 FROM runs WHERE id = ?",
    )
    .bind(&run.id)
    .fetch_one(&app.db)
    .await?;

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
    Ok(json!({
        "documents": rows("SELECT count(*) AS total, count_if(status = 'passed') AS passed, count_if(status = 'failed') AS failed,
                                  count_if(status = 'error') AS error, count_if(status = 'timeout') AS timeout,
                                  count_if(status IN ('pending', 'uploaded')) AS unfinished FROM docs")?.pop(),
        "overlapped": run.overlapped,
        "fields": rows(&format!("SELECT field, bool_or(is_line) AS line_field, {field_stats} FROM fields GROUP BY field ORDER BY bool_or(is_line), field"))?,
        "lines": rows("SELECT count_if(outcome = 'compared') AS compared, count_if(outcome = 'missing') AS missing, count_if(outcome = 'extra') AS extra FROM lines")?.pop(),
        "by_template": breakdown("template_id")?,
        "by_currency": breakdown("currency")?,
        "by_language": breakdown("language")?,
        "errors": rows("SELECT error_stage AS stage, error_code AS code, count(*) AS documents FROM docs WHERE status = 'error' GROUP BY ALL ORDER BY documents DESC")?,
        "models": rows("SELECT model, prompt, count(*) AS documents FROM docs WHERE model IS NOT NULL GROUP BY ALL ORDER BY documents DESC")?,
        "durations": rows("SELECT median(seconds) AS median_s, quantile_cont(seconds, 0.95) AS p95_s, max(seconds) AS max_s FROM docs WHERE seconds IS NOT NULL")?.pop(),
        "wall_clock_s": wall,
    }))
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
}
