import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import BriefReader, { annotate } from "../views/BriefReader";
import MarketPulse from "../views/MarketPulse";
import Positions from "../views/Positions";
import TicketReview from "../views/TicketReview";
import { META_CONNECTED, META_MANUAL, TICKET, mockFetch, renderWithQuery } from "./helpers";

const EMPTY_PORTFOLIO = {
  as_of: "2026-07-18T13:30:00+00:00",
  total_value: 0,
  settled_cash: 0,
  unsettled_cash: 0,
  gfv_count: 0,
  sleeves: {},
  positions: [],
};

describe("TicketReview", () => {
  it("renders every required ticket field including bear case and portfolio impact", async () => {
    mockFetch([
      { match: /\/api\/meta/, body: META_CONNECTED },
      { match: /\/api\/tickets$/, body: [TICKET] },
    ]);
    renderWithQuery(<TicketReview />);
    await screen.findByText(/#7 BUY QQQM/);
    expect(screen.getByText(/Bear case/)).toBeInTheDocument();
    expect(screen.getByText(/AI capex disappoints/)).toBeInTheDocument();
    expect(screen.getByText(/What would falsify this/)).toBeInTheDocument();
    expect(screen.getByText(/Portfolio impact/)).toBeInTheDocument();
    expect(screen.getByText(/satellite sleeve 0% → 22%/)).toBeInTheDocument();
    expect(screen.getByText(/\$250\.00 settled → \$55\.00 this trade/)).toBeInTheDocument();
    expect(screen.getByText(/MARKET order, forced/)).toBeInTheDocument();
  });

  it("approve (connected) shows the CLI next step and never claims execution happened", async () => {
    mockFetch([
      { match: /\/api\/meta/, body: META_CONNECTED },
      {
        match: /\/api\/tickets\/7\/approve/,
        body: {
          status: "APPROVED",
          next_step: "uv run atlas execute --ticket-id 7",
          note: "Execution is CLI-only by design — the browser can never place an order.",
        },
      },
      { match: /\/api\/tickets$/, body: [TICKET] },
    ]);
    renderWithQuery(<TicketReview />);
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    await screen.findByText(/nothing has been executed/i);
    expect(screen.getByText("uv run atlas execute --ticket-id 7")).toBeInTheDocument();
    expect(screen.getByText(/browser can never place an order/i)).toBeInTheDocument();
    expect(screen.queryByTestId("manual-howto")).not.toBeInTheDocument();
  });

  it("manual mode renders the place-it-yourself panel with the app path", async () => {
    mockFetch([
      { match: /\/api\/meta/, body: META_MANUAL },
      { match: /\/api\/tickets$/, body: [TICKET] },
    ]);
    renderWithQuery(<TicketReview />);
    await screen.findByText(/#7 BUY QQQM/);
    const panel = await screen.findByTestId("manual-howto");
    expect(panel).toHaveTextContent(/place this yourself in Robinhood/i);
    expect(panel).toHaveTextContent(/Buy in Dollars/);
    expect(panel).toHaveTextContent(/atlas filled --ticket-id 7/);
    expect(screen.getByText(/place trades yourself in Robinhood/i)).toBeInTheDocument();
  });
});

describe("Positions", () => {
  it("stale price renders the stale badge and hides the number", async () => {
    mockFetch([
      {
        match: /\/api\/portfolio/,
        body: {
          ...EMPTY_PORTFOLIO,
          total_value: 100,
          positions: [
            {
              ticker: "VOO", sleeve: "core", shares: 0.5, cost_basis: 100, price: 0,
              value: 0, unrealized_pl: -100, unrealized_pl_pct: -100, weight: 0, stale: true,
            },
          ],
        },
      },
    ]);
    renderWithQuery(<Positions />);
    await screen.findByText("VOO");
    expect(screen.getByText("stale")).toBeInTheDocument();
  });

  it("empty book renders guidance, not a blank table", async () => {
    mockFetch([{ match: /\/api\/portfolio/, body: EMPTY_PORTFOLIO }]);
    renderWithQuery(<Positions />);
    await screen.findByText(/No positions yet/);
    expect(screen.getByText(/execute a ticket/i)).toBeInTheDocument();
  });
});

describe("MarketPulse", () => {
  it("null price renders unavailable instead of a fake number", async () => {
    mockFetch([
      {
        match: /\/api\/market-pulse/,
        body: {
          as_of: "2026-07-18T13:30:00+00:00",
          rows: [
            { symbol: "SPY", label: "S&P 500 ETF", price: 748.3, stale: false, as_of: "2026-07-18T13:30:00+00:00" },
            { symbol: "^VIX", label: "VIX (fear gauge)", price: null, stale: true, as_of: "2026-07-18T13:30:00+00:00" },
          ],
        },
      },
    ]);
    renderWithQuery(<MarketPulse />);
    await screen.findByText("SPY");
    expect(screen.getByText("$748.30")).toBeInTheDocument();
    expect(screen.getByText("unavailable")).toBeInTheDocument();
  });
});

describe("BriefReader", () => {
  it("marks glossary terms with hover tooltips", async () => {
    mockFetch([
      {
        match: /\/api\/brief\/latest/,
        body: {
          date: "2026-07-18", kind: "preopen", status: "APPROVED", critic_rounds: 1,
          markdown: "# Brief\n\nUse a limit order for whole shares.",
          glossary: { "limit order": "An order that fills at your price or better." },
        },
      },
    ]);
    renderWithQuery(<BriefReader />);
    const term = await screen.findByTestId("glossary-term");
    expect(term).toHaveTextContent("limit order");
    expect(term).toHaveAttribute("title", "An order that fills at your price or better.");
  });

  it("404 renders the run-cycle empty state", async () => {
    mockFetch([{ match: /\/api\/brief\/latest/, status: 404, body: { detail: "No brief yet" } }]);
    renderWithQuery(<BriefReader />);
    await waitFor(() => expect(screen.getByText(/No brief yet/)).toBeInTheDocument());
    expect(screen.getByText(/uv run atlas cycle/)).toBeInTheDocument();
  });

  it("annotate splits and wraps only known terms", () => {
    const nodes = annotate("check the expense ratio today", { "expense ratio": "the fund fee" });
    expect(nodes.length).toBeGreaterThan(1);
  });
});
