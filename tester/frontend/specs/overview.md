# Tester Frontend: Overview

## Purpose

Thin web client. Only visualizes what the testing backend provides; contains no business logic.

## Stack

JavaScript, React.

## Screens (PROPOSED)

- Dataset selection (all subdirectories of the datasets root, e.g. `run-001`; switchable) and run start (parallelism setting).
- Run history list.
- Run details: summary, list of documents with status.
- Failed document details: expected vs. actual fields, link to open the source file.

## Interfaces

- Consumes the backend HTTP API: see `tester/backend/specs/api.md`.

## Open questions

- OPEN: build tooling (Vite, etc.), TypeScript or plain JS.
- OPEN: live progress updates (polling vs. server-sent events).
