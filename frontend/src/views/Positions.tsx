import { useQuery } from "@tanstack/react-query";
import { Empty, Section, StaleBadge, Timestamp } from "../components/ui";
import { fetchPortfolio } from "../lib/api";
import { fmtPct, fmtShares, fmtUSD, fmtWeight, plColor } from "../lib/format";

export default function Positions() {
  const { data, isLoading } = useQuery({ queryKey: ["portfolio"], queryFn: fetchPortfolio });
  if (isLoading) return <Empty message="Loading positions…" />;
  if (!data) return <Empty message="Backend unreachable" hint="Run: uv run atlas serve" />;

  return (
    <>
      <div className="flex items-baseline justify-between">
        <h1 className="text-xl font-bold">Positions</h1>
        <Timestamp iso={data.as_of} />
      </div>
      <Section
        title="Open paper positions"
        caption="Cost basis is total dollars paid. Unrealized P&L is what you'd have if you sold at the shown price — a stale badge means that price could not be refreshed and the row should not be trusted."
      >
        {data.positions.length === 0 ? (
          <Empty message="No positions yet." hint="Approve and execute a ticket to open one." />
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase text-zinc-500">
                <th className="py-1">Ticker</th>
                <th>Sleeve</th>
                <th className="num">Shares</th>
                <th className="num">Cost basis</th>
                <th className="num">Price</th>
                <th className="num">Value</th>
                <th className="num">P&L $</th>
                <th className="num">P&L %</th>
                <th className="num">Weight</th>
              </tr>
            </thead>
            <tbody>
              {data.positions.map((p) => (
                <tr key={p.ticker} className="border-t border-zinc-800">
                  <td className="py-1.5 font-semibold">
                    {p.ticker}
                    {p.stale && <StaleBadge />}
                  </td>
                  <td className="text-zinc-400">{p.sleeve}</td>
                  <td className="num">{fmtShares(p.shares)}</td>
                  <td className="num">{fmtUSD(p.cost_basis)}</td>
                  <td className="num">{p.stale ? "—" : fmtUSD(p.price)}</td>
                  <td className="num">{fmtUSD(p.value)}</td>
                  <td className={`num ${plColor(p.unrealized_pl)}`}>{fmtUSD(p.unrealized_pl)}</td>
                  <td className={`num ${plColor(p.unrealized_pl)}`}>{fmtPct(p.unrealized_pl_pct)}</td>
                  <td className="num">{fmtWeight(p.weight)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>
    </>
  );
}
