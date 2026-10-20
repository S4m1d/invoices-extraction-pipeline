//! Dataset discovery (tester/backend/specs/dataset-format.md).

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Serialize)]
pub struct Dataset {
    pub name: String,
    pub total_documents: i64,
    pub total_units: i64,
    pub created_at: Option<String>,
}

#[derive(Debug, Clone)]
pub struct Document {
    pub invoice_id: String,
    pub path: PathBuf,
}

#[derive(Deserialize)]
struct Manifest {
    schema_version: i64,
    total_documents: i64,
    total_units: i64,
    created_at: Option<String>,
    parts: Vec<Part>,
}

#[derive(Deserialize)]
struct Part {
    key: String,
}

#[derive(Deserialize)]
struct UnitManifest {
    documents: Vec<UnitDocument>,
}

#[derive(Deserialize)]
struct UnitDocument {
    key: String,
}

/// Every immediate subdirectory of the root with a readable manifest.json, sorted by name.
pub fn list(root: &Path) -> Result<Vec<Dataset>> {
    let mut out = Vec::new();
    for entry in std::fs::read_dir(root).with_context(|| format!("read datasets root {}", root.display()))? {
        let entry = entry?;
        if !entry.path().is_dir() {
            continue;
        }
        let name = entry.file_name().to_string_lossy().to_string();
        match manifest(root, &name) {
            Ok(m) => out.push(Dataset { name, total_documents: m.total_documents, total_units: m.total_units, created_at: m.created_at }),
            Err(e) => tracing::warn!(dataset = %name, error = format!("{e:#}"), "skipping dataset without a usable manifest"),
        }
    }
    out.sort_by(|a, b| a.name.cmp(&b.name));
    Ok(out)
}

fn manifest(root: &Path, name: &str) -> Result<Manifest> {
    let path = root.join(name).join("manifest.json");
    let m: Manifest = serde_json::from_slice(&std::fs::read(&path).with_context(|| format!("read {}", path.display()))?)?;
    anyhow::ensure!(m.schema_version == 1, "{}: schema_version {}, want 1", path.display(), m.schema_version);
    Ok(m)
}

/// All documents of a dataset (all units), in manifest order. Keys are relative to the datasets root.
pub fn documents(root: &Path, name: &str) -> Result<Vec<Document>> {
    let m = manifest(root, name)?;
    let mut out = Vec::new();
    for part in m.parts {
        let unit: UnitManifest = serde_json::from_slice(&std::fs::read(root.join(&part.key))?)?;
        for d in unit.documents {
            let path = root.join(&d.key);
            let invoice_id = path.file_stem().context("document without file name")?.to_string_lossy().to_string();
            out.push(Document { invoice_id, path });
        }
    }
    Ok(out)
}

/// Path of the dataset's document file for invoice_id (for the original.pdf artifact).
pub fn document_path(root: &Path, name: &str, invoice_id: &str) -> Result<PathBuf> {
    documents(root, name)?
        .into_iter()
        .find(|d| d.invoice_id == invoice_id)
        .map(|d| d.path)
        .with_context(|| format!("{invoice_id} not in dataset {name}"))
}

/// Rejects names that could escape the datasets root.
pub fn valid_name(name: &str) -> bool {
    !name.is_empty() && !name.contains('/') && !name.contains('\\') && name != "." && name != ".."
}
