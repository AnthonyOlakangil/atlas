import { useQuery } from "@tanstack/react-query";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Empty, Section, StatTile } from "../components/ui";
import { fetchTrackRecord } from "../lib/api";
import { fmtPct } from "../lib/format";

export default function TrackRecord() {
  const { data, isLoading } = useQuery({ queryKey: ["track"], queryFn: fetchTrackRecord });
  if (isLoading) return <Empty message="Loading track record…" />;
  if (!data) return <Empty message="Backend unreachable" hint="Run: uv run atlas serve" />;

  const { stats } = data;
  return (
    <>
      <h1 className="text-xl font-bold">Track Record</h1>
      <p className="text-sm text-zinc-500">
        The honesty mechanism: every executed ticket is scored at +1/+5/+20 trading days against
        SPY. Bad numbers are shown exactly like good ones.
      </p>
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {Object.entries(stats.windows).map(([win, w]) => (
          <StatTile
            key={win}
            label={`${win} hit rate`}
            value={w.hit_rate == null ? "—" : `${(w.hit_rate * 100).toFixed(0)}%`}
            hint={`n=${w.n}; avg alpha ${fmtPct(w.avg_alpha)} (a "hit" beat SPY over the window)`}
            tone={w.hit_rate == null ? "default" : w.hit_rate >= 0.5 ? "good" : "bad"}
          />
        ))}
        <StatTile
          label="Max drawdown"
          value={fmtPct(stats.max_drawdown_pct)}
          hint="Worst peak-to-trough decline of the paper book."
          tone={stats.max_drawdown_pct < -10 ? "bad" : "default"}
        />
        <StatTile
          label="Paper days"
          value={`${stats.paper_days_logged} / 30`}
          hint="LIVE mode stays locked until 30 paper trading days are logged."
        />
      </div>

      {stats.scored === 0 ? (
        <Empty
          message="No scored tickets yet."
          hint="Scores appear once executed tickets mature past their +1d window."
        />
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          <Section
            title="Alpha distribution"
            caption="Each bar counts scored windows by alpha vs SPY. Mass to the right of zero means Atlas added value over the index; mass to the left means the index was the better trade."
          >
            <div className="h-56">
              <ResponsiveContainer>
                <BarChart data={data.alpha_histogram}>
                  <CartesianGrid stroke="#27272a" />
                  <XAxis dataKey="bin" stroke="#71717a" fontSize={10} />
                  <YAxis allowDecimals={false} stroke="#71717a" fontSize={11} />
                  <Tooltip />
                  <Bar dataKey="count" isAnimationActive={false}>
                    {data.alpha_histogram.map((h) => (
                      <Cell key={h.bin} fill={h.lo >= 0 ? "#34d399" : "#fb7185"} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </Section>
          <Section
            title="Drawdown curve"
            caption="How far below its own peak the paper book has been, over time. Deeper and longer valleys mean more pain to hold the strategy."
          >
            <div className="h-56">
              <ResponsiveContainer>
                <AreaChart data={data.drawdown}>
                  <CartesianGrid stroke="#27272a" />
                  <XAxis dataKey="date" stroke="#71717a" fontSize={10} />
                  <YAxis stroke="#71717a" fontSize={11} tickFormatter={(v) => `${v}%`} />
                  <Tooltip formatter={(v: number) => `${v.toFixed(2)}%`} />
                  <Area type="monotone" dataKey="drawdown" stroke="#fb7185" fill="#fb718533" isAnimationActive={false} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </Section>
        </div>
      )}
    </>
  );
}
