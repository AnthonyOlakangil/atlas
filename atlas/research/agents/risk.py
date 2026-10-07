"""The portfolio-risk agent — deterministic code, not LLM (§6).

Risk enforcement lives in code so it cannot be argued out of its caps. It is
the only agent that can veto an idea. It enforces bands and caps against
whatever instrument research selected — never the seed ticker lists (§2.0) —
and reasons portfolio-first (§2.0.1).
"""

from __future__ import annotations

import copy
from decimal import ROUND_DOWN, Decimal

from atlas.core.format import money
from atlas.core.logging import get_logger
from atlas.core.settings import OperatorConfig
from atlas.portfolio.drift import BandStatus, compute_drift
from atlas.portfolio.sizing import SECTOR_MAP, size_buy
from atlas.portfolio.state import PortfolioState, PositionView
from atlas.portfolio.validate import TicketDraft, validate_ticket
from atlas.research.agents.bands import (
    breach_anomalies,
    breach_explanation,
    find_unaddressed_breaches,
)
from atlas.research.agents.schemas import (
    ApprovedIdea,
    ResearchBundle,
    RiskAssessment,
    TradeIdea,
    Veto,
)

log = get_logger("research.risk")

MAX_TICKETS_PER_DAY = 3  # §12: force prioritization


def _apply_fill(
    work: PortfolioState,
    *,
    ticker: str,
    sleeve: str,
    is_buy: bool,
    shares: Decimal,
    dollars: Decimal,
    price: Decimal,
) -> None:
    """Update the working state so later proposals in the SAME cycle see this trade.

    A buy spends settled cash and adds equity; a sell/trim removes equity and adds
    UNSETTLED proceeds (which cannot fund a same-cycle buy — T+1, §2.1). This is what
    makes the day's ticket set JOINTLY fundable rather than each ticket independently
    sized against the same starting cash.
    """
    pos = next((p for p in work.positions if p.ticker == ticker), None)
    if is_buy:
        work.cash.settled = work.cash.settled - dollars
        if pos is None:
            work.positions.append(
                PositionView(
                    ticker=ticker, sleeve=sleeve, shares=shares, cost_basis=dollars,
                    price=price, value=(shares * price).quantize(Decimal("0.01")),
                )
            )
        else:
            pos.shares = pos.shares + shares
            pos.value = (pos.shares * price).quantize(Decimal("0.01"))
    else:
        work.cash.unsettled = work.cash.unsettled + dollars
        if pos is not None:
            pos.shares = pos.shares - shares
            pos.value = (pos.shares * price).quantize(Decimal("0.01"))
            if pos.shares <= 0:
                work.positions.remove(pos)


def _portfolio_impact(
    idea: TradeIdea, spend: Decimal, state: PortfolioState, cfg: OperatorConfig
) -> str:
    """§2.0.1: state what this trade does to the whole book, not the trade in isolation."""
    total_before = state.total_value
    total_after = total_before  # cash→equity swap; total unchanged at fill
    sleeve_before = state.sleeve_weights().get(idea.sleeve, Decimal(0))
    sleeve_after = (
        (sleeve_before * total_before + spend) / total_after if total_after else Decimal(0)
    )
    band = cfg.sleeves[idea.sleeve].target_pct if idea.sleeve in cfg.sleeves else (0, 0)
    in_band = band[0] <= float(sleeve_after) <= band[1] + 1e-9

    sector = SECTOR_MAP.get(idea.ticker)
    overlaps = [
        p.ticker for p in state.positions
        if p.ticker != idea.ticker and sector is not None and SECTOR_MAP.get(p.ticker) == sector
    ]
    corr_note = (
        f"adds correlation to existing {', '.join(overlaps)} (same {sector} exposure)"
        if overlaps
        else "no direct sector overlap with current holdings"
    )
    if idea.action in ("SELL", "TRIM"):
        # `spend` is negative for a sell (equity leaves the sleeve). Proceeds settle
        # T+1, so settled cash is unchanged today — it moves to unsettled (§2.1).
        proceeds = -spend
        cash_note = (
            f"settled cash {money(state.cash.settled)} unchanged ({money(proceeds)} proceeds "
            "settle T+1, then spendable)"
        )
    else:
        cash_after = state.cash.settled - spend
        cash_note = f"settled cash {money(state.cash.settled)} → {money(cash_after)}"
    return (
        f"{idea.sleeve} sleeve {float(sleeve_before):.1%} → {float(sleeve_after):.1%} "
        f"({'in band' if in_band else 'OUT OF BAND'} {band[0]:.0%}–{band[1]:.0%}); "
        f"{corr_note}; {cash_note}"
    )


