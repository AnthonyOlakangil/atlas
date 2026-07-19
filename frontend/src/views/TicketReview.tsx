import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Empty, StatusChip, Timestamp } from "../components/ui";
import { ApiError, fetchTickets, ticketAction, type Ticket } from "../lib/api";
import { fmtShares, fmtUSD } from "../lib/format";

function Field(props: { label: string; children: React.ReactNode }) {
  return (
    <div className="text-sm">
      <span className="font-semibold text-zinc-300">{props.label}: </span>
      <span className="text-zinc-400">{props.children}</span>
    </div>
  );
}

function TicketCard({ ticket }: { ticket: Ticket }) {
  const qc = useQueryClient();
  const [nextStep, setNextStep] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const act = useMutation({
    mutationFn: (action: "approve" | "reject") => ticketAction(ticket.id, action),
    onSuccess: (body) => {
      setError(null);
      if (body.next_step) setNextStep(body.next_step);
      qc.invalidateQueries({ queryKey: ["tickets"] });
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "request failed"),
  });

  const orderLine = ticket.is_fractional
    ? `${fmtShares(ticket.quantity)} shares (fractional) — MARKET order, forced: Robinhood forbids fractional limit orders, so this fill has no price cap`
    : `${fmtShares(ticket.quantity)} whole share(s) — ${ticket.order_type.toUpperCase()} at ${fmtUSD(ticket.limit_price)}`;

  return (
    <article className="card space-y-2">
      <header className="flex items-center justify-between">
        <h3 className="text-base font-bold">
          #{ticket.id} {ticket.action} {ticket.ticker} — {fmtUSD(ticket.dollar_amount)}{" "}
          <span className="text-sm font-normal text-zinc-500">({ticket.sleeve}, {ticket.mode})</span>
        </h3>
        <div className="flex items-center gap-2">
          <StatusChip status={ticket.status} />
          <Timestamp iso={ticket.created_at} />
        </div>
      </header>

      <Field label="Order">{orderLine}</Field>
      <Field label="Cash check">
        <span className="tabular-nums">
          {fmtUSD(ticket.cash_before)} settled → {fmtUSD(ticket.dollar_amount)} this trade →{" "}
          {fmtUSD(ticket.cash_after)} remaining
        </span>
      </Field>
      <Field label="Thesis">{ticket.thesis}</Field>
      <Field label="Catalyst">{ticket.catalyst}</Field>
      <Field label="Risk">{ticket.risk}</Field>
      <Field label="Bear case">{ticket.bear_case}</Field>
      <Field label="What would falsify this">{ticket.falsifier}</Field>
      <Field label="Time horizon">{ticket.time_horizon}</Field>
      <Field label="Sizing rationale">{ticket.sizing_rationale}</Field>
      <Field label="Portfolio impact">{ticket.portfolio_impact}</Field>
      <Field label="Exit plan">
        {ticket.exit_stop != null
          ? `stop ${fmtUSD(ticket.exit_stop)} / target ${fmtUSD(ticket.exit_target)} — app-managed: only enforced while Atlas is running`
          : "band-rebalanced holding; the falsifier defines the exit"}
      </Field>
      <Field label="Learning note">{ticket.learning_note}</Field>

      {ticket.status === "PENDING_APPROVAL" && (
        <div className="flex gap-2 pt-2">
          <button
            onClick={() => act.mutate("approve")}
            className="rounded bg-emerald-800 px-4 py-1.5 text-sm font-semibold text-emerald-100 hover:bg-emerald-700"
          >
            Approve
          </button>
          <button
            onClick={() => act.mutate("reject")}
            className="rounded bg-rose-900 px-4 py-1.5 text-sm font-semibold text-rose-100 hover:bg-rose-800"
          >
            Reject
          </button>
        </div>
      )}
      {nextStep && (
        <div className="rounded-lg border border-sky-800 bg-sky-950/60 p-3 text-sm">
          <p className="font-semibold text-sky-300">Approved — nothing has been executed.</p>
          <p className="mt-1 text-zinc-300">
            Execution happens only in your terminal. Run:
          </p>
          <code className="mt-1 block rounded bg-zinc-900 p-2 text-sky-200">{nextStep}</code>
          <p className="caption">The browser can never place an order — by design.</p>
        </div>
      )}
      {error && <p className="text-sm text-rose-400">{error}</p>}
    </article>
  );
}

export default function TicketReview() {
  const { data, isLoading } = useQuery({ queryKey: ["tickets"], queryFn: fetchTickets });
  if (isLoading) return <Empty message="Loading tickets…" />;
  if (!data) return <Empty message="Backend unreachable" hint="Run: uv run atlas serve" />;

  return (
    <>
      <h1 className="text-xl font-bold">Ticket Review</h1>
      <p className="text-sm text-zinc-500">
        Approving marks a ticket ready for execution and hands you the CLI command — orders are
        never fired from the browser.
      </p>
      {data.length === 0 ? (
        <Empty message="No tickets yet." hint="Run `uv run atlas cycle` to generate proposals." />
      ) : (
        data.map((t) => <TicketCard key={t.id} ticket={t} />)
      )}
    </>
  );
}
