import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Empty, Section } from "../components/ui";
import { fetchPerformance } from "../lib/api";

const WINDOWS = ["1D", "1W", "1M", "3M", "YTD", "ALL"];

export default function Performance() {
  const [window, setWindow] = useState("ALL");
  const { data, isLoading } = useQuery({
    queryKey: ["performance", window],
    queryFn: () => fetchPerformance(window),
  });

  return (
    <>
      <h1 className="text-xl font-bold">Performance</h1>
      <div className="flex gap-2">
        {WINDOWS.map((w) => (
          <button
            key={w}
            onClick={() => setWindow(w)}
            className={`rounded px-3 py-1 text-xs font-semibold ${
              window === w ? "bg-sky-800 text-sky-100" : "bg-zinc-800 text-zinc-400"
            }`}
          >
            {w}
          </button>
        ))}
      </div>
      <Section
        title={`Cumulative return — Atlas vs SPY (${window})`}
        caption="Both lines start at 0% at the window's first snapshot. SPY is the benchmark Atlas is trying to beat — if the blue line sits below it, the index was the better choice over this window."
      >
        {isLoading ? (
          <Empty message="Loading…" />
        ) : !data || data.series.length < 2 ? (
          <Empty
            message="Not enough snapshots yet."
            hint="One snapshot is written per daily cycle — check back after a couple of days."
          />
        ) : (
          <div className="h-72">
            <ResponsiveContainer>
              <LineChart data={data.series}>
                <CartesianGrid stroke="#27272a" />
                <XAxis dataKey="date" stroke="#71717a" fontSize={11} />
                <YAxis stroke="#71717a" fontSize={11} tickFormatter={(v) => `${v}%`} />
                <Tooltip formatter={(v: number) => `${v.toFixed(2)}%`} />
                <Legend />
                <Line type="monotone" dataKey="atlas" name="Atlas (paper)" stroke="#38bdf8" dot={false} isAnimationActive={false} />
                <Line type="monotone" dataKey="spy" name="SPY" stroke="#a1a1aa" dot={false} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </Section>
    </>
  );
}
