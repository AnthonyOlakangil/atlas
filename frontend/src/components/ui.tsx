import type { ReactNode } from "react";
import { fmtTime } from "../lib/format";

export function StatTile(props: {
  label: string;
  value: string;
  hint?: string;
  tone?: "default" | "good" | "bad" | "warn";
}) {
  const tone =
    props.tone === "good"
      ? "text-emerald-400"
      : props.tone === "bad"
        ? "text-rose-400"
        : props.tone === "warn"
          ? "text-amber-400"
          : "text-zinc-100";
  return (
    <div className="card" title={props.hint}>
      <div className="text-xs uppercase tracking-wide text-zinc-500">{props.label}</div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums ${tone}`}>{props.value}</div>
      {props.hint && <div className="caption">{props.hint}</div>}
    </div>
  );
}

export function Section(props: { title: string; caption: string; children: ReactNode }) {
  return (
    <section className="card">
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-zinc-400">
        {props.title}
      </h2>
      {props.children}
      <p className="caption">{props.caption}</p>
    </section>
  );
}

export function StaleBadge(props: { asOf?: string }) {
  return (
    <span
      className="ml-2 rounded bg-amber-900/60 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-amber-300"
      title={props.asOf ? `last good data: ${fmtTime(props.asOf)}` : "no fresh data available"}
    >
      stale
    </span>
  );
}

const CHIP: Record<string, string> = {
  PENDING_APPROVAL: "bg-amber-900/60 text-amber-300",
  APPROVED: "bg-sky-900/60 text-sky-300",
  EXECUTED: "bg-emerald-900/60 text-emerald-300",
  REJECTED: "bg-rose-900/60 text-rose-300",
  EXPIRED: "bg-zinc-800 text-zinc-400",
  FAILED: "bg-rose-900/60 text-rose-300",
};

export function StatusChip(props: { status: string }) {
  return (
    <span
      className={`rounded px-2 py-0.5 text-[11px] font-semibold ${CHIP[props.status] ?? "bg-zinc-800 text-zinc-300"}`}
    >
      {props.status}
    </span>
  );
}

export function Empty(props: { message: string; hint?: string }) {
  return (
    <div className="card text-center text-zinc-400">
      <p>{props.message}</p>
      {props.hint && <p className="caption">{props.hint}</p>}
    </div>
  );
}

export function Timestamp(props: { iso: string }) {
  return <span className="text-[11px] text-zinc-500">as of {fmtTime(props.iso)}</span>;
}
