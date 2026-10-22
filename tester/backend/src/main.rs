//! Tester backend (tester/backend/specs): `tester` runs the service, `tester healthcheck` probes it,
//! `tester sources-override` prints the Compose mounts for a data sources file (specs/data-sources.md).

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
mod sources;
mod store;
mod tracker;
mod validate;

use anyhow::Result;
use std::{sync::Arc, time::Duration};
use tower_http::services::{ServeDir, ServeFile};

#[tokio::main]
async fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.first().map(String::as_str) {
        Some("healthcheck") => std::process::exit(healthcheck().await),
        Some("sources-override") => std::process::exit(sources_override(&args[1..])),
        _ => {}
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

    let sources = sources::load(&cfg.sources_file, cfg.sources_mount_root.as_deref())?;
    for s in &sources {
        match s.unavailable() {
            None => tracing::info!(source = %s.name, kind = s.kind.as_str(), path = %s.path.display(), "data source"),
            Some(reason) => tracing::warn!(source = %s.name, kind = s.kind.as_str(), reason, "data source unavailable"),
        }
    }

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
    let app = Arc::new(app::App::new(cfg.clone(), sources, db, pg, objects, http.clone()));

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

/// Reads a data sources file on stdin. `--paths` prints "name<TAB>path" per source (the Makefile checks
/// them on the host); otherwise prints the Compose override, without the sources named in `--skip a,b`.
fn sources_override(args: &[String]) -> i32 {
    let mut yaml = String::new();
    if let Err(e) = std::io::Read::read_to_string(&mut std::io::stdin(), &mut yaml) {
        eprintln!("read stdin: {e}");
        return 1;
    }
    let list = match sources::parse(&yaml, None) {
        Ok(l) => l,
        Err(e) => {
            eprintln!("data sources file: {e:#}");
            return 1;
        }
    };
    match args.first().map(String::as_str) {
        Some("--paths") => {
            for s in &list {
                println!("{}\t{}", s.name, s.configured_path.display());
            }
        }
        Some("--skip") => {
            let skip: Vec<String> = args.get(1).map(|a| a.split(',').filter(|s| !s.is_empty()).map(String::from).collect()).unwrap_or_default();
            print!("{}", sources::compose_override(&list, &skip));
        }
        None => print!("{}", sources::compose_override(&list, &[])),
        Some(other) => {
            eprintln!("unknown argument {other:?}; usage: tester sources-override [--paths | --skip name,...] < sources.yaml");
            return 2;
        }
    }
    0
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
