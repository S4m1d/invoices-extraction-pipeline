//! Tester backend (tester/backend/specs): `tester` runs the service, `tester healthcheck` probes it.

mod analytics;
mod api;
mod app;
mod compare;
mod config;
mod datasets;
mod golden;
mod objects;
mod pipeline;
mod runs;
mod store;
mod tracker;

use anyhow::Result;
use std::{sync::Arc, time::Duration};
use tower_http::services::{ServeDir, ServeFile};

#[tokio::main]
async fn main() {
    if std::env::args().nth(1).as_deref() == Some("healthcheck") {
        std::process::exit(healthcheck().await);
    }
    if let Err(e) = run().await {
        eprintln!("{e:#}");
        std::process::exit(1);
    }
}

async fn run() -> Result<()> {
    let cfg = config::Config::load(|k| std::env::var(k).ok())?;
    tracing_subscriber::fmt()
        .json()
        .with_env_filter(tracing_subscriber::EnvFilter::try_from_env("LOG_LEVEL").unwrap_or_else(|_| "info".into()))
        .init();

    let db = store::open(&cfg.db_path).await?;
    let paused = store::pause_running_runs(&db).await?;
    if paused > 0 {
        tracing::warn!(runs = paused, "runs that were running at shutdown are now paused; resume them to continue");
    }
    let pg = sqlx::postgres::PgPoolOptions::new()
        .max_connections(4)
        .acquire_timeout(Duration::from_secs(5))
        .connect_lazy(&cfg.database_url)?;
    let objects = objects::Objects::new(&cfg.minio_endpoint, &cfg.minio_access_key, &cfg.minio_secret_key);
    let http = reqwest::Client::new();
    let app = Arc::new(app::App::new(cfg.clone(), db, pg, objects, http.clone()));

    tokio::spawn(pipeline::poll(app.pipeline.clone(), cfg.service_health_urls.clone(), http));
    let unmatched: tracker::Unmatched = Default::default();
    tokio::spawn(tracker::consume_failures(app.clone(), unmatched.clone()));
    tokio::spawn(tracker::track(app.clone(), unmatched));

    let index = cfg.static_dir.join("index.html");
    let router = api::router(app).fallback_service(ServeDir::new(&cfg.static_dir).fallback(ServeFile::new(index)));
    let listener = tokio::net::TcpListener::bind(&cfg.http_addr).await?;
    tracing::info!(addr = %cfg.http_addr, "tester started");
    axum::serve(listener, router).with_graceful_shutdown(shutdown()).await?;
    tracing::info!("tester stopped");
    Ok(())
}

/// Resolves on SIGINT or SIGTERM (`docker stop`). Running runs are paused on the next start.
async fn shutdown() {
    let mut term = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()).expect("SIGTERM handler");
    tokio::select! {
        _ = tokio::signal::ctrl_c() => {}
        _ = term.recv() => {}
    }
}

/// Probes /healthz on the local server (Docker HEALTHCHECK).
async fn healthcheck() -> i32 {
    let addr = std::env::var("TESTER_HTTP_ADDR").unwrap_or_else(|_| ":8090".into());
    let addr = if addr.starts_with(':') { format!("127.0.0.1{addr}") } else { addr };
    match reqwest::Client::new().get(format!("http://{addr}/healthz")).timeout(Duration::from_secs(5)).send().await {
        Ok(r) if r.status().is_success() => 0,
        _ => 1,
    }
}
