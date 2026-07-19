import { useState } from "react";
import BriefReader from "./views/BriefReader";
import MarketPulse from "./views/MarketPulse";
import Overview from "./views/Overview";
import Performance from "./views/Performance";
import Positions from "./views/Positions";
import SectorHeat from "./views/SectorHeat";
import TicketReview from "./views/TicketReview";
import TrackRecord from "./views/TrackRecord";

const VIEWS = [
  ["overview", "Portfolio Overview", Overview],
  ["performance", "Performance", Performance],
  ["positions", "Positions", Positions],
  ["pulse", "Market Pulse", MarketPulse],
  ["tickets", "Ticket Review", TicketReview],
  ["brief", "Brief Reader", BriefReader],
  ["track", "Track Record", TrackRecord],
  ["heat", "Sector Heat", SectorHeat],
] as const;

export default function App() {
  const [view, setView] = useState<string>("overview");
  const Active = VIEWS.find(([id]) => id === view)?.[2] ?? Overview;
  return (
    <div className="flex min-h-screen">
      <nav className="w-56 shrink-0 border-r border-zinc-800 bg-zinc-950 p-4">
        <h1 className="mb-1 text-lg font-bold text-zinc-100">Atlas</h1>
        <p className="mb-6 text-[11px] leading-snug text-zinc-500">
          Paper-first research agent. Orders execute only via the CLI, never from this page.
        </p>
        <ul className="space-y-1">
          {VIEWS.map(([id, label]) => (
            <li key={id}>
              <button
                onClick={() => setView(id)}
                className={`w-full rounded-lg px-3 py-2 text-left text-sm ${
                  view === id
                    ? "bg-zinc-800 font-semibold text-zinc-100"
                    : "text-zinc-400 hover:bg-zinc-900"
                }`}
              >
                {label}
              </button>
            </li>
          ))}
        </ul>
      </nav>
      <main className="flex-1 space-y-4 p-6">
        <Active />
      </main>
    </div>
  );
}
