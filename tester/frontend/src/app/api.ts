// Backend API client and types (tester/backend/specs/api.md).

export type RunStatus = "running" | "paused" | "stopped" | "completed";
/** `timeout` only in runs from before timeouts were removed (ADR 0016). */
export type DocStatus = "pending" | "uploaded" | "passed" | "failed" | "error" | "cancelled" | "timeout";

/** Data source kind (tester/backend/specs/data-sources.md). */
export type Kind = "synthetic" | "documents";

export const KIND_LABEL: Record<Kind, string> = {
  synthetic: "Synthetic (golden set)",
  documents: "Real documents (no golden set)",
};

export interface Source {
  name: string;
  kind: Kind;
  connection_type: string;
  available: boolean;
  reason: string | null;
  required_fields: { invoice: string[]; line: string[] } | null;
}

export interface Dataset {
  source: string;
  kind: Kind;
  name: string;
  total_documents: number;
  total_units: number | null;
  created_at: string | null;
  /** Set when the dataset cannot be run. */
  error: string | null;
  estimated_cost_usd: number;
}

export interface Spend {
  day: string;
  spent_usd: string;
  daily_budget_usd: number;
  cost_per_document_usd: number;
}

export interface Run {
  id: string;
  source: string;
  dataset: string;
  kind: Kind;
  parallelism: number;
  status: RunStatus;
  documents_total: number;
  started_at: string;
  finished_at: string | null;
  overlapped: boolean;
}

export type Counts = Record<DocStatus, number>;

/** Outcome counts with their scores (backend comparison.md, Metrics); a score is null when its denominator is 0. */
export interface Scores {
  match: number;
  mismatch: number;
  missing: number;
  unexpected: number;
  accuracy: number | null;
  precision: number | null;
  recall: number | null;
  f1: number | null;
}

export interface FieldStat extends Partial<Scores> {
  field: string;
  line_field: boolean;
  match: number;
  mismatch: number;
  missing: number;
  unexpected: number;
}

export interface Metrics {
  header: Scores;
  line: Scores;
  header_documents: { compared: number; correct: number; accuracy: number | null };
}

export interface Breakdown {
  value: string | null;
  documents: number;
  passed: number;
  field_match: number;
  field_total: number;
}

interface SummaryCommon {
  /** `cancelled` is absent in summaries stored before it existed. */
  documents: { total: number; passed: number; failed: number; error: number; cancelled?: number; timeout: number; unfinished: number };
  overlapped: boolean;
  errors: { stage: string; code: string; documents: number }[];
  models: { model: string; prompt: string | null; documents: number }[];
  durations: { median_s: number | null; p95_s: number | null; max_s: number | null } | null;
  wall_clock_s: number | null;
}

export interface SyntheticSummary extends SummaryCommon {
  /** Absent in summaries stored before data sources existed. */
  kind?: "synthetic";
  /** Absent when an old summary could not be recomputed (golden set unavailable). */
  metrics?: Metrics;
  fields: FieldStat[];
  lines: { compared: number; missing: number; extra: number };
  by_template: Breakdown[];
  by_currency: Breakdown[];
  by_language: Breakdown[];
}

export interface DocumentsSummary extends SummaryCommon {
  kind: "documents";
  required_fields: { field: string; line_field: boolean; present: number; missing: number }[];
  checks: { check: CheckName; pass: number; fail: number; not_applicable: number }[];
  by_currency: { value: string | null; documents: number; passed: number }[];
}

export type Summary = SyntheticSummary | DocumentsSummary;

export interface RunDetail {
  run: Run;
  counts: Counts;
  waiting: string | null;
  summary: Summary | null;
}

export interface RunListItem {
  run: Run;
  counts: Counts;
  models: Summary["models"] | null;
  metrics: Metrics | null;
}

export interface DocumentRow {
  invoice_id: string;
  document_id: string | null;
  correlation_id: string | null;
  status: DocStatus;
  uploaded_at: string | null;
  finished_at: string | null;
  error_stage: string | null;
  error_code: string | null;
  error_message: string | null;
  timeout_reason: string | null;
  extraction_model: string | null;
  prompt_version: string | null;
  template_id?: string;
  currency?: string;
}

export interface FieldRow {
  position: number | null;
  field: string;
  outcome: "match" | "mismatch" | "missing" | "unexpected";
  expected: string | null;
  actual: string | null;
}

