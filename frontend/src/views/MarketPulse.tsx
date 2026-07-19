import { useQuery } from "@tanstack/react-query";
import { Empty, Section, Timestamp } from "../components/ui";
import { fetchPulse } from "../lib/api";
import { fmtUSD } from "../lib/format";

export default function MarketPulse() {
  const { data, isLoading } = useQuery({ queryKey: ["pulse"], queryFn: fetchPulse });
  if (isLoading) return <Empty message="Loading market pulse…" />;
  if (!data) return <Empty message="Backend unreachable" hint="Run: uv run atlas serve" />;

  return (
    <>
      <div className="flex items-baseline justify-between">
        <h1 className="text-xl font-bold">Market Pulse</h1>
        <Timestamp iso={data.as_of} />
      </div>
      <Section
        title="The macro dashboard"
        caption="Index ETFs stand in for their indexes. ^TNX prints the 10-year yield ×10 (45.7 = 4.57%). The VIX above ~20 signals elevated fear; oil and gold move on inflation and geopolitics; BTC is the risk-appetite thermometer."
      >
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
          {data.rows.map((r) => (
            <div key={r.symbol} className="card" title={`retrieved ${r.as_of}`}>
              <div className="flex items-baseline justify-between">
                <span className="text-xs font-semibold text-zinc-400">{r.symbol}</span>
              </div>
              <div className="mt-1 text-xl font-semibold tabular-nums">
                {r.price == null ? (
                  <span className="text-sm text-zinc-500">unavailable</span>
                ) : (
                  fmtUSD(r.price)
                )}
              </div>
              <div className="caption">{r.label}</div>
            </div>
          ))}
        </div>
      </Section>
    </>
  );
}
