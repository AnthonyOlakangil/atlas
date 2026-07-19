"""The portfolio-risk agent — deterministic code, not LLM (§6).

Risk enforcement lives in code so it cannot be argued out of its caps. It is
the only agent that can veto an idea. It enforces bands and caps against
whatever instrument research selected — never the seed ticker lists (§2.0) —
and reasons portfolio-first (§2.0.1).
"""

from __future__ import annotations

from decimal import Decimal

from atlas.core.logging import get_logger
from atlas.core.settings import OperatorConfig
from atlas.portfolio.drift import BandStatus, compute_drift
from atlas.portfolio.sizing import SECTOR_MAP, size_buy
from atlas.portfolio.state import PortfolioState
from atlas.research.agents.schemas import (
    ApprovedIdea,
    ResearchBundle,
    RiskAssessment,
    TradeIdea,
    Veto,
)

log = get_logger("research.risk")

MAX_TICKETS_PER_DAY = 3  # §12: force prioritization


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
    cash_after = state.cash.settled - spend
    return (
        f"{idea.sleeve} sleeve {float(sleeve_before):.1%} → {float(sleeve_after):.1%} "
        f"({'in band' if in_band else 'OUT OF BAND'} {band[0]:.0%}–{band[1]:.0%}); "
        f"{corr_note}; settled cash ${state.cash.settled} → ${cash_after}"
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
                f"(${d.delta_to_band} to re-enter)"
            )

    gfv = state.ledger.gfv_count()
    assessment.gfv_status = (
        f"{gfv} GFV(s) on record" + (" — ACCOUNT RESTRICTION RISK" if state.ledger.gfv_restricted else "")
    )

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
            held = next((p for p in state.positions if p.ticker == idea.ticker), None)
            if held is None or held.shares <= 0:
                assessment.vetoed.append(Veto(ticker=idea.ticker, reason="no position to sell (long-only)"))
                continue
            sell_shares = min(
                held.shares, (Decimal(str(idea.target_dollars)) / price).quantize(Decimal("0.0001"))
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
                    cash_before=float(state.cash.settled),
                    cash_after=float(state.cash.settled),  # proceeds are UNSETTLED until T+1
                    sizing_notes=[
                        f"{idea.action} {sell_shares} sh of {held.shares} held",
                        "proceeds settle T+1 — not spendable until then",
                    ],
                    portfolio_impact=_portfolio_impact(idea, -proceeds, state, cfg),
                )
            )
            approved_count += 1
            continue

        # BUY
        decision = size_buy(
            ticker=idea.ticker,
            sleeve=idea.sleeve,
            price=price,
            target_dollars=Decimal(str(idea.target_dollars)),
            state=state,
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
                sizing_notes=decision.reasons,
                portfolio_impact=_portfolio_impact(idea, decision.dollar_amount, state, cfg),
            )
        )
        approved_count += 1

    return assessment
