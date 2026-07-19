import { useQuery } from "@tanstack/react-query";
import { ResponsiveContainer, Tooltip, Treemap } from "recharts";
import { Empty, Section } from "../components/ui";
import { fetchPortfolio } from "../lib/api";
import { fmtPct, fmtUSD } from "../lib/format";

function heatColor(plPct: number): string {
  if (plPct > 5) return "#059669";
  if (plPct > 0) return "#34d399";
  if (plPct === 0) return "#3f3f46";
  if (plPct > -5) return "#fb7185";
  return "#be123c";
}

export default function SectorHeat() {
  const { data, isLoading } = useQuery({ queryKey: ["portfolio"], queryFn: fetchPortfolio });
  if (isLoading) return <Empty message="Loading…" />;
  if (!data) return <Empty message="Backend unreachable" hint="Run: uv run atlas serve" />;

  const cells = data.positions.map((p) => ({
    name: p.ticker,
    size: Math.max(p.value, 0.01),
    fill: heatColor(p.unrealized_pl_pct),
    pl: p.unrealized_pl_pct,
    value: p.value,
    sleeve: p.sleeve,
  }));

  return (
    <>
      <h1 className="text-xl font-bold">Sector Heat</h1>
      <Section
        title="Holdings treemap"
        caption="Tile area = position weight in the book; color = unrealized P&L % (green up, red down). Day-change coloring is a v2 item once per-ticker prior closes are tracked. Two big tiles of the same color can be the same bet wearing different tickers."
      >
        {cells.length === 0 ? (
          <Empty message="No positions to map yet." />
        ) : (
          <div className="h-80">
            <ResponsiveContainer>
              <Treemap data={cells} dataKey="size" stroke="#18181b" isAnimationActive={false}>
                <Tooltip
                  content={({ payload }) => {
                    const p = payload?.[0]?.payload;
                    if (!p) return null;
                    return (
                      <div className="card text-xs">
                        <div className="font-bold">{p.name}</div>
                        <div>{p.sleeve}</div>
                        <div className="tabular-nums">
                          {fmtUSD(p.value)} · {fmtPct(p.pl)}
                        </div>
                      </div>
                    );
                  }}
                />
              </Treemap>
            </ResponsiveContainer>
          </div>
        )}
      </Section>
    </>
  );
}
