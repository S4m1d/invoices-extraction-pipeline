//! Configuration from the environment (tester/backend/specs/overview.md, Deployment and configuration).

use anyhow::{Result, bail};
use std::path::PathBuf;

#[derive(Debug, Clone)]
pub struct Config {
    pub http_addr: String,
    pub sources_file: PathBuf,
    /// If set, source {name} is read from {root}/{name} (specs/data-sources.md, Docker setup).
    pub sources_mount_root: Option<PathBuf>,
    pub db_path: PathBuf,
    pub ingestor_url: String,
    pub amqp_url: String,
    pub database_url: String,
    pub minio_endpoint: String,
    pub minio_access_key: String,
    pub minio_secret_key: String,
    pub cost_per_document_usd: f64,
    pub daily_budget_usd: f64,
    pub service_health_urls: Vec<(String, String)>,
    pub static_dir: PathBuf,
}

const DEFAULT_HEALTH: &str = "ingestor=http://ingestor:8080/healthz,text-extractor=http://text-extractor:8080/healthz,\
fields-extractor=http://fields-extractor:8080/healthz,integrator=http://integrator:8080/healthz";

impl Config {
    pub fn load(get: impl Fn(&str) -> Option<String>) -> Result<Config> {
        let mut missing = Vec::new();
        let mut invalid = Vec::new();
        let opt = |k: &str, d: &str| get(k).filter(|v| !v.is_empty()).unwrap_or_else(|| d.to_string());
        let mut req = |k: &str| match get(k).filter(|v| !v.is_empty()) {
            Some(v) => v,
            None => {
                missing.push(k.to_string());
                String::new()
            }
        };
        let amqp_url = req("AMQP_URL");
        let database_url = req("DATABASE_URL");
        let minio_endpoint = req("MINIO_ENDPOINT");
        let minio_access_key = req("MINIO_ACCESS_KEY");
        let minio_secret_key = req("MINIO_SECRET_KEY");

        let mut number = |k: &str, d: &str| match opt(k, d).parse::<f64>() {
            Ok(v) if v > 0.0 && v.is_finite() => v,
            _ => {
                invalid.push(k.to_string());
                0.0
            }
        };
        let cost = number("TESTER_COST_PER_DOCUMENT_USD", "0.0026");
        let budget = number("LLM_DAILY_BUDGET_USD", "10");

        let mut health = Vec::new();
        for item in opt("TESTER_SERVICE_HEALTH_URLS", DEFAULT_HEALTH).split(',') {
            match item.trim().split_once('=') {
                Some((name, url)) if !name.is_empty() && url.starts_with("http") => {
                    health.push((name.trim().to_string(), url.trim().to_string()))
                }
                _ => {
                    invalid.push("TESTER_SERVICE_HEALTH_URLS".to_string());
                    break;
                }
            }
        }
        if !missing.is_empty() || !invalid.is_empty() {
            bail!("config: missing [{}], invalid [{}]", missing.join(", "), invalid.join(", "));
        }
        let mut http_addr = opt("TESTER_HTTP_ADDR", ":8090");
        if http_addr.starts_with(':') {
            http_addr = format!("0.0.0.0{http_addr}");
        }
        Ok(Config {
            http_addr,
            sources_file: opt("TESTER_SOURCES_FILE", "/config/sources.yaml").into(),
            sources_mount_root: get("TESTER_SOURCES_MOUNT_ROOT").filter(|v| !v.is_empty()).map(PathBuf::from),
            db_path: opt("TESTER_DB_PATH", "/data/tester.sqlite").into(),
            ingestor_url: opt("INGESTOR_URL", "http://ingestor:8080"),
            amqp_url,
            database_url,
            minio_endpoint,
            minio_access_key,
            minio_secret_key,
            cost_per_document_usd: cost,
            daily_budget_usd: budget,
            service_health_urls: health,
            static_dir: opt("TESTER_STATIC_DIR", "/app/static").into(),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    fn env(pairs: &[(&str, &str)]) -> impl Fn(&str) -> Option<String> {
        let m: HashMap<String, String> = pairs.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect();
        move |k| m.get(k).cloned()
    }

    const REQUIRED: [(&str, &str); 5] = [
        ("AMQP_URL", "amqp://u:p@rabbitmq:5672/"),
        ("DATABASE_URL", "postgres://tester:x@postgres:5432/invoices"),
        ("MINIO_ENDPOINT", "minio:9000"),
        ("MINIO_ACCESS_KEY", "k"),
        ("MINIO_SECRET_KEY", "s"),
    ];

    #[test]
    fn defaults() {
        let c = Config::load(env(&REQUIRED)).unwrap();
        assert_eq!(c.http_addr, "0.0.0.0:8090");
        assert_eq!(c.service_health_urls.len(), 4);
        assert_eq!(c.service_health_urls[2].0, "fields-extractor");
        assert_eq!(c.cost_per_document_usd, 0.0026);
        assert_eq!(c.sources_file, PathBuf::from("/config/sources.yaml"));
        assert_eq!(c.sources_mount_root, None);
    }

    #[test]
    fn reports_all_problems() {
        let err = Config::load(env(&[("TESTER_COST_PER_DOCUMENT_USD", "0"), ("TESTER_SERVICE_HEALTH_URLS", "nope")]))
            .unwrap_err()
            .to_string();
        for name in ["AMQP_URL", "DATABASE_URL", "MINIO_ENDPOINT", "TESTER_COST_PER_DOCUMENT_USD", "TESTER_SERVICE_HEALTH_URLS"] {
            assert!(err.contains(name), "{err} does not mention {name}");
        }
    }
}
