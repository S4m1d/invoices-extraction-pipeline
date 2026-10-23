// Datasets / start run and run history (frontend/specs/overview.md, Screens).
import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, finishedCount, isActive, KIND_LABEL, pct, score, type Dataset, type Kind } from "../api";
import { PipelineBanner, StatusBadge } from "./common";
import s from "../app.module.css";

export function HomePage() {
  const sources = useQuery({ queryKey: ["sources"], queryFn: api.sources });
  const datasets = useQuery({ queryKey: ["datasets"], queryFn: api.datasets });
  const spend = useQuery({ queryKey: ["spend"], queryFn: api.spend, refetchInterval: 10_000 });
  const runs = useQuery({
    queryKey: ["runs"],
    queryFn: api.runs,
    refetchInterval: (q) => (q.state.data?.some((r) => isActive(r.run.status)) ? 2000 : false),
  });
  const [kind, setKind] = useState<Kind>("synthetic");
  // "source/dataset"; empty selects the first runnable dataset of the kind
  const [choice, setChoice] = useState("");
  const [parallelism, setParallelism] = useState(4);
  const navigate = useNavigate();
  const qc = useQueryClient();

  const key = (d: Dataset) => `${d.source}/${d.name}`;
  const ofKind = datasets.data?.filter((d) => d.kind === kind) ?? [];
  const runnable = ofKind.filter((d) => !d.error);
  const selected = runnable.find((d) => key(d) === choice) ?? runnable[0];
  const kindSources = sources.data?.filter((src) => src.kind === kind) ?? [];

  const start = useMutation({
    mutationFn: () => api.startRun(selected!.source, selected!.name, parallelism),
    onSuccess: (run) => {
      qc.invalidateQueries({ queryKey: ["runs"] });
      navigate(`/runs/${run.id}`);
    },
  });

  const spent = Number(spend.data?.spent_usd ?? 0);
  const budget = spend.data?.daily_budget_usd ?? 0;
  const left = budget - spent;

  return (
    <>
      <PipelineBanner />
      <section className={s.card}>
        <h2>Start a run</h2>
        <div className={s.tabs} role="tablist">
          {(["synthetic", "documents"] as const).map((k) => (
            <button
              key={k}
              role="tab"
              aria-selected={kind === k}
              className={kind === k ? s.tabActive : s.tab}
              onClick={() => {
                setKind(k);
                setChoice("");
              }}
            >
              {KIND_LABEL[k]}
            </button>
          ))}
        </div>
        <p className={s.muted}>
          {kind === "synthetic"
            ? "Results are compared with the dataset's golden set."
            : "No golden set: a document fails on a pipeline error, a missing required field, or totals that do not add up."}
        </p>
        {(sources.isError || datasets.isError) && <p className={s.error}>Cannot list datasets: {String(sources.error ?? datasets.error)}</p>}
        {sources.data && kindSources.length === 0 && <p>No {kind} data source is configured (infra/tester-sources.yaml).</p>}
        {kindSources
          .filter((src) => !src.available)
          .map((src) => (
            <p key={src.name} className={s.warn}>
              Source {src.name} is unavailable: {src.reason}
            </p>
          ))}
        {ofKind
          .filter((d) => d.error)
          .map((d) => (
            <p key={key(d)} className={s.warn}>
              {key(d)} cannot be run: {d.error}
            </p>
          ))}
        {datasets.data && kindSources.some((src) => src.available) && runnable.length === 0 && <p>No datasets found in the {kind} sources.</p>}
        {selected && (
          <div className={s.form}>
            <label>
              Dataset
              <select value={key(selected)} onChange={(e) => setChoice(e.target.value)}>
                {kindSources.map((src) => {
                  const list = runnable.filter((d) => d.source === src.name);
                  return (
                    list.length > 0 && (
                      <optgroup key={src.name} label={src.name}>
                        {list.map((d) => (
                          <option key={key(d)} value={key(d)}>
                            {d.name} ({d.total_documents} documents)
                          </option>
                        ))}
                      </optgroup>
                    )
                  );
                })}
              </select>
            </label>
            <label>
              Parallel uploads
              <input
                type="number"
                min={1}
                max={32}
                value={parallelism}
                onChange={(e) => setParallelism(Math.max(1, Math.min(32, Number(e.target.value) || 1)))}
              />
            </label>
            <button disabled={start.isPending} onClick={() => start.mutate()}>
              Start run
            </button>
          </div>
        )}
        {selected && spend.data && (
          <p className={s.muted}>
            Estimated LLM cost: ${selected.estimated_cost_usd.toFixed(2)} ({selected.total_documents} documents × $
            {spend.data.cost_per_document_usd}). Today's spend: ${spent.toFixed(4)} of ${budget.toFixed(2)} daily budget
            {selected.estimated_cost_usd > left && (
              <strong className={s.warn}> — more than the remaining budget; the pipeline will pause midway.</strong>
            )}
            . No cost when the stack runs with the fake LLM.
          </p>
        )}
        {start.isError && <p className={s.error}>{String(start.error)}</p>}
      </section>

      <section className={s.card}>
        <h2>Runs</h2>
        {runs.data?.length === 0 && <p>No runs yet.</p>}
        {runs.data && runs.data.length > 0 && (
          <table className={s.table}>
            <thead>
              <tr>
                <th>Started</th>
                <th>Kind</th>
                <th>Dataset</th>
                <th>Status</th>
                <th>Progress</th>
                <th>Passed</th>
                <th>Header fields</th>
                <th>Line fields</th>
                <th>Model / prompt</th>
              </tr>
            </thead>
            <tbody>
              {runs.data.map(({ run, counts, models, metrics }) => {
                const finished = finishedCount(counts);
                return (
                  <tr key={run.id}>
                    <td>
                      <Link to={`/runs/${run.id}`}>{new Date(run.started_at).toLocaleString()}</Link>
                    </td>
                    <td>{run.kind === "documents" ? "real" : "synthetic"}</td>
                    <td>
                      {run.source}/{run.dataset}
                    </td>
                    <td>
                      <StatusBadge status={run.status} />
                      {run.overlapped && <span className={s.muted}> overlapped</span>}
                    </td>
                    <td>
                      {finished} / {run.documents_total}
                    </td>
                    <td>{pct(counts.passed, finished)}</td>
                    <td>{score(metrics?.header.accuracy)}</td>
                    <td>{score(metrics?.line.accuracy)}</td>
                    <td>{models?.map((m) => `${m.model} ${m.prompt ?? ""}`).join(", ") || "–"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>
    </>
  );
}
