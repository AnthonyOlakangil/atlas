import { useQuery } from "@tanstack/react-query";
import { Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { Empty, Section, StatTile, Timestamp } from "../components/ui";
import { fetchPortfolio } from "../lib/api";
import { fmtUSD, fmtWeight } from "../lib/format";

const SLEEVE_COLORS: Record<string, string> = {
  core: "#38bdf8",
  satellite_ai_semis: "#a78bfa",
  diversifier: "#fbbf24",
  speculative: "#fb7185",
  cash: "#3f3f46",
};

const STATUS_TONE: Record<string, string> = {
  below: "text-amber-400",
  in_band: "text-emerald-400",
  above: "text-rose-400",
};

export default function Overview() {
  const { data, isLoading, error } = useQuery({ queryKey: ["portfolio"], queryFn: fetchPortfolio });
  if (isLoading) return <Empty message="Loading portfolio…" />;
  if (error || !data) return <Empty message="Backend unreachable" hint="Run: uv run atlas serve" />;

  const invested = data.positions.reduce((s, p) => s + p.value, 0);
  const cash = data.settled_cash + data.unsettled_cash;
  const pie = [
    ...Object.entries(data.sleeves)
      .map(([name, s]) => ({ name, value: s.actual * data.total_value }))
      .filter((r) => r.value > 0),
    ...(cash > 0 ? [{ name: "cash", value: cash }] : []),
  ];

  return (
    <>
      <div className="flex items-baseline justify-between">
        <h1 className="text-xl font-bold">Portfolio Overview</h1>
        <Timestamp iso={data.as_of} />
      </div>
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatTile label="Total value" value={fmtUSD(data.total_value)} />
        <StatTile
          label="Settled cash"
          value={fmtUSD(data.settled_cash)}
          hint="Spendable now. Buys are only ever funded from settled cash (T+1 rule)."
        />
        <StatTile
          label="Unsettled cash"
          value={fmtUSD(data.unsettled_cash)}
          hint="Sale proceeds waiting for T+1 settlement — not spendable yet."
          tone={data.unsettled_cash > 0 ? "warn" : "default"}
        />
        <StatTile
          label="GFV count"
          value={String(data.gfv_count)}
          hint="Good Faith Violations in the last 12 months. Three trigger a 90-day broker restriction."
          tone={data.gfv_count > 0 ? "bad" : "good"}
        />
      </div>

      <Section
        title="Allocation vs. target bands"
        caption="The pie shows where the money actually is; the table compares each sleeve to its target band from operator.yaml. Drift outside a band is a rebalancing signal, not an emergency."
      >
        {invested === 0 && cash === 0 ? (
          <Empty message="No positions or cash yet." hint="Fund the paper book and run `uv run atlas cycle`." />
        ) : (
          <div className="grid gap-4 md:grid-cols-2">
            <div className="h-64">
              <ResponsiveContainer>
                <PieChart>
                  <Pie data={pie} dataKey="value" nameKey="name" innerRadius={50} outerRadius={90}>
                    {pie.map((entry) => (
                      <Cell key={entry.name} fill={SLEEVE_COLORS[entry.name] ?? "#71717a"} />
                    ))}
                  </Pie>
                  <Tooltip formatter={(v: number) => fmtUSD(v)} />
                  <Legend />
                </PieChart>
              </ResponsiveContainer>
            </div>
            <table className="w-full self-center text-sm">
              <thead>
                <tr className="text-left text-xs uppercase text-zinc-500">
                  <th className="py-1">Sleeve</th>
                  <th className="num">Actual</th>
                  <th className="num">Band</th>
                  <th className="num">Status</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(data.sleeves).map(([name, s]) => (
                  <tr key={name} className="border-t border-zinc-800">
                    <td className="py-1.5">{name}</td>
                    <td className="num">{fmtWeight(s.actual)}</td>
                    <td className="num text-zinc-400">
                      {fmtWeight(s.band[0])}–{fmtWeight(s.band[1])}
                    </td>
                    <td className={`num font-semibold ${STATUS_TONE[s.status]}`}>
                      {s.status.replace("_", " ")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </>
  );
}
