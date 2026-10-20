// Document details: error, expected vs actual, lines by position, artifacts (frontend/specs/overview.md).
import { Link, useParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { api, type FieldRow } from "../api";
import { StatusBadge } from "./common";
import s from "../app.module.css";

export function DocumentPage() {
  const { runId = "", invoiceId = "" } = useParams();
  const q = useQuery({
    queryKey: ["document", runId, invoiceId],
    queryFn: () => api.document(runId, invoiceId),
    refetchInterval: (q) => (q.state.data && ["pending", "uploaded"].includes(q.state.data.document.status) ? 2000 : false),
  });
  if (q.isError) return <p className={s.error}>{String(q.error)}</p>;
  if (!q.data) return <p>Loading…</p>;
  const { document: d, fields, lines, artifacts } = q.data;
  const header = fields.filter((f) => f.position == null);
  const byPosition = new Map<number, FieldRow[]>();
  for (const f of fields.filter((f) => f.position != null)) {
    byPosition.set(f.position!, [...(byPosition.get(f.position!) ?? []), f]);
  }

  return (
    <>
      <p>
        <Link to={`/runs/${runId}`}>← Run</Link>
      </p>
      <section className={s.card}>
        <h2>
          {d.invoice_id} <StatusBadge status={d.status} />
        </h2>
        <p className={s.muted}>
          {q.data.template_id} · {q.data.currency} · {q.data.language} · document {d.document_id ?? "not uploaded"} · correlation{" "}
          {d.correlation_id ?? "–"}
          {d.extraction_model && <> · {d.extraction_model} prompt {d.prompt_version}</>}
        </p>
        {d.error_code && (
          <p className={s.error}>
            {d.error_stage}: {d.error_code} — {d.error_message}
          </p>
        )}
        {d.timeout_reason && <p className={s.error}>Timeout: {d.timeout_reason}</p>}
        <p>
          {artifacts.map((a) => (
            <a key={a} className={s.artifact} href={api.artifactUrl(runId, invoiceId, a)} target="_blank" rel="noreferrer">
              {a}
            </a>
          ))}
        </p>
      </section>

      {header.length > 0 && (
        <section className={s.card}>
          <h2>Header</h2>
          <FieldTable rows={header} />
        </section>
      )}

      {lines.length > 0 && (
        <section className={s.card}>
          <h2>Lines (compared by position)</h2>
          {lines.map((l) => (
            <div key={l.position} className={s.line}>
              <h3>
                Line {l.position}{" "}
                {l.outcome !== "compared" && <span className={s[`outcome_${l.outcome === "extra" ? "unexpected" : "missing"}`]}>{l.outcome}</span>}
                <span className={s.muted}>
                  {" "}
                  golden #{l.golden_line_no ?? "–"} / extracted #{l.extracted_line_no ?? "–"}
                </span>
              </h3>
              {byPosition.get(l.position) && <FieldTable rows={byPosition.get(l.position)!} />}
            </div>
          ))}
        </section>
      )}
    </>
  );
}

function FieldTable({ rows }: { rows: FieldRow[] }) {
  return (
    <table className={s.table}>
      <thead>
        <tr>
          <th>Field</th>
          <th>Expected (golden)</th>
          <th>Actual (extracted)</th>
          <th>Outcome</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((f) => (
          <tr key={`${f.position}/${f.field}`} className={f.outcome !== "match" ? s.diff : undefined}>
            <td>{f.field}</td>
            <td>{f.expected ?? <em>null</em>}</td>
            <td>{f.actual ?? <em>null</em>}</td>
            <td className={s[`outcome_${f.outcome}`]}>{f.outcome}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
