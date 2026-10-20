//! MinIO access: pipeline artifacts and today's LLM spend (read-only).

use anyhow::{Context, Result};
use aws_sdk_s3::{Client, config::{BehaviorVersion, Credentials, Region}};
use rust_decimal::Decimal;
use std::str::FromStr;

pub const BUCKET: &str = "invoices";
pub const USAGE_BUCKET: &str = "fields-extractor-usage";

#[derive(Clone)]
pub struct Objects {
    client: Client,
}

impl Objects {
    pub fn new(endpoint: &str, access_key: &str, secret_key: &str) -> Objects {
        let url = if endpoint.starts_with("http") { endpoint.to_string() } else { format!("http://{endpoint}") };
        let config = aws_sdk_s3::Config::builder()
            .behavior_version(BehaviorVersion::latest())
            .endpoint_url(url)
            .region(Region::new("us-east-1"))
            .credentials_provider(Credentials::new(access_key, secret_key, None, None, "static"))
            .force_path_style(true)
            .build();
        Objects { client: Client::from_conf(config) }
    }

    /// Object bytes, or None if it does not exist.
    pub async fn get(&self, bucket: &str, key: &str) -> Result<Option<Vec<u8>>> {
        match self.client.get_object().bucket(bucket).key(key).send().await {
            Ok(out) => Ok(Some(out.body.collect().await.context("read object")?.into_bytes().to_vec())),
            Err(e) => {
                if e.as_service_error().map(|s| s.is_no_such_key()).unwrap_or(false) {
                    Ok(None)
                } else {
                    Err(anyhow::anyhow!("get {bucket}/{key}: {}", aws_sdk_s3::error::DisplayErrorContext(&e)))
                }
            }
        }
    }

    /// Sum of today's fields-extractor spend counters (fields-extractor/specs/overview.md, Daily budget).
    pub async fn spent_today(&self, day: &str) -> Result<Decimal> {
        let mut total = Decimal::ZERO;
        let mut pages = self.client.list_objects_v2().bucket(USAGE_BUCKET).prefix(format!("{day}/")).into_paginator().send();
        while let Some(page) = pages.next().await {
            let page = page.map_err(|e| anyhow::anyhow!("list {USAGE_BUCKET}: {}", aws_sdk_s3::error::DisplayErrorContext(&e)))?;
            for obj in page.contents() {
                if let Some(key) = obj.key()
                    && let Some(data) = self.get(USAGE_BUCKET, key).await?
                {
                    let v: serde_json::Value = serde_json::from_slice(&data)?;
                    if let Some(s) = v.get("spent_usd").and_then(|s| s.as_str()) {
                        total += Decimal::from_str(s).unwrap_or(Decimal::ZERO);
                    }
                }
            }
        }
        Ok(total)
    }

    pub async fn check(&self) -> Result<()> {
        self.client
            .head_bucket()
            .bucket(BUCKET)
            .send()
            .await
            .map_err(|e| anyhow::anyhow!("bucket {BUCKET}: {}", aws_sdk_s3::error::DisplayErrorContext(&e)))?;
        Ok(())
    }
}
