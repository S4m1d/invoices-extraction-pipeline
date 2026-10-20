// Run details: progress, controls, summary, document list (frontend/specs/overview.md, Screens).
import { useState } from "react";
import { Link, useParams } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, isActive, pct, seconds, type Breakdown, type Summary } from "../api";
import { PipelineBanner, StatusBadge } from "./common";
import s from "../app.module.css";

const STATUSES = ["passed", "failed", "error", "timeout", "uploaded", "pending"] as const;

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
  const finished = counts.passed + counts.failed + counts.error + counts.timeout;

  return (
    <>
      <p>
        <Link to="/">← Runs</Link>
      </p>
      <PipelineBanner waiting={waiting} />
      <section className={s.card}>
        <h2>
          Run on {r.dataset} <StatusBadge status={r.status} />
        </h2>
        <p className={s.muted}>
          Started {new Date(r.started_at).toLocaleString()}
          {r.finished_at && <> · finished {new Date(r.finished_at).toLocaleString()}</>} · {r.parallelism} parallel uploads
          {r.overlapped && <> · overlapped with another run (durations not comparable)</>}
        </p>
        <div className={s.stats}>
          {STATUSES.map((st) => (
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
            <button className={s.danger} onClick={() => confirm("Stop this run? Remaining documents are not uploaded.") && control.mutate("stop")}>
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
              {STATUSES.map((st) => (
                <option key={st}>{st}</option>
              ))}
            </select>
          </label>
          <label>
            Template
            <input value={filters.template_id ?? ""} onChange={(e) => setFilters({ ...filters, template_id: e.target.value })} />
          </label>
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
              <th>Template</th>
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
                <td>{d.template_id}</td>
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
        Passed {d.passed} of {d.total} ({pct(d.passed, d.total)}) · failed {d.failed} · pipeline errors {d.error} · timeouts {d.timeout}
      </p>
      <p className={s.muted}>
        Run time {seconds(summary.wall_clock_s)} · per document: median {seconds(summary.durations?.median_s)}, p95{" "}
        {seconds(summary.durations?.p95_s)}, max {seconds(summary.durations?.max_s)}
        {summary.models.length > 0 && <> · {summary.models.map((m) => `${m.model} prompt ${m.prompt ?? "?"} (${m.documents})`).join(", ")}</>}
      </p>
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
