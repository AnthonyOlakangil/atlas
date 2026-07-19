const API = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";

export interface SleeveDrift {
  actual: number;
  band: [number, number];
  status: "below" | "in_band" | "above";
  delta_to_band: number;
}

export interface PositionRow {
  ticker: string;
  sleeve: string;
  shares: number;
  cost_basis: number;
  price: number;
  value: number;
  unrealized_pl: number;
  unrealized_pl_pct: number;
  weight: number;
  stale: boolean;
}

export interface Portfolio {
  as_of: string;
  total_value: number;
  settled_cash: number;
  unsettled_cash: number;
  gfv_count: number;
  sleeves: Record<string, SleeveDrift>;
  positions: PositionRow[];
}

export interface Ticket {
  id: number;
  status: string;
  mode: string;
  created_at: string;
  ticker: string;
  action: string;
  sleeve: string;
  order_type: string;
  quantity: number;
  dollar_amount: number;
  limit_price: number | null;
  is_fractional: boolean;
  cash_before: number;
  cash_after: number;
  thesis: string;
  catalyst: string;
  risk: string;
  bear_case: string;
  falsifier: string;
  time_horizon: string;
  sizing_rationale: string;
  portfolio_impact: string;
  learning_note: string;
  exit_stop: number | null;
  exit_target: number | null;
  robinhood_howto: string;
}

export interface Meta {
  broker_mode: "manual" | "connected";
  atlas_mode: string;
}

export interface BriefData {
  date: string;
  kind: string;
  status: string;
  critic_rounds: number;
  markdown: string;
  glossary: Record<string, string>;
}

export interface PulseRow {
  symbol: string;
  label: string;
  price: number | null;
  stale: boolean;
  as_of: string;
}

export interface PerfPoint {
  date: string;
  atlas: number;
  spy: number | null;
}

export interface TrackRecord {
  stats: {
    windows: Record<
      string,
      {
        n: number;
        hit_rate: number | null;
        avg_win_alpha: number | null;
        avg_loss_alpha: number | null;
        avg_alpha: number | null;
      }
    >;
    scored: number;
    max_drawdown_pct: number;
    paper_days_logged: number;
  };
  alpha_histogram: { bin: string; lo: number; count: number }[];
  drawdown: { date: string; drawdown: number }[];
}

async function get<T>(path: string): Promise<T> {
  const resp = await fetch(`${API}${path}`);
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new ApiError(resp.status, body.detail ?? resp.statusText);
  }
  return resp.json();
}

export class ApiError extends Error {
  constructor(
    public status: number,
    detail: string,
  ) {
    super(detail);
  }
}

export const fetchMeta = () => get<Meta>("/api/meta");
export const fetchPortfolio = () => get<Portfolio>("/api/portfolio");
export const fetchTickets = () => get<Ticket[]>("/api/tickets");
export const fetchBrief = () => get<BriefData>("/api/brief/latest");
export const fetchPulse = () => get<{ as_of: string; rows: PulseRow[] }>("/api/market-pulse");
export const fetchPerformance = (window: string) =>
  get<{ window: string; series: PerfPoint[] }>(`/api/performance?window=${window}`);
export const fetchTrackRecord = () => get<TrackRecord>("/api/track-record");

export async function ticketAction(
  id: number,
  action: "approve" | "reject",
): Promise<{ status: string; next_step?: string; note?: string }> {
  const resp = await fetch(`${API}/api/tickets/${id}/${action}`, { method: "POST" });
  const body = await resp.json();
  if (!resp.ok) throw new ApiError(resp.status, body.detail ?? "request failed");
  return body;
}
