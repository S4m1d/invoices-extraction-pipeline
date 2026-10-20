//! Shared application state.

use crate::{config::Config, golden, objects::Objects, pipeline};
use anyhow::Result;
use sqlx::{PgPool, SqlitePool};
use std::{collections::HashMap, sync::Arc};
use tokio::sync::{Mutex, watch};

/// Desired upload state of a run, watched by its uploader task.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Control {
    Run,
    Pause,
    Stop,
}

pub struct App {
    pub cfg: Config,
    pub db: SqlitePool,
    pub pg: PgPool,
    pub objects: Objects,
    pub http: reqwest::Client,
    pub pipeline: pipeline::Shared,
    pub controls: Mutex<HashMap<String, watch::Sender<Control>>>,
    golden: Mutex<HashMap<String, Arc<HashMap<String, golden::Golden>>>>,
    pub failures_connected: std::sync::atomic::AtomicBool,
    /// "run_id/invoice_id" of uploads in flight, so a resumed run never uploads a document twice.
    pub inflight: std::sync::Mutex<std::collections::HashSet<String>>,
}

impl App {
    pub fn new(cfg: Config, db: SqlitePool, pg: PgPool, objects: Objects, http: reqwest::Client) -> App {
        App {
            cfg,
            db,
            pg,
            objects,
            http,
            pipeline: Default::default(),
            controls: Mutex::new(HashMap::new()),
            golden: Mutex::new(HashMap::new()),
            failures_connected: Default::default(),
            inflight: Default::default(),
        }
    }

    /// Golden records of a dataset, read once per process (datasets change only when the author adds new ones).
    pub async fn golden(&self, dataset: &str) -> Result<Arc<HashMap<String, golden::Golden>>> {
        let mut cache = self.golden.lock().await;
        if let Some(g) = cache.get(dataset) {
            return Ok(g.clone());
        }
        let root = self.cfg.datasets_dir.clone();
        let name = dataset.to_string();
        let g = Arc::new(tokio::task::spawn_blocking(move || golden::load(&root, &name)).await??);
        cache.insert(dataset.to_string(), g.clone());
        Ok(g)
    }
}
