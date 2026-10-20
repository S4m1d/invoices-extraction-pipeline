# Tester Frontend: Overview

## Purpose

Thin web client. Only visualizes what the testing backend provides; contains no business logic.

## Stack

JavaScript, React: TypeScript, Vite, React Router, TanStack Query (data fetching, polling), no UI component library (plain CSS modules); `npm` with a lockfile.

## Screens

- **Datasets / start run**: datasets found under the datasets root (switchable), document count, cost estimate and today's LLM spend vs. the daily budget; parallelism setting; start button. Active runs are listed, since several can run at once.
- **Run history**: runs with dataset, start time, duration, status, pass rate, model and prompt version.
- **Run details**: live progress while active (counts by status; pause, resume and stop buttons; the pipeline's waiting state when a service is paused or down, which also stops the timeouts); when finished, the summary: pass rate, field accuracy table, breakdown by template / currency / language, pipeline errors by stage and code, durations. Document list with filters (status, template, currency).
- **Document details**: status; pipeline error (stage, code, message) if any; header fields expected vs. actual with outcome highlighting; lines compared by position (missing and extra lines marked) with field differences; timeout reason for timed-out documents; links to open the original PDF and the intermediate artifacts (`layout.md`, `extracted.json`, diagnostics files).

## Live updates

Polling: while a run is active (running or paused with documents in flight), run status every 2 s (TanStack Query `refetchInterval`), stopped when the run ends.

## Build and serving

`vite build` produces static files; the tester image builds them in a Node stage and the backend serves them from `/` (API under `/api`), so there is one container, one origin and no CORS. During development, `vite dev` proxies `/api` to the backend.

## Interfaces

- Consumes the backend HTTP API: see `tester/backend/specs/api.md`.

## Open questions

None. Resolved: TypeScript with Vite (see Stack); polling (see Live updates).
