import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { vi } from "vitest";

export function renderWithQuery(ui: ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

type Route = { match: RegExp; status?: number; body: unknown };

export function mockFetch(routes: Route[]) {
  const fn = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const route = routes.find((r) => r.match.test(url));
    if (!route) throw new Error(`no mock for ${url}`);
    return {
      ok: (route.status ?? 200) < 400,
      status: route.status ?? 200,
      json: async () => route.body,
      statusText: "mock",
    } as Response;
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

export const TICKET = {
  id: 7,
  status: "PENDING_APPROVAL",
  mode: "PAPER",
  created_at: "2026-07-18T13:30:00+00:00",
  ticker: "QQQM",
  action: "BUY",
  sleeve: "satellite_ai_semis",
  order_type: "market",
  quantity: 0.2386,
  dollar_amount: 55.0,
  limit_price: null,
  is_fractional: true,
  cash_before: 250.0,
  cash_after: 195.0,
  thesis: "Broad growth exposure at a lower fee while chips digest.",
  catalyst: "Q2 earnings wave starting Jul 14",
  risk: "rate-driven multiple compression",
  bear_case: "AI capex disappoints and growth multiples derate",
  falsifier: "hyperscaler capex guides down",
  time_horizon: "months",
  sizing_rationale: "within sleeve band and settled cash",
  portfolio_impact: "satellite sleeve 0% → 22% (in band); no overlap; settled cash $250 → $195",
  learning_note: "Fractional shares trade as market orders only on Robinhood.",
  exit_stop: 212.06,
  exit_target: 276.6,
  robinhood_howto:
    'Open Robinhood → Search "QQQM" → **Trade** → **Buy**. Robinhood switches to **Buy in Dollars** for $55.00 — a **market** order with no price field.',
};

export const META_MANUAL = { broker_mode: "manual", atlas_mode: "PAPER" };
export const META_CONNECTED = { broker_mode: "connected", atlas_mode: "PAPER" };
