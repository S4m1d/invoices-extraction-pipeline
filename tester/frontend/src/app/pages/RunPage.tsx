// Run details: progress, controls, summary, document list (frontend/specs/overview.md, Screens).
import { useState } from "react";
import { Link, useParams } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, CHECK_LABEL, finishedCount, isActive, KIND_LABEL, pct, score, seconds, type Breakdown, type DocumentsSummary, type Metrics, type Summary, type SyntheticSummary } from "../api";
import { PipelineBanner, StatusBadge } from "./common";
import s from "../app.module.css";

const STATUSES = ["passed", "failed", "error", "cancelled", "uploaded", "pending"] as const;

export function RunPage() {
  const { runId = "" } = useParams();
  const qc = useQueryClient();
  const run = useQuery({
    queryKey: ["run", runId],
    queryFn: () => api.run(runId),
    refetchInterval: (q) => {
      const d = q.state.data;
      return d && (isActive(d.run.status) || d.counts.uploaded > 0 || !d.run.finished_at) ? 2000 : false;
    },
  });
  const [filters, setFilters] = useState<Record<string, string>>({});
  const docs = useQuery({
    queryKey: ["documents", runId, filters, run.data?.counts],
    queryFn: () => api.documents(runId, filters),
    enabled: !!run.data,
  });
  const control = useMutation({
    mutationFn: (action: "pause" | "resume" | "stop") => api.control(runId, action),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["run", runId] }),
  });

  if (run.isError) return <p className={s.error}>{String(run.error)}</p>;
  if (!run.data) return <p>Loading…</p>;
  const { run: r, counts, summary, waiting } = run.data;
  const finished = finishedCount(counts);
  const synthetic = r.kind !== "documents";
  // `timeout` exists only in runs from before timeouts were removed (ADR 0016).
  const statuses = counts.timeout ? [...STATUSES.slice(0, 4), "timeout" as const, ...STATUSES.slice(4)] : STATUSES;

  return (
    <>
      <p>
        <Link to="/">← Runs</Link>
      </p>
      <PipelineBanner waiting={waiting} />
      <section className={s.card}>
        <h2>
          Run on {r.source}/{r.dataset} <StatusBadge status={r.status} />
        </h2>
        <p className={s.muted}>
          {KIND_LABEL[r.kind]} · Started {new Date(r.started_at).toLocaleString()}
          {r.finished_at && <> · finished {new Date(r.finished_at).toLocaleString()}</>} · {r.parallelism} parallel uploads
          {r.overlapped && <> · overlapped with another run (durations not comparable)</>}
        </p>
        <div className={s.stats}>
          {statuses.map((st) => (
            <div key={st} className={s.stat}>
              <span className={s.statValue}>{counts[st]}</span>
              <span className={s.statLabel}>{st}</span>
            </div>
          ))}
        </div>
        <progress max={r.documents_total} value={finished} className={s.progress} />
        <div className={s.form}>
          {r.status === "running" && <button onClick={() => control.mutate("pause")}>Pause</button>}
          {r.status === "paused" && <button onClick={() => control.mutate("resume")}>Resume</button>}
          {isActive(r.status) && (
            <button className={s.danger} onClick={() => confirm("Stop this run? Remaining documents are not uploaded, and documents still in the pipeline are cancelled.") && control.mutate("stop")}>
              Stop
            </button>
          )}
        </div>
        {control.isError && <p className={s.error}>{String(control.error)}</p>}
      </section>

      {summary && <SummaryView summary={summary} />}

      <section className={s.card}>
        <h2>Documents</h2>
        <div className={s.form}>
          <label>
            Status
            <select value={filters.status ?? ""} onChange={(e) => setFilters({ ...filters, status: e.target.value })}>
              <option value="">all</option>
              {statuses.map((st) => (
                <option key={st}>{st}</option>
              ))}
            </select>
          </label>
          {synthetic && (
            <label>
              Template
              <input value={filters.template_id ?? ""} onChange={(e) => setFilters({ ...filters, template_id: e.target.value })} />
            </label>
          )}
          <label>
            Currency
            <input value={filters.currency ?? ""} onChange={(e) => setFilters({ ...filters, currency: e.target.value.toUpperCase() })} />
          </label>
        </div>
        <table className={s.table}>
          <thead>
            <tr>
              <th>Invoice</th>
              <th>Status</th>
              {synthetic && <th>Template</th>}
              <th>Currency</th>
              <th>Error / reason</th>
            </tr>
          </thead>
          <tbody>
            {docs.data?.documents.map((d) => (
              <tr key={d.invoice_id}>
                <td>
                  <Link to={`/runs/${runId}/documents/${d.invoice_id}`}>{d.invoice_id}</Link>
                </td>
                <td>
                  <StatusBadge status={d.status} />
                </td>
                {synthetic && <td>{d.template_id}</td>}
                <td>{d.currency}</td>
                <td className={s.muted}>
                  {d.error_code ? `${d.error_stage}: ${d.error_code}` : (d.timeout_reason ?? "")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {docs.data && <p className={s.muted}>{docs.data.total} documents</p>}
      </section>
    </>
  );
}

function SummaryView({ summary }: { summary: Summary }) {
  const d = summary.documents;
  return (
    <section className={s.card}>
      <h2>Summary</h2>
      <p>
        Passed {d.passed} of {d.total} ({pct(d.passed, d.total)}) · failed {d.failed} · pipeline errors {d.error}
        {d.cancelled ? <> · cancelled {d.cancelled}</> : null}
        {d.timeout ? <> · timeouts {d.timeout}</> : null}
      </p>
      <p className={s.muted}>
        Run time {seconds(summary.wall_clock_s)} · per document: median {seconds(summary.durations?.median_s)}, p95{" "}
        {seconds(summary.durations?.p95_s)}, max {seconds(summary.durations?.max_s)}
        {summary.models.length > 0 && <> · {summary.models.map((m) => `${m.model} prompt ${m.prompt ?? "?"} (${m.documents})`).join(", ")}</>}
      </p>
      {summary.kind === "documents" ? <DocumentsSummaryView summary={summary} /> : <SyntheticSummaryView summary={summary} />}
      {summary.errors.length > 0 && (
        <>
          <h3>Pipeline errors</h3>
          <table className={s.table}>
            <tbody>
              {summary.errors.map((e) => (
                <tr key={`${e.stage}/${e.code}`}>
                  <td>{e.stage}</td>
                  <td>{e.code}</td>
                  <td>{e.documents}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  );
}

/** No golden set: required-field presence, arithmetic checks, extracted currency (backend validation.md, Summary). */
function DocumentsSummaryView({ summary }: { summary: DocumentsSummary }) {
  return (
    <>
      <h3>Required fields</h3>
      <table className={s.table}>
        <thead>
          <tr>
            <th>Field</th>
            <th>Present</th>
            <th>Missing</th>
            <th>Present %</th>
          </tr>
        </thead>
        <tbody>
          {summary.required_fields.map((f) => (
            <tr key={`${f.line_field}/${f.field}`}>
              <td>
                {f.line_field ? "line " : ""}
                {f.field}
              </td>
              <td>{f.present}</td>
              <td>{f.missing}</td>
              <td>{pct(f.present, f.present + f.missing)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <h3>Checks</h3>
      <table className={s.table}>
        <thead>
          <tr>
            <th>Check</th>
            <th>Pass</th>
            <th>Fail</th>
            <th>Not applicable</th>
          </tr>
        </thead>
        <tbody>
          {summary.checks.map((c) => (
            <tr key={c.check}>
              <td>{CHECK_LABEL[c.check] ?? c.check}</td>
              <td>{c.pass}</td>
              <td>{c.fail}</td>
              <td>{c.not_applicable}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <h3>By currency (extracted)</h3>
      <table className={s.table}>
        <thead>
          <tr>
            <th />
            <th>Docs</th>
            <th>Passed</th>
          </tr>
        </thead>
        <tbody>
          {summary.by_currency.map((b) => (
            <tr key={b.value ?? ""}>
              <td>{b.value ?? "–"}</td>
              <td>{b.documents}</td>
              <td>{pct(b.passed, b.documents)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

/** Headline metrics (backend comparison.md, Metrics). */
function MetricTiles({ metrics }: { metrics?: Metrics }) {
  if (!metrics) return <p className={s.muted}>Headline metrics unavailable: this run's summary could not be recomputed.</p>;
  const pr = (m: Metrics["header"]) => `P ${score(m.precision)} · R ${score(m.recall)}`;
  const hd = metrics.header_documents;
  const tiles = [
    { label: "Header field accuracy", value: score(metrics.header.accuracy), note: `${metrics.header.match} of ${metrics.header.match + metrics.header.mismatch + metrics.header.missing + metrics.header.unexpected}` },
    { label: "Line field accuracy", value: score(metrics.line.accuracy), note: `${metrics.line.match} of ${metrics.line.match + metrics.line.mismatch + metrics.line.missing + metrics.line.unexpected}` },
    { label: "Header F1", value: score(metrics.header.f1), note: pr(metrics.header) },
    { label: "Line F1", value: score(metrics.line.f1), note: pr(metrics.line) },
    { label: "Documents with all header fields right", value: score(hd.accuracy), note: `${hd.correct} of ${hd.compared} compared` },
  ];
  return (
    <div className={s.stats}>
      {tiles.map((t) => (
        <div key={t.label} className={s.stat}>
          <span className={s.statValue}>{t.value}</span>
          <span className={s.statLabel}>{t.label}</span>
          <span className={s.statLabel}>{t.note}</span>
        </div>
      ))}
    </div>
  );
}

function SyntheticSummaryView({ summary }: { summary: SyntheticSummary }) {
  return (
    <>
      <MetricTiles metrics={summary.metrics} />
      <h3>Field accuracy</h3>
      <table className={s.table}>
        <thead>
          <tr>
            <th>Field</th>
            <th>Match</th>
            <th>Mismatch</th>
            <th>Missing</th>
            <th>Unexpected</th>
            <th>Accuracy</th>
            <th>Precision</th>
            <th>Recall</th>
            <th>F1</th>
          </tr>
        </thead>
        <tbody>
          {summary.fields.map((f) => {
            const total = f.match + f.mismatch + f.missing + f.unexpected;
            return (
              <tr key={f.field}>
                <td>
                  {f.line_field ? "line " : ""}
                  {f.field}
                </td>
                <td>{f.match}</td>
                <td>{f.mismatch}</td>
                <td>{f.missing}</td>
                <td>{f.unexpected}</td>
                <td>{pct(f.match, total)}</td>
                <td>{score(f.precision)}</td>
                <td>{score(f.recall)}</td>
                <td>{score(f.f1)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {summary.lines && (
        <p className={s.muted}>
          Lines: {summary.lines.compared} compared by position, {summary.lines.missing} missing, {summary.lines.extra} extra
        </p>
      )}
      <div className={s.columns}>
        <BreakdownTable title="By template" rows={summary.by_template} />
        <BreakdownTable title="By currency" rows={summary.by_currency} />
        <BreakdownTable title="By language" rows={summary.by_language} />
      </div>
    </>
  );
}

function BreakdownTable({ title, rows }: { title: string; rows: Breakdown[] }) {
  return (
    <div>
      <h3>{title}</h3>
      <table className={s.table}>
        <thead>
          <tr>
            <th />
            <th>Docs</th>
            <th>Passed</th>
            <th>Fields</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((b) => (
            <tr key={b.value ?? ""}>
              <td>{b.value ?? "–"}</td>
              <td>{b.documents}</td>
              <td>{pct(b.passed, b.documents)}</td>
              <td>{pct(b.field_match, b.field_total)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
