// Datasets / start run and run history (frontend/specs/overview.md, Screens).
import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, isActive, pct } from "../api";
import { PipelineBanner, StatusBadge } from "./common";
import s from "../app.module.css";

export function HomePage() {
  const datasets = useQuery({ queryKey: ["datasets"], queryFn: api.datasets });
  const spend = useQuery({ queryKey: ["spend"], queryFn: api.spend, refetchInterval: 10_000 });
  const runs = useQuery({
    queryKey: ["runs"],
    queryFn: api.runs,
    refetchInterval: (q) => (q.state.data?.some((r) => isActive(r.run.status)) ? 2000 : false),
  });
  const [dataset, setDataset] = useState("");
  const [parallelism, setParallelism] = useState(4);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const start = useMutation({
    mutationFn: () => api.startRun(dataset || datasets.data![0].name, parallelism),
    onSuccess: (run) => {
      qc.invalidateQueries({ queryKey: ["runs"] });
      navigate(`/runs/${run.id}`);
    },
  });

  const selected = datasets.data?.find((d) => d.name === (dataset || datasets.data?.[0]?.name));
  const spent = Number(spend.data?.spent_usd ?? 0);
  const budget = spend.data?.daily_budget_usd ?? 0;
  const left = budget - spent;

  return (
    <>
      <PipelineBanner />
      <section className={s.card}>
        <h2>Start a run</h2>
        {datasets.isError && <p className={s.error}>Cannot list datasets: {String(datasets.error)}</p>}
        {datasets.data?.length === 0 && <p>No datasets found under the datasets root.</p>}
        {datasets.data && datasets.data.length > 0 && (
          <div className={s.form}>
            <label>
              Dataset
              <select value={dataset || datasets.data[0].name} onChange={(e) => setDataset(e.target.value)}>
                {datasets.data.map((d) => (
                  <option key={d.name} value={d.name}>
                    {d.name} ({d.total_documents} documents)
                  </option>
                ))}
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
                <th>Dataset</th>
                <th>Status</th>
                <th>Progress</th>
                <th>Passed</th>
                <th>Model / prompt</th>
              </tr>
            </thead>
            <tbody>
              {runs.data.map(({ run, counts, models }) => {
                const finished = counts.passed + counts.failed + counts.error + counts.timeout;
                return (
                  <tr key={run.id}>
                    <td>
                      <Link to={`/runs/${run.id}`}>{new Date(run.started_at).toLocaleString()}</Link>
                    </td>
                    <td>{run.dataset}</td>
                    <td>
                      <StatusBadge status={run.status} />
                      {run.overlapped && <span className={s.muted}> overlapped</span>}
                    </td>
                    <td>
                      {finished} / {run.documents_total}
                    </td>
                    <td>{pct(counts.passed, finished)}</td>
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