def run_risk_agent(
    bundle: ResearchBundle,
    state: PortfolioState,
    cfg: OperatorConfig,
    prices: dict[str, Decimal],
) -> RiskAssessment:
    assessment = RiskAssessment(
        settled_cash=float(state.cash.settled),
        unsettled_cash=float(state.cash.unsettled),
        total_value=float(state.total_value),
        sleeve_weights={k: float(v) for k, v in state.sleeve_weights().items()},
    )

    for d in compute_drift(state, cfg):
        if d.status is not BandStatus.IN_BAND:
            assessment.drift_notes.append(
                f"{d.sleeve}: {float(d.actual_pct):.1%} is {d.status.value} band "
                f"{float(d.band_lo):.0%}–{float(d.band_hi):.0%} "
                f"({money(d.delta_to_band)} to re-enter)"
            )

    # Task B: a band breach nothing addresses is a detected condition. The prompt
    # asks the sector analyst to remediate; this verifies it actually did.
    breaches = find_unaddressed_breaches(compute_drift(state, cfg), bundle.sector.trade_ideas)
    assessment.unaddressed_breaches = [b.sleeve for b in breaches]
    assessment.anomalies.extend(breach_anomalies(breaches))
    assessment.breach_explanation = breach_explanation(breaches)

    gfv = state.ledger.gfv_count()
    assessment.gfv_status = (
        f"{gfv} GFV(s) on record" + (" — ACCOUNT RESTRICTION RISK" if state.ledger.gfv_restricted else "")
    )

    # Drift/GFV above reflect the real starting book. From here, `work` evolves as
    # trades are approved so the cycle's tickets are jointly fundable (§2.1).
    work = copy.deepcopy(state)

    ideas = sorted(bundle.sector.trade_ideas, key=lambda i: -i.confidence)
    approved_count = 0
    for idea in ideas:
        if idea.action in ("HOLD", "WATCH"):
            assessment.watch_items.append(f"{idea.ticker}: {idea.action} — {idea.thesis[:160]}")
            continue

        price = prices.get(idea.ticker)
        if price is None or price <= 0:
            assessment.vetoed.append(Veto(ticker=idea.ticker, reason="no reliable price this cycle"))
            continue

        if idea.action in ("SELL", "TRIM"):
            if state.ledger.would_cause_gfv(idea.ticker, state.as_of):
                assessment.vetoed.append(
                    Veto(
                        ticker=idea.ticker,
                        reason="selling now would trigger a Good Faith Violation (§2.1) — hard block",
                    )
                )
                continue
            held = next((p for p in work.positions if p.ticker == idea.ticker), None)
            if held is None or held.shares <= 0:
                assessment.vetoed.append(Veto(ticker=idea.ticker, reason="no position to sell (long-only)"))
                continue
            sell_dollars = Decimal(str(idea.target_dollars))
            # A TRIM rebalances an overweight — it must never push the sleeve BELOW
            # its band floor and create a new underweight. Clamp to the band_lo
            # floor. A full SELL (thesis exit) is not band-clamped; it may go to 0.
            trim_note: str | None = None
            if idea.action == "TRIM":
                total = work.total_value
                sleeve_val = work.sleeve_weights().get(idea.sleeve, Decimal(0)) * total
                band_lo = (
                    Decimal(str(cfg.sleeves[idea.sleeve].target_pct[0]))
                    if idea.sleeve in cfg.sleeves
                    else Decimal(0)
                )
                max_trim = (sleeve_val - band_lo * total).quantize(Decimal("0.01"))
                if max_trim <= 0:
                    assessment.watch_items.append(
                        f"{idea.ticker}: TRIM skipped — {idea.sleeve} already at/below its "
                        f"band floor {float(band_lo):.0%}; trimming would deepen the underweight"
                    )
                    continue
                if sell_dollars > max_trim:
                    trim_note = (
                        f"trim clamped to {money(max_trim)} so {idea.sleeve} lands at its band floor "
                        f"{float(band_lo):.0%} (LLM asked {money(sell_dollars)}, would have overshot below band)"
                    )
                    sell_dollars = max_trim
            # Round shares DOWN: a clamped trim must never cross its floor, and a
            # sell must never exceed the position, on rounding.
            sell_shares = min(
                held.shares, (sell_dollars / price).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
            )
            is_frac = sell_shares != sell_shares.to_integral_value()
            proceeds = (sell_shares * price).quantize(Decimal("0.01"))
            if approved_count >= MAX_TICKETS_PER_DAY:
                assessment.watch_items.append(f"{idea.ticker}: over daily ticket cap — deferred")
                continue
            assessment.approved.append(
                ApprovedIdea(
                    idea=idea,
                    sized_dollars=float(proceeds),
                    quantity=str(sell_shares),
                    order_type="market" if is_frac else "stop_limit",
                    limit_price=None if is_frac else float((price * Decimal("0.995")).quantize(Decimal("0.01"))),
                    is_fractional=is_frac,
                    cash_before=float(work.cash.settled),
                    cash_after=float(work.cash.settled),  # proceeds are UNSETTLED until T+1
                    sizing_notes=[
                        f"{idea.action} {sell_shares} sh of {held.shares} held",
                        *( [trim_note] if trim_note else [] ),
                        "proceeds settle T+1 — not spendable until then",
                    ],
                    portfolio_impact=_portfolio_impact(idea, -proceeds, work, cfg),
                )
            )
            _apply_fill(
                work, ticker=idea.ticker, sleeve=idea.sleeve, is_buy=False,
                shares=sell_shares, dollars=proceeds, price=price,
            )
            approved_count += 1
            continue

        # BUY — sized against `work` so cumulative settled-cash spend across the
        # cycle's buys can never exceed available settled cash (§2.1).
        decision = size_buy(
            ticker=idea.ticker,
            sleeve=idea.sleeve,
            price=price,
            target_dollars=Decimal(str(idea.target_dollars)),
            state=work,
            cfg=cfg,
            is_diversified_fund=idea.is_diversified_fund,
        )
        if decision.watch_item:
            assessment.watch_items.append(
                f"{idea.ticker}: {idea.thesis[:120]} — {'; '.join(decision.reasons)}"
            )
            continue
        if not decision.approved:
            assessment.vetoed.append(Veto(ticker=idea.ticker, reason="; ".join(decision.reasons)))
            continue
        if approved_count >= MAX_TICKETS_PER_DAY:
            assessment.watch_items.append(f"{idea.ticker}: over daily ticket cap ({MAX_TICKETS_PER_DAY}) — deferred")
            continue
        # Structural validation at generation: an unexecutable ticket must never
        # be built (Bug 3). Rejections are recorded, never silently dropped.
        check = validate_ticket(
            TicketDraft(
                ticker=idea.ticker,
                action=idea.action,
                order_type=decision.order_type,
                quantity=decision.quantity,
                limit_price=decision.limit_price,
                market_price=price,
                dollar_amount=decision.dollar_amount,
                settled_cash=decision.cash_before,
                is_fractional=decision.is_fractional,
            )
        )
        if not check.ok:
            assessment.anomalies.append(f"ticket rejected [{check.rule}] {check.message}")
            assessment.vetoed.append(
                Veto(ticker=idea.ticker, reason=f"[{check.rule}] {check.message}")
            )
            continue
        assessment.approved.append(
            ApprovedIdea(
                idea=idea,
                sized_dollars=float(decision.dollar_amount),
                quantity=str(decision.quantity),
                order_type=decision.order_type.value,
                limit_price=float(decision.limit_price) if decision.limit_price else None,
                is_fractional=decision.is_fractional,
                cash_before=float(decision.cash_before),
                cash_after=float(decision.cash_after),
                sizing_notes=[*decision.reasons, *( [check.disclosure] if check.disclosure else [] )],
                portfolio_impact=_portfolio_impact(idea, decision.dollar_amount, work, cfg),
            )
        )
        _apply_fill(
            work, ticker=idea.ticker, sleeve=idea.sleeve, is_buy=True,
            shares=decision.quantity, dollars=decision.dollar_amount, price=price,
        )
        approved_count += 1

    return assessment
