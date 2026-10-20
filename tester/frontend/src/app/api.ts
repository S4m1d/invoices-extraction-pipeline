// Backend API client and types (tester/backend/specs/api.md).

export type RunStatus = "running" | "paused" | "stopped" | "completed";
export type DocStatus = "pending" | "uploaded" | "passed" | "failed" | "error" | "timeout";

export interface Dataset {
  name: string;
  total_documents: number;
  total_units: number;
  created_at: string | null;
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
  dataset: string;
  parallelism: number;
  status: RunStatus;
  documents_total: number;
  started_at: string;
  finished_at: string | null;
  overlapped: boolean;
}

export type Counts = Record<DocStatus, number>;

export interface FieldStat {
  field: string;
  line_field: boolean;
  match: number;
  mismatch: number;
  missing: number;
  unexpected: number;
}

export interface Breakdown {
  value: string | null;
  documents: number;
  passed: number;
  field_match: number;
  field_total: number;
}

export interface Summary {
  documents: { total: number; passed: number; failed: number; error: number; timeout: number; unfinished: number };
  overlapped: boolean;
  fields: FieldStat[];
  lines: { compared: number; missing: number; extra: number };
  by_template: Breakdown[];
  by_currency: Breakdown[];
  by_language: Breakdown[];
  errors: { stage: string; code: string; documents: number }[];
  models: { model: string; prompt: string | null; documents: number }[];
  durations: { median_s: number | null; p95_s: number | null; max_s: number | null } | null;
  wall_clock_s: number | null;
}

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

export interface DocumentDetail {
  document: DocumentRow;
  template_id: string | null;
  currency: string | null;
  language: string | null;
  fields: FieldRow[];
  lines: LineRow[];
  artifacts: string[];
}

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
  datasets: () => request<Dataset[]>("/api/datasets"),
  spend: () => request<Spend>("/api/spend"),
  pipeline: () => request<PipelineState>("/api/pipeline"),
  runs: () => request<RunListItem[]>("/api/runs"),
  run: (id: string) => request<RunDetail>(`/api/runs/${id}`),
  startRun: (dataset: string, parallelism: number) =>
    request<Run>("/api/runs", { method: "POST", body: JSON.stringify({ dataset, parallelism }) }),
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

export const pct = (part: number, total: number) => (total ? `${Math.round((100 * part) / total)}%` : "–");

export const seconds = (s: number | null | undefined) =>
  s == null ? "–" : s < 90 ? `${s.toFixed(1)} s` : `${(s / 60).toFixed(1)} min`;
