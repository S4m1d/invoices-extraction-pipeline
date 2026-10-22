//! Golden set access through DuckDB (tester/backend/specs/overview.md, Golden set access): the gzip
//! JSON Lines shards are read directly, nothing is imported.

use crate::compare::{Invoice, Line};
use anyhow::{Context, Result};
use duckdb::Connection;
use std::{collections::HashMap, path::Path};

#[derive(Debug, Clone)]
pub struct Golden {
    pub invoice: Invoice,
    pub template_id: String,
    pub currency: String,
    pub language: String,
    /// False for telecom invoices, which print no unit price (specs/comparison.md, Lines).
    pub unit_price_printed: bool,
}

/// Golden records of a dataset by invoice_id, with top-level lines only (parent_line_no null) in
/// printed order (specs/comparison.md, Lines).
pub fn load(root: &Path, dataset: &str) -> Result<HashMap<String, Golden>> {
    let dir = root.join(dataset).join("golden");
    let invoices = glob(&dir.join("invoices"))?;
    let lines = glob(&dir.join("line_items"))?;
    let conn = Connection::open_in_memory()?;
    let value = |path: &str| {
        format!("coalesce(json_extract_string(json, '$.{path}.__decimal__'), json_extract_string(json, '$.{path}.__date__'), json_extract_string(json, '$.{path}'))")
    };
    let sql = format!(
        "SELECT json_extract_string(json, '$.invoice_id'), {}, {}, {}, {}, {}, {}, {}, {}, {},
                coalesce(json_extract_string(json, '$.template_id'), ''), coalesce(json_extract_string(json, '$.language'), ''),
                coalesce(json_extract_string(json, '$.business_type'), '')
         FROM read_ndjson_objects({invoices}, compression = 'gzip')",
        value("issuer_name"), value("recipient_name"), value("invoice_number"), value("issue_date"),
        value("due_date"), value("currency"), value("subtotal"), value("tax_total"), value("grand_total"),
    );
    let mut out: HashMap<String, Golden> = HashMap::new();
    let mut stmt = conn.prepare(&sql).context("query golden invoices")?;
    let rows = stmt.query_map([], |r| {
        let s = |i: usize| r.get::<_, Option<String>>(i);
        Ok((
            s(0)?.unwrap_or_default(),
            Invoice {
                supplier_name: s(1)?,
                recipient_name: s(2)?,
                invoice_number: s(3)?,
                issue_date: s(4)?,
                due_date: s(5)?,
                currency: s(6)?,
                subtotal: s(7)?,
                tax_total: s(8)?,
                grand_total: s(9)?,
                tax_rate: None,
                lines: vec![],
            },
            s(10)?.unwrap_or_default(),
            s(11)?.unwrap_or_default(),
            s(12)?.unwrap_or_default(),
        ))
    })?;
    for row in rows {
        let (id, invoice, template_id, language, business_type) = row?;
        let currency = invoice.currency.clone().unwrap_or_default();
        let unit_price_printed = business_type != "telecom";
        out.insert(id, Golden { invoice, template_id, currency, language, unit_price_printed });
    }

    // Printed order: grouped by group_key, then section, each group placed at its first line_no.
    let sql = format!(
        "SELECT invoice_id, line_no, description, quantity, unit_price, amount FROM (
             SELECT json_extract_string(json, '$.invoice_id') AS invoice_id,
                    CAST(json_extract(json, '$.line_no') AS BIGINT) AS line_no,
                    {} AS description, {} AS quantity, {} AS unit_price, {} AS amount,
                    json_extract_string(json, '$.group_key') AS group_key,
                    json_extract_string(json, '$.section') AS section
             FROM read_ndjson_objects({lines}, compression = 'gzip')
             WHERE json_extract(json, '$.parent_line_no') IS NULL OR json_type(json, '$.parent_line_no') = 'NULL'
         )
         ORDER BY invoice_id,
                  min(line_no) OVER (PARTITION BY invoice_id, group_key),
                  min(line_no) OVER (PARTITION BY invoice_id, group_key, section),
                  line_no",
        value("description"), value("quantity"), value("unit_price"), value("extended_amount"),
    );
    let mut stmt = conn.prepare(&sql).context("query golden lines")?;
    let rows = stmt.query_map([], |r| {
        Ok((
            r.get::<_, Option<String>>(0)?.unwrap_or_default(),
            Line {
                line_no: r.get(1)?,
                description: r.get(2)?,
                quantity: r.get(3)?,
                unit_price: r.get(4)?,
                amount: r.get(5)?,
            },
        ))
    })?;
    for row in rows {
        let (id, line) = row?;
        if let Some(g) = out.get_mut(&id) {
            g.invoice.lines.push(line);
        }
    }
    Ok(out)
}

