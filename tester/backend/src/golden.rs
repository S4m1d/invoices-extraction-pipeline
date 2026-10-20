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
}

/// Golden records of a dataset by invoice_id, with top-level lines only (parent_line_no null).
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
                coalesce(json_extract_string(json, '$.template_id'), ''), coalesce(json_extract_string(json, '$.language'), '')
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
                lines: vec![],
            },
            s(10)?.unwrap_or_default(),
            s(11)?.unwrap_or_default(),
        ))
    })?;
    for row in rows {
        let (id, invoice, template_id, language) = row?;
        let currency = invoice.currency.clone().unwrap_or_default();
        out.insert(id, Golden { invoice, template_id, currency, language });
    }

    let sql = format!(
        "SELECT json_extract_string(json, '$.invoice_id'), CAST(json_extract(json, '$.line_no') AS BIGINT), {}, {}, {}, {}
         FROM read_ndjson_objects({lines}, compression = 'gzip')
         WHERE json_extract(json, '$.parent_line_no') IS NULL OR json_type(json, '$.parent_line_no') = 'NULL'
         ORDER BY 1, 2",
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
