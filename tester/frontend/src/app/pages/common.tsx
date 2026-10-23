import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import s from "../app.module.css";

export function StatusBadge({ status }: { status: string }) {
  return <span className={`${s.badge} ${s[`status_${status}`] ?? ""}`}>{status}</span>;
}

/** The pipeline's waiting state (a service paused or down): documents stay queued and continue later. */
export function PipelineBanner({ waiting }: { waiting?: string | null }) {
  const pipeline = useQuery({ queryKey: ["pipeline"], queryFn: api.pipeline, refetchInterval: 5000, enabled: waiting === undefined });
  const reason = waiting === undefined ? pipeline.data?.waiting : waiting;
  if (!reason) return null;
  return (
    <div className={s.banner}>
      <strong>Pipeline waiting</strong> (documents stay queued and continue later): {reason}
    </div>
  );
}