/// A DuckDB list literal of all unit shards in dir (`['a.jsonl.gz', 'b.jsonl.gz']`).
fn glob(dir: &Path) -> Result<String> {
    let mut files: Vec<String> = std::fs::read_dir(dir)
        .with_context(|| format!("read {}", dir.display()))?
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| p.to_string_lossy().ends_with(".jsonl.gz"))
        .map(|p| p.to_string_lossy().replace('\'', "''"))
        .collect();
    files.sort();
    anyhow::ensure!(!files.is_empty(), "no shards in {}", dir.display());
    Ok(format!("[{}]", files.iter().map(|f| format!("'{f}'")).collect::<Vec<_>>().join(", ")))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Writes rows (JSON objects) as one gzip JSON Lines shard, the dataset format.
    fn shard(dir: &Path, table: &str, rows: &[&str]) {
        let d = dir.join("run-x").join("golden").join(table);
        std::fs::create_dir_all(&d).unwrap();
        let src = d.join("rows.json");
        std::fs::write(&src, rows.join("\n")).unwrap();
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(&format!(
            "COPY (SELECT json FROM read_ndjson_objects('{}')) TO '{}' (FORMAT csv, HEADER false, QUOTE '', COMPRESSION gzip)",
            src.display(),
            d.join("unit-000000.jsonl.gz").display()
        ))
        .unwrap();
        std::fs::remove_file(src).unwrap();
    }

    #[test]
    fn lines_in_printed_order_and_telecom_flag() {
        let dir = std::env::temp_dir().join(format!("tester-golden-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        shard(&dir, "invoices", &[
            r#"{"invoice_id": "t", "business_type": "telecom", "template_id": "telecom-itemized-37", "currency": "USD"}"#,
            r#"{"invoice_id": "r", "business_type": "retail", "template_id": "x", "currency": "USD"}"#,
        ]);
        // t: generation order interleaves two phones and two sections; r: no grouping.
        let line = |inv: &str, no: i64, group: &str, section: &str| {
            let q = |s: &str| if s.is_empty() { "null".to_string() } else { format!("\"{s}\"") };
            format!(r#"{{"invoice_id": "{inv}", "line_no": {no}, "parent_line_no": null, "description": "d{no}", "quantity": {{"__decimal__": "1"}}, "unit_price": {{"__decimal__": "1.0000"}}, "extended_amount": {{"__decimal__": "1.00"}}, "group_key": {}, "section": {}}}"#, q(group), q(section))
        };
        let rows = [
            line("t", 1, "A", "Messaging"), line("t", 2, "A", "Voice"), line("t", 3, "B", "Voice"),
            line("t", 4, "A", "Messaging"), line("t", 5, "B", "Data"), line("t", 6, "A", "Voice"),
            line("r", 2, "", ""), line("r", 1, "", ""),
        ];
        shard(&dir, "line_items", &rows.iter().map(String::as_str).collect::<Vec<_>>());

        let golden = load(&dir, "run-x").unwrap();
        let order = |id: &str| golden[id].invoice.lines.iter().map(|l| l.line_no).collect::<Vec<_>>();
        assert_eq!(order("t"), vec![1, 4, 2, 6, 3, 5]); // A: Messaging, Voice; then B: Voice, Data
        assert_eq!(order("r"), vec![1, 2]);
        assert!(!golden["t"].unit_price_printed);
        assert!(golden["r"].unit_price_printed);
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