export interface LineRow {
  position: number;
  golden_line_no: number | null;
  extracted_line_no: number | null;
  outcome: "compared" | "missing" | "extra";
}

export interface SyntheticDocumentDetail {
  kind: "synthetic";
  document: DocumentRow;
  template_id: string | null;
  currency: string | null;
  language: string | null;
  fields: FieldRow[];
  lines: LineRow[];
  artifacts: string[];
}

export type CheckName = "lines_sum" | "totals" | "line_amount";

export const CHECK_LABEL: Record<CheckName, string> = {
  lines_sum: "Lines sum to subtotal",
  totals: "Subtotal + tax = total",
  line_amount: "Quantity × unit price = amount",
};

export interface RequiredFieldRow {
  position: number | null;
  field: string;
  outcome: "present" | "missing";
  actual: string | null;
}

export interface CheckRow {
  check: CheckName;
  position: number | null;
  outcome: "pass" | "fail" | "not_applicable";
  expected: string | null;
  actual: string | null;
}

export type ExtractedValue = string | null;

export interface Extracted {
  supplier_name: ExtractedValue;
  recipient_name: ExtractedValue;
  invoice_number: ExtractedValue;
  issue_date: ExtractedValue;
  due_date: ExtractedValue;
  currency: ExtractedValue;
  subtotal: ExtractedValue;
  tax_total: ExtractedValue;
  grand_total: ExtractedValue;
  tax_rate?: ExtractedValue;
  lines: { line_no: number; description: ExtractedValue; quantity: ExtractedValue; unit_price: ExtractedValue; amount: ExtractedValue }[];
}

export interface DocumentsDocumentDetail {
  kind: "documents";
  document: DocumentRow;
  currency: string | null;
  fields: RequiredFieldRow[];
  checks: CheckRow[];
  extracted: Extracted | null;
  artifacts: string[];
}

export type DocumentDetail = SyntheticDocumentDetail | DocumentsDocumentDetail;

export interface PipelineState {
  services: { service: string; healthy: boolean; failing: string[]; errors: Record<string, string> }[];
  checked_at: string | null;
  waiting: string | null;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  const body = await resp.json().catch(() => null);
  if (!resp.ok) {
    throw new ApiError(resp.status, body?.error?.code ?? "error", body?.error?.message ?? resp.statusText);
  }
  return body as T;
}

export const api = {
  sources: () => request<Source[]>("/api/sources"),
  datasets: () => request<Dataset[]>("/api/datasets"),
  spend: () => request<Spend>("/api/spend"),
  pipeline: () => request<PipelineState>("/api/pipeline"),
  runs: () => request<RunListItem[]>("/api/runs"),
  run: (id: string) => request<RunDetail>(`/api/runs/${id}`),
  startRun: (source: string, dataset: string, parallelism: number) =>
    request<Run>("/api/runs", { method: "POST", body: JSON.stringify({ source, dataset, parallelism }) }),
  control: (id: string, action: "pause" | "resume" | "stop") =>
    request<{ id: string; status: RunStatus }>(`/api/runs/${id}/${action}`, { method: "POST" }),
  documents: (id: string, filters: Record<string, string>) => {
    const q = new URLSearchParams({ limit: "1000", ...filters });
    return request<{ total: number; documents: DocumentRow[] }>(`/api/runs/${id}/documents?${q}`);
  },
  document: (id: string, invoiceId: string) => request<DocumentDetail>(`/api/runs/${id}/documents/${invoiceId}`),
  artifactUrl: (id: string, invoiceId: string, name: string) => `/api/runs/${id}/documents/${invoiceId}/artifacts/${name}`,
};

export const isActive = (s: RunStatus) => s === "running" || s === "paused";

/** Documents that reached a final status. */
export const finishedCount = (c: Counts) => c.passed + c.failed + c.error + (c.cancelled ?? 0) + (c.timeout ?? 0);

/** A 0..1 score as a percentage with one decimal; `–` when null. */
export const score = (v: number | null | undefined) => (v == null ? "–" : `${(100 * v).toFixed(1)}%`);

export const pct = (part: number, total: number) => (total ? `${Math.round((100 * part) / total)}%` : "–");

export const seconds = (s: number | null | undefined) =>
  s == null ? "–" : s < 90 ? `${s.toFixed(1)} s` : `${(s / 60).toFixed(1)} min`;
