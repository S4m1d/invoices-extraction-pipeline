//! Data sources configuration (tester/backend/specs/data-sources.md).

use anyhow::{Context, Result, bail};
use serde::Serialize;
use serde_norway::Value;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Kind {
    Synthetic,
    Documents,
}

impl Kind {
    pub fn as_str(self) -> &'static str {
        match self {
            Kind::Synthetic => "synthetic",
            Kind::Documents => "documents",
        }
    }

    pub fn parse(s: &str) -> Option<Kind> {
        match s {
            "synthetic" => Some(Kind::Synthetic),
            "documents" => Some(Kind::Documents),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct RequiredFields {
    pub invoice: Vec<String>,
    pub line: Vec<String>,
}

/// Extraction-schema field names (fields-extractor/specs/extraction-schema.md).
pub const INVOICE_FIELDS: [&str; 10] = [
    "supplier_name", "recipient_name", "invoice_number", "issue_date", "due_date", "currency", "subtotal", "tax_total",
    "grand_total", "tax_rate",
];
pub const LINE_FIELDS: [&str; 5] = ["line_no", "description", "quantity", "unit_price", "amount"];

impl Default for RequiredFields {
    fn default() -> Self {
        let v = |s: &[&str]| s.iter().map(|s| s.to_string()).collect();
        RequiredFields {
            invoice: v(&["supplier_name", "invoice_number", "issue_date", "currency", "subtotal", "tax_total", "grand_total"]),
            line: v(&["description", "quantity", "amount"]),
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct Source {
    pub name: String,
    pub kind: Kind,
    pub connection_type: String,
    /// `connection.path` as configured (a host path in the Docker setup).
    pub configured_path: PathBuf,
    /// Where the backend reads the source: `{mount_root}/{name}` when a mount root is set, else `configured_path`.
    #[serde(skip)]
    pub path: PathBuf,
    /// `documents` sources only; the default when not configured.
    pub required_fields: Option<RequiredFields>,
}

impl Source {
    /// None if the source can be read, else the reason (shown as `unavailable`).
    pub fn unavailable(&self) -> Option<String> {
        match std::fs::read_dir(&self.path) {
            Ok(_) => None,
            Err(e) => Some(format!("{}: {e}", self.path.display())),
        }
    }
}

pub fn load(file: &Path, mount_root: Option<&Path>) -> Result<Vec<Source>> {
    let text = std::fs::read_to_string(file).with_context(|| format!("read data sources file {}", file.display()))?;
    parse(&text, mount_root).with_context(|| format!("data sources file {}", file.display()))
}

/// Parses and validates the YAML, reporting every problem at once.
pub fn parse(yaml: &str, mount_root: Option<&Path>) -> Result<Vec<Source>> {
    let doc: Value = serde_norway::from_str(yaml).context("not valid YAML")?;
    let mut errors: Vec<String> = Vec::new();
    let Some(list) = doc.get("sources").and_then(Value::as_sequence) else {
        bail!("`sources` must be a list");
    };
    if list.is_empty() {
        bail!("`sources` must have at least one entry");
    }
    let mut out = Vec::new();
    let mut seen: Vec<String> = Vec::new();
    for (i, entry) in list.iter().enumerate() {
        let at = |key: &str| format!("sources[{i}].{key}");
        let text = |v: Option<&Value>| v.and_then(Value::as_str).map(str::to_string);
        let name = text(entry.get("name"));
        let label = name.clone().unwrap_or_else(|| format!("sources[{i}]"));
        match &name {
            None => errors.push(format!("{}: missing", at("name"))),
            Some(n) if !valid_name(n) => {
                errors.push(format!("{}: {n:?} must be 1 to 40 lowercase letters, digits or '-'", at("name")))
            }
            Some(n) if seen.contains(n) => errors.push(format!("{}: duplicate name {n:?}", at("name"))),
            _ => {}
        }
        seen.extend(name.clone());
        let kind = match text(entry.get("kind")) {
            None => {
                errors.push(format!("{label}: kind: missing"));
                None
            }
            Some(k) => Kind::parse(&k).or_else(|| {
                errors.push(format!("{label}: kind: unknown kind {k:?} (synthetic or documents)"));
                None
            }),
        };
        let conn = entry.get("connection");
        let conn_type = text(conn.and_then(|c| c.get("type")));
        match conn_type.as_deref() {
            None => errors.push(format!("{label}: connection.type: missing")),
            Some("local") => {}
            Some(t) => errors.push(format!("{label}: connection.type: {t:?} is not implemented; only \"local\" is supported")),
        }
        let path = text(conn.and_then(|c| c.get("path")));
        match path.as_deref() {
            None => errors.push(format!("{label}: connection.path: missing")),
            Some(p) if !Path::new(p).is_absolute() => errors.push(format!("{label}: connection.path: {p:?} must be absolute")),
            _ => {}
        }
        let required = match (entry.get("required_fields"), kind) {
            (Some(_), Some(Kind::Synthetic)) => {
                errors.push(format!("{label}: required_fields: only allowed on documents sources"));
                None
            }
            (Some(v), Some(Kind::Documents)) => Some(required_fields(v, &label, &mut errors)),
            (None, Some(Kind::Documents)) => Some(RequiredFields::default()),
            _ => None,
        };
        if let Some(map) = entry.as_mapping() {
            for key in map.keys().filter_map(Value::as_str) {
                if !["name", "kind", "connection", "required_fields"].contains(&key) {
                    errors.push(format!("{label}: unknown key {key:?}"));
                }
            }
        }
        if let (Some(name), Some(kind), Some(conn_type), Some(path)) = (name, kind, conn_type, path) {
            let configured_path = PathBuf::from(path);
            let effective = mount_root.map(|r| r.join(&name)).unwrap_or_else(|| configured_path.clone());
            out.push(Source { name, kind, connection_type: conn_type, configured_path, path: effective, required_fields: required });
        }
    }
    if !errors.is_empty() {
        bail!("{}", errors.join("; "));
    }
    Ok(out)
}

fn required_fields(v: &Value, label: &str, errors: &mut Vec<String>) -> RequiredFields {
    let mut list = |key: &str, allowed: &[&str]| -> Vec<String> {
        let Some(items) = v.get(key) else { return vec![] };
        let Some(items) = items.as_sequence() else {
            errors.push(format!("{label}: required_fields.{key}: must be a list"));
            return vec![];
        };
        let mut out = Vec::new();
        for item in items {
            match item.as_str() {
                Some(f) if allowed.contains(&f) => out.push(f.to_string()),
                Some(f) => errors.push(format!("{label}: required_fields.{key}: unknown field {f:?}")),
                None => errors.push(format!("{label}: required_fields.{key}: field names must be strings")),
            }
        }
        out
    };
    RequiredFields { invoice: list("invoice", &INVOICE_FIELDS), line: list("line", &LINE_FIELDS) }
}

fn valid_name(name: &str) -> bool {
    (1..=40).contains(&name.len()) && name.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-')
}

pub const MOUNT_ROOT: &str = "/sources";
pub const CONFIG_PATH: &str = "/config/sources.yaml";

/// Compose override that mounts every source except `skip` read-only at /sources/{name}, and the YAML
/// itself (specs/data-sources.md, Docker setup). Written to infra/, so `./tester-sources.yaml` resolves there.
pub fn compose_override(sources: &[Source], skip: &[String]) -> String {
    let q = |s: &str| serde_json::to_string(s).expect("string"); // a JSON string is a valid YAML scalar
    let mut out = String::from(
        "# Generated by `tester sources-override` from infra/tester-sources.yaml (tester/backend/specs/data-sources.md).\n\
         # Do not edit: `make up` rewrites it.\nservices:\n  tester:\n    environment:\n",
    );
    out += &format!("      TESTER_SOURCES_FILE: {CONFIG_PATH}\n      TESTER_SOURCES_MOUNT_ROOT: {MOUNT_ROOT}\n    volumes:\n");
    out += &format!("      - type: bind\n        source: ./tester-sources.yaml\n        target: {CONFIG_PATH}\n        read_only: true\n");
    for s in sources.iter().filter(|s| !skip.contains(&s.name)) {
        out += &format!(
            "      - type: bind\n        source: {}\n        target: {MOUNT_ROOT}/{}\n        read_only: true\n",
            q(&s.configured_path.to_string_lossy()),
            s.name
        );
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    const EXAMPLE: &str = "
sources:
  - name: synthetic
    kind: synthetic
    connection: {type: local, path: /data/blobs}
  - name: real
    kind: documents
    connection:
      type: local
      path: /mnt/real_docs
    required_fields:
      invoice: [supplier_name, due_date]
      line: [amount]
  - name: more
    kind: documents
    connection: {type: local, path: /mnt/more}
";

    #[test]
    fn parses_example_with_defaults() {
        let s = parse(EXAMPLE, None).unwrap();
        assert_eq!(s.iter().map(|s| (s.name.as_str(), s.kind)).collect::<Vec<_>>(), [
            ("synthetic", Kind::Synthetic),
            ("real", Kind::Documents),
            ("more", Kind::Documents)
        ]);
        assert_eq!(s[0].path, PathBuf::from("/data/blobs"));
        assert_eq!(s[0].required_fields, None);
        assert_eq!(s[1].required_fields.as_ref().unwrap().invoice, ["supplier_name", "due_date"]);
        assert_eq!(s[1].required_fields.as_ref().unwrap().line, ["amount"]);
        assert_eq!(s[2].required_fields, Some(RequiredFields::default()));
    }

    #[test]
    fn mount_root_replaces_paths() {
        let s = parse(EXAMPLE, Some(Path::new("/sources"))).unwrap();
        assert_eq!(s[1].path, PathBuf::from("/sources/real"));
        assert_eq!(s[1].configured_path, PathBuf::from("/mnt/real_docs"));
    }

    #[test]
    fn reports_all_problems() {
        let yaml = "
sources:
  - name: Bad_Name
    kind: synthetic
    connection: {type: local, path: relative/path}
    required_fields: {invoice: [supplier_name]}
  - name: s3
    kind: golden
    connection: {type: s3, path: /x}
  - name: s3
    kind: documents
    connection: {type: local}
    required_fields: {invoice: [vat_id]}
    colour: blue
";
        let err = parse(yaml, None).unwrap_err().to_string();
        for want in [
            "must be 1 to 40 lowercase",
            "must be absolute",
            "only allowed on documents sources",
            "unknown kind \"golden\"",
            "\"s3\" is not implemented; only \"local\"",
            "duplicate name \"s3\"",
            "connection.path: missing",
            "unknown field \"vat_id\"",
            "unknown key \"colour\"",
        ] {
            assert!(err.contains(want), "{err}\n  does not contain {want:?}");
        }
    }

    #[test]
    fn rejects_empty_and_invalid_documents() {
        assert!(parse("sources: []", None).unwrap_err().to_string().contains("at least one"));
        assert!(parse("other: 1", None).unwrap_err().to_string().contains("must be a list"));
        assert!(parse("sources: [", None).unwrap_err().to_string().contains("not valid YAML"));
    }

    #[test]
    fn compose_override_mounts_sources_and_config() {
        let s = parse(EXAMPLE, None).unwrap();
        let out = compose_override(&s, &["more".to_string()]);
        let doc: Value = serde_norway::from_str(&out).unwrap();
        let tester = &doc["services"]["tester"];
        assert_eq!(tester["environment"]["TESTER_SOURCES_MOUNT_ROOT"].as_str(), Some("/sources"));
        let mounts: Vec<(&str, &str)> = tester["volumes"]
            .as_sequence()
            .unwrap()
            .iter()
            .map(|v| (v["source"].as_str().unwrap(), v["target"].as_str().unwrap()))
            .collect();
        assert_eq!(mounts, [
            ("./tester-sources.yaml", "/config/sources.yaml"),
            ("/data/blobs", "/sources/synthetic"),
            ("/mnt/real_docs", "/sources/real"),
        ]);
        assert!(tester["volumes"].as_sequence().unwrap().iter().all(|v| v["read_only"].as_bool() == Some(true)));
    }
}
