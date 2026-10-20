//! Pipeline health polling for the pause-aware timeout (tester/backend/specs/overview.md, Completion).

use serde::{Deserialize, Serialize};
use std::{collections::BTreeMap, sync::Arc, time::Duration};
use tokio::sync::RwLock;

#[derive(Debug, Clone, Serialize, Default)]
pub struct ServiceHealth {
    pub service: String,
    pub healthy: bool,
    pub failing: Vec<String>,
    pub errors: BTreeMap<String, String>,
}

#[derive(Debug, Clone, Serialize, Default)]
pub struct PipelineState {
    pub services: Vec<ServiceHealth>,
    pub checked_at: Option<String>,
}

impl PipelineState {
    /// The waiting state: None if every service is healthy, else a readable reason.
    pub fn waiting(&self) -> Option<String> {
        let parts: Vec<String> = self
            .services
            .iter()
            .filter(|s| !s.healthy)
            .map(|s| {
                if s.failing.is_empty() {
                    format!("{}: unreachable", s.service)
                } else {
                    let checks: Vec<String> = s
                        .failing
                        .iter()
                        .map(|c| match s.errors.get(c) {
                            Some(reason) => format!("{c}: {reason}"),
                            None => c.clone(),
                        })
                        .collect();
                    format!("{}: {}", s.service, checks.join("; "))
                }
            })
            .collect();
        if parts.is_empty() { None } else { Some(parts.join(" | ")) }
    }
}

#[derive(Deserialize)]
struct HealthBody {
    #[serde(default)]
    failing: Vec<String>,
    #[serde(default)]
    errors: BTreeMap<String, String>,
}

pub type Shared = Arc<RwLock<PipelineState>>;

/// Polls every service's /healthz every 5 s.
pub async fn poll(state: Shared, urls: Vec<(String, String)>, client: reqwest::Client) {
    loop {
        let mut services = Vec::new();
        for (service, url) in &urls {
            services.push(check(&client, service, url).await);
        }
        {
            let mut s = state.write().await;
            let was = s.waiting();
            s.services = services;
            s.checked_at = Some(crate::store::now());
            let now = s.waiting();
            if was != now {
                match &now {
                    Some(reason) => tracing::warn!(reason = %reason, "pipeline waiting"),
                    None => tracing::info!("pipeline healthy again"),
                }
            }
        }
        tokio::time::sleep(Duration::from_secs(5)).await;
    }
}

async fn check(client: &reqwest::Client, service: &str, url: &str) -> ServiceHealth {
    let mut h = ServiceHealth { service: service.to_string(), ..Default::default() };
    match client.get(url).timeout(Duration::from_secs(4)).send().await {
        Ok(resp) if resp.status().is_success() => h.healthy = true,
        Ok(resp) => {
            if let Ok(body) = resp.json::<HealthBody>().await {
                h.failing = body.failing;
                h.errors = body.errors;
            }
        }
        Err(_) => {}
    }
    h
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn waiting_reason() {
        let mut s = PipelineState::default();
        assert_eq!(s.waiting(), None);
        s.services.push(ServiceHealth { service: "ingestor".into(), healthy: true, ..Default::default() });
        s.services.push(ServiceHealth {
            service: "fields-extractor".into(),
            healthy: false,
            failing: vec!["llm".into()],
            errors: [("llm".to_string(), "llm_daily_budget_reached (paused): spent $10.0012 of $10.00".to_string())].into(),
        });
        s.services.push(ServiceHealth { service: "integrator".into(), healthy: false, ..Default::default() });
        assert_eq!(
            s.waiting().unwrap(),
            "fields-extractor: llm: llm_daily_budget_reached (paused): spent $10.0012 of $10.00 | integrator: unreachable"
        );
    }
}
