//! Dataset discovery in a data source (tester/backend/specs/data-sources.md, Datasets in a source;
//! synthetic layout: dataset-format.md).

use crate::sources::{Kind, Source};
use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::{
    collections::HashMap,
    path::{Path, PathBuf},
};

#[derive(Debug, Clone, Serialize)]
pub struct Dataset {
    pub source: String,
    pub kind: Kind,
    pub name: String,
    pub total_documents: i64,
    /// Synthetic datasets only.
    pub total_units: Option<i64>,
    pub created_at: Option<String>,
    /// Set when the dataset cannot be run.
    pub error: Option<String>,
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

/// Datasets of a source, sorted by name.
pub fn list(source: &Source) -> Result<Vec<Dataset>> {
    match source.kind {
        Kind::Synthetic => list_synthetic(source),
        Kind::Documents => list_documents(source),
    }
}

/// All documents of a dataset, in manifest order (synthetic) or by file name (documents).
pub fn documents(source: &Source, name: &str) -> Result<Vec<Document>> {
    match source.kind {
        Kind::Synthetic => synthetic_documents(&source.path, name),
        Kind::Documents => {
            let (docs, duplicates) = pdfs(&documents_dir(source, name)?)?;
            anyhow::ensure!(duplicates.is_empty(), "file names differ only in the extension's case: {}", duplicates.join(", "));
            Ok(docs)
        }
    }
}

/// Path of the dataset's document file for invoice_id (for the original.pdf artifact).
pub fn document_path(source: &Source, name: &str, invoice_id: &str) -> Result<PathBuf> {
    documents(source, name)?
        .into_iter()
        .find(|d| d.invoice_id == invoice_id)
        .map(|d| d.path)
        .with_context(|| format!("{invoice_id} not in dataset {name}"))
}

fn hidden(name: &str) -> bool {
    name.starts_with('.')
}

/// Synthetic: every immediate subdirectory with a readable manifest.json.
fn list_synthetic(source: &Source) -> Result<Vec<Dataset>> {
    let root = &source.path;
    let mut out = Vec::new();
    for entry in std::fs::read_dir(root).with_context(|| format!("read source {}", root.display()))? {
        let entry = entry?;
        let name = entry.file_name().to_string_lossy().to_string();
        if hidden(&name) || !entry.path().is_dir() {
            continue;
        }
        match manifest(root, &name) {
            Ok(m) => out.push(Dataset {
                source: source.name.clone(),
                kind: Kind::Synthetic,
                name,
                total_documents: m.total_documents,
                total_units: Some(m.total_units),
                created_at: m.created_at,
                error: None,
            }),
            Err(e) => tracing::warn!(source = %source.name, dataset = %name, error = format!("{e:#}"), "skipping dataset without a usable manifest"),
        }
    }
    out.sort_by(|a, b| a.name.cmp(&b.name));
    Ok(out)
}

/// Documents: every subdirectory with at least one PDF, plus the PDFs in the source directory itself
/// as a dataset named after the source (a subdirectory of that name wins).
fn list_documents(source: &Source) -> Result<Vec<Dataset>> {
    let root = &source.path;
    let mut out = Vec::new();
    let dataset = |name: String, docs: Vec<Document>, duplicates: Vec<String>| Dataset {
        source: source.name.clone(),
        kind: Kind::Documents,
        name,
        total_documents: docs.len() as i64,
        total_units: None,
        created_at: None,
        error: (!duplicates.is_empty()).then(|| format!("file names differ only in the extension's case: {}", duplicates.join(", "))),
    };
    for entry in std::fs::read_dir(root).with_context(|| format!("read source {}", root.display()))? {
        let entry = entry?;
        let name = entry.file_name().to_string_lossy().to_string();
        if hidden(&name) || !entry.path().is_dir() {
            continue;
        }
        let (docs, duplicates) = pdfs(&entry.path())?;
        if !docs.is_empty() {
            out.push(dataset(name, docs, duplicates));
        }
    }
    let (docs, duplicates) = pdfs(root)?;
    if !docs.is_empty() {
        if out.iter().any(|d| d.name == source.name) {
            tracing::warn!(source = %source.name, "PDFs in the source directory are not listed: a subdirectory has the source's name");
        } else {
            out.push(dataset(source.name.clone(), docs, duplicates));
        }
    }
    out.sort_by(|a, b| a.name.cmp(&b.name));
    Ok(out)
}

fn documents_dir(source: &Source, name: &str) -> Result<PathBuf> {
    let sub = source.path.join(name);
    if sub.is_dir() {
        Ok(sub)
    } else if name == source.name {
        Ok(source.path.clone())
    } else {
        anyhow::bail!("dataset {name} not found in source {}", source.name)
    }
}

/// PDFs directly in dir (by name), and the invoice ids whose file names differ only in the extension's case.
fn pdfs(dir: &Path) -> Result<(Vec<Document>, Vec<String>)> {
    let mut by_id: HashMap<String, Vec<PathBuf>> = HashMap::new();
    for entry in std::fs::read_dir(dir).with_context(|| format!("read {}", dir.display()))? {
        let path = entry?.path();
        let name = path.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default();
        let Some(stem) = name.len().checked_sub(4).filter(|&i| name.is_char_boundary(i) && name[i..].eq_ignore_ascii_case(".pdf")).map(|i| &name[..i]) else {
            continue;
        };
        if hidden(&name) || stem.is_empty() || !path.is_file() {
            continue; // is_file follows symlinks
        }
        by_id.entry(stem.to_string()).or_default().push(path);
    }
    let mut docs = Vec::new();
    let mut duplicates = Vec::new();
    for (invoice_id, mut paths) in by_id {
        if paths.len() > 1 {
            duplicates.push(invoice_id.clone());
        }
        paths.sort();
        docs.push(Document { invoice_id, path: paths.swap_remove(0) });
    }
    docs.sort_by(|a, b| a.invoice_id.cmp(&b.invoice_id));
    duplicates.sort();
    Ok((docs, duplicates))
}

fn manifest(root: &Path, name: &str) -> Result<Manifest> {
    let path = root.join(name).join("manifest.json");
    let m: Manifest = serde_json::from_slice(&std::fs::read(&path).with_context(|| format!("read {}", path.display()))?)?;
    anyhow::ensure!(m.schema_version == 1, "{}: schema_version {}, want 1", path.display(), m.schema_version);
    Ok(m)
}

/// All documents of a synthetic dataset (all units), in manifest order. Keys are relative to the source directory.
fn synthetic_documents(root: &Path, name: &str) -> Result<Vec<Document>> {
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

/// Rejects names that could escape the source directory.
pub fn valid_name(name: &str) -> bool {
    !name.is_empty() && !name.contains('/') && !name.contains('\\') && name != "." && name != ".."
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sources::RequiredFields;

    fn source(dir: &Path, name: &str, kind: Kind) -> Source {
        Source {
            name: name.into(),
            kind,
            connection_type: "local".into(),
            configured_path: dir.into(),
            path: dir.into(),
            required_fields: (kind == Kind::Documents).then(RequiredFields::default),
        }
    }

    fn tmp(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("tester-datasets-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn touch(path: PathBuf) {
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, b"%PDF-1.4").unwrap();
    }

    #[test]
    fn documents_source_lists_subdirectories_and_loose_pdfs() {
        let dir = tmp("docs");
        touch(dir.join("b.pdf"));
        touch(dir.join("a.PDF"));
        touch(dir.join(".hidden.pdf"));
        std::fs::write(dir.join("notes.txt"), "x").unwrap();
        touch(dir.join("supplier-x/x1.pdf"));
        touch(dir.join("supplier-x/deeper/ignored.pdf"));
        std::fs::create_dir_all(dir.join("empty")).unwrap();
        std::os::unix::fs::symlink(dir.join("b.pdf"), dir.join("linked.pdf")).unwrap();

        let s = source(&dir, "real", Kind::Documents);
        let list = list(&s).unwrap();
        assert_eq!(list.iter().map(|d| (d.name.as_str(), d.total_documents)).collect::<Vec<_>>(), [("real", 3), ("supplier-x", 1)]);
        assert!(list.iter().all(|d| d.error.is_none() && d.kind == Kind::Documents && d.source == "real"));
        let ids: Vec<String> = documents(&s, "real").unwrap().into_iter().map(|d| d.invoice_id).collect();
        assert_eq!(ids, ["a", "b", "linked"]);
        assert_eq!(document_path(&s, "supplier-x", "x1").unwrap(), dir.join("supplier-x/x1.pdf"));
        assert!(documents(&s, "missing").is_err());
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn subdirectory_named_like_the_source_wins() {
        let dir = tmp("clash");
        touch(dir.join("loose.pdf"));
        touch(dir.join("real/inner.pdf"));
        let s = source(&dir, "real", Kind::Documents);
        let list = list(&s).unwrap();
        assert_eq!(list.len(), 1);
        assert_eq!(documents(&s, "real").unwrap()[0].invoice_id, "inner");
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn extension_case_duplicates_cannot_run() {
        let dir = tmp("dup");
        touch(dir.join("x.pdf"));
        touch(dir.join("x.PDF"));
        let s = source(&dir, "real", Kind::Documents);
        assert!(list(&s).unwrap()[0].error.as_deref().unwrap().contains("x"));
        assert!(documents(&s, "real").is_err());
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn synthetic_source_lists_manifest_directories() {
        let dir = tmp("synth");
        std::fs::create_dir_all(dir.join("run-x/manifest")).unwrap();
        std::fs::write(
            dir.join("run-x/manifest.json"),
            r#"{"schema_version": 1, "total_documents": 1, "total_units": 1, "created_at": "2026-10-22T00:00:00Z",
                "parts": [{"key": "run-x/manifest/unit-000000.json"}]}"#,
        )
        .unwrap();
        std::fs::write(dir.join("run-x/manifest/unit-000000.json"), r#"{"documents": [{"key": "run-x/documents/unit-000000/inv_1.pdf"}]}"#).unwrap();
        std::fs::create_dir_all(dir.join("no-manifest")).unwrap();
        let s = source(&dir, "synthetic", Kind::Synthetic);
        let list = list(&s).unwrap();
        assert_eq!(list.iter().map(|d| (d.name.as_str(), d.total_units)).collect::<Vec<_>>(), [("run-x", Some(1))]);
        assert_eq!(documents(&s, "run-x").unwrap()[0].invoice_id, "inv_1");
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
