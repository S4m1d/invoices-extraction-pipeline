// Document details: error, artifacts, and expected vs actual (synthetic) or required fields and checks
// (real documents) (frontend/specs/overview.md).
import { Fragment } from "react";
import { Link, useParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import {
  api,
  CHECK_LABEL,
  type CheckRow,
  type DocumentsDocumentDetail,
  type FieldRow,
  type SyntheticDocumentDetail,
} from "../api";
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
  const { document: d, artifacts } = q.data;
  const meta =
    q.data.kind === "documents"
      ? [q.data.currency]
      : [q.data.template_id, q.data.currency, q.data.language];

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
          {meta.filter(Boolean).join(" · ")} · document {d.document_id ?? "not uploaded"} · correlation{" "}
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

      {q.data.kind === "documents" ? <DocumentsResults data={q.data} /> : <SyntheticResults data={q.data} />}
    </>
  );
}

function groupByPosition<T extends { position: number | null }>(rows: T[]): Map<number, T[]> {
  const out = new Map<number, T[]>();
  for (const r of rows.filter((r) => r.position != null)) {
    out.set(r.position!, [...(out.get(r.position!) ?? []), r]);
  }
  return out;
}

/** Real documents: extracted values with missing required fields highlighted, and the checks. */
function DocumentsResults({ data }: { data: DocumentsDocumentDetail }) {
  const { fields, checks, extracted } = data;
  const missing = new Set(fields.filter((f) => f.outcome === "missing").map((f) => `${f.position ?? ""}/${f.field}`));
  const required = new Set(fields.map((f) => `${f.position ?? ""}/${f.field}`));
  if (!extracted) return null;
  const headerFields = ["supplier_name", "recipient_name", "invoice_number", "issue_date", "due_date", "currency", "subtotal", "tax_total", "grand_total", "tax_rate"] as const;
  const lineFields = ["description", "quantity", "unit_price", "amount"] as const;
  const lines = [...extracted.lines].sort((a, b) => a.line_no - b.line_no);
  const cell = (key: string, value: string | null | undefined) => (
    <td className={missing.has(key) ? s.outcome_missing : undefined}>
      {value ?? <em>{missing.has(key) ? "missing" : "null"}</em>}
    </td>
  );
  return (
    <>
      <section className={s.card}>
        <h2>Checks</h2>
        <CheckTable rows={checks} />
      </section>
      <section className={s.card}>
        <h2>Header (extracted)</h2>
        <table className={s.table}>
          <thead>
            <tr>
              <th>Field</th>
              <th>Value</th>
              <th>Required</th>
            </tr>
          </thead>
          <tbody>
            {headerFields.map((f) => (
              <tr key={f} className={missing.has(`/${f}`) ? s.diff : undefined}>
                <td>{f}</td>
                {cell(`/${f}`, extracted[f])}
                <td className={s.muted}>{required.has(`/${f}`) ? "yes" : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section className={s.card}>
        <h2>Lines (extracted)</h2>
        <table className={s.table}>
          <thead>
            <tr>
              <th>#</th>
              {lineFields.map((f) => (
                <th key={f}>{f}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {lines.map((l, i) => (
              <tr key={l.line_no} className={lineFields.some((f) => missing.has(`${i + 1}/${f}`)) ? s.diff : undefined}>
                <td>{l.line_no}</td>
                {lineFields.map((f) => (
                  <Fragment key={f}>{cell(`${i + 1}/${f}`, l[f])}</Fragment>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}

function CheckTable({ rows }: { rows: CheckRow[] }) {
  return (
    <table className={s.table}>
      <thead>
        <tr>
          <th>Check</th>
          <th>Line</th>
          <th>Expected (computed)</th>
          <th>Actual (extracted)</th>
          <th>Outcome</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((c) => (
          <tr key={`${c.check}/${c.position}`} className={c.outcome === "fail" ? s.diff : undefined}>
            <td>{CHECK_LABEL[c.check] ?? c.check}</td>
            <td>{c.position ?? ""}</td>
            <td>{c.expected ?? "–"}</td>
            <td>{c.actual ?? "–"}</td>
            <td className={s[`outcome_${c.outcome}`]}>{c.outcome.replace("_", " ")}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Synthetic: header and lines compared with the golden set. */
function SyntheticResults({ data }: { data: SyntheticDocumentDetail }) {
  const { fields, lines } = data;
  const header = fields.filter((f) => f.position == null);
  const byPosition = groupByPosition(fields);
  return (
    <>
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
