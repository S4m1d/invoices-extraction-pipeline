# 0005. Testing client frontend is a React web app

- Status: accepted
- Date: 2026-10-13

## Context

The draft proposed "JS, Slint" for the frontend of the testing client, with a Rust backend. Slint is primarily a native UI toolkit.

## Decision

The frontend is a web app built with React. It is a thin client that only visualizes what the Rust backend provides over HTTP.

## Consequences

- No UI toolkit dependency in Rust; backend exposes an HTTP API (`tester/backend/specs/api.md`).
- Build tooling and language flavour (JS/TS) are still open.
