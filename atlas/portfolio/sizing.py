"""Position sizing against operator.yaml (§2, §2.1).

Binding constraints: sleeve target_pct bands, risk caps, settled cash, the
fractional⇒market platform rule, and the $10 minimum ticket. The sleeve
seed_examples are NEVER consulted — any US-listed long-only instrument the
research selected is sized on its merits (§2.0).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal

from atlas.core.models import OrderType
from atlas.core.settings import OperatorConfig
from atlas.portfolio.state import PortfolioState

MIN_TICKET_USD = Decimal("10")  # §12: below this, fees and slippage dominate

# Best-effort sector map for the concentration cap. Unknown tickers are NOT
# rejected (the universe is open — §2.0); they simply skip the sector check
# and carry a note. This map may grow; it must never become a whitelist.
SECTOR_MAP: dict[str, str] = {
    "NVDA": "tech_semis", "TSM": "tech_semis", "AVGO": "tech_semis", "AMD": "tech_semis",
    "MU": "tech_semis", "INTC": "tech_semis", "DELL": "tech_semis", "SMH": "tech_semis",
    "SOXX": "tech_semis", "SOXQ": "tech_semis", "SMCI": "tech_semis", "ANET": "tech_semis",
    "ASML": "tech_semis", "ARM": "tech_semis", "QQQ": "tech_broad", "QQQM": "tech_broad",
    "VOO": "broad_market", "VTI": "broad_market", "SPY": "broad_market", "RSP": "broad_market",
    "GLDM": "gold", "IAU": "gold", "GLD": "gold",
    "XLV": "healthcare", "XLF": "financials", "IBIT": "crypto",
}


@dataclass
class SizingDecision:
    ticker: str
    sleeve: str
    approved: bool
    watch_item: bool  # unaffordable with settled cash → watch item, not ticket
    order_type: OrderType
    quantity: Decimal
    dollar_amount: Decimal
    limit_price: Decimal | None
    is_fractional: bool
    cash_before: Decimal
    cash_after: Decimal
    reasons: list[str] = field(default_factory=list)


def _reject(ticker: str, sleeve: str, cash: Decimal, reasons: list[str], *, watch: bool = False) -> SizingDecision:
    return SizingDecision(
        ticker=ticker, sleeve=sleeve, approved=False, watch_item=watch,
        order_type=OrderType.MARKET, quantity=Decimal(0), dollar_amount=Decimal(0),
        limit_price=None, is_fractional=False, cash_before=cash, cash_after=cash,
        reasons=reasons,
    )


def size_buy(
    *,
    ticker: str,
    sleeve: str,
    price: Decimal,
    target_dollars: Decimal,
    state: PortfolioState,
    cfg: OperatorConfig,
    fractional_tradable: bool = True,
    is_diversified_fund: bool = False,
    limit_buffer_pct: Decimal = Decimal("0.005"),
) -> SizingDecision:
    """Size a buy the research layer proposed. Clamps to caps; never violates them.

    is_diversified_fund: the single-position cap targets single-ISSUER risk; a
    diversified fund (e.g. the core index ETF at 50-60%) is capped by its sleeve
    band instead — otherwise the operator's own framework would be unsatisfiable.
    """
    reasons: list[str] = []
    settled = state.cash.settled
    total = state.total_value

    if sleeve not in cfg.sleeves:
        return _reject(ticker, sleeve, settled, [f"unknown sleeve {sleeve!r}"])
    if price <= 0:
        return _reject(ticker, sleeve, settled, [f"no valid price for {ticker}"])

    spend = target_dollars

    # --- risk caps: clamp, never violate ------------------------------------
    if total > 0:
        held = state.position_weight(ticker) * total
        max_position = cfg.risk_decimal("max_single_position_pct") * total
        if not is_diversified_fund and held + spend > max_position:
            clamped = max_position - held
            reasons.append(
                f"clamped by single-position cap {cfg.risk.max_single_position_pct:.0%}: "
                f"{spend} → {clamped}"
            )
            spend = clamped

        band_lo, band_hi = cfg.sleeves[sleeve].target_pct
        sleeve_now = state.sleeve_weights().get(sleeve, Decimal(0)) * total
        sleeve_max = Decimal(str(band_hi)) * total
        if sleeve_now + spend > sleeve_max:
            clamped = sleeve_max - sleeve_now
            reasons.append(
                f"clamped by sleeve band: {sleeve} tops out at {band_hi:.0%} "
                f"({spend} → {clamped})"
            )
            spend = clamped

        sector = SECTOR_MAP.get(ticker)
        if sector is None:
            reasons.append(f"sector unknown for {ticker} — concentration check skipped, flagged for review")
        elif sector == "broad_market":
            pass  # a whole-market fund is not a sector bet; the sleeve band governs it
        else:
            sector_now = sum(
                (p.value for p in state.positions if SECTOR_MAP.get(p.ticker) == sector),
                Decimal(0),
            )
            sector_max = cfg.risk_decimal("max_sector_concentration_pct") * total
            if sector_now + spend > sector_max:
                clamped = sector_max - sector_now
                reasons.append(
                    f"clamped by sector-concentration cap "
                    f"{cfg.risk.max_sector_concentration_pct:.0%} ({sector}): → {clamped}"
                )
                spend = clamped

    spend = spend.quantize(Decimal("0.01"), rounding=ROUND_DOWN)

    if spend < MIN_TICKET_USD:
        return _reject(
            ticker, sleeve, settled,
            [*reasons, f"below ${MIN_TICKET_USD} minimum ticket after caps (got ${spend})"],
        )

    # --- settled-cash gate (§2.1): a thesis Atlas can't fund is a WATCH ITEM --
    if not state.cash.can_fund(spend):
        return _reject(
            ticker, sleeve, settled,
            [*reasons,
             f"settled cash ${settled} cannot fund ${spend} — surfaced as watch item, not ticket"],
            watch=True,
        )

    # --- whole-vs-fractional fork (§2.1 platform rule) ------------------------
    whole_shares = (spend / price).to_integral_value(rounding=ROUND_DOWN)
    if whole_shares >= 1:
        quantity = whole_shares
        limit_price = (price * (1 + limit_buffer_pct)).quantize(Decimal("0.01"))
        dollar_amount = (quantity * limit_price).quantize(Decimal("0.01"))
        if not state.cash.can_fund(dollar_amount):
            # limit buffer nudged it past settled cash — drop a share if needed
            quantity -= 1
            dollar_amount = (quantity * limit_price).quantize(Decimal("0.01"))
            if quantity < 1 or dollar_amount < MIN_TICKET_USD:
                return _reject(ticker, sleeve, settled, [*reasons, "cannot fund a whole share within settled cash"], watch=True)
        return SizingDecision(
            ticker=ticker, sleeve=sleeve, approved=True, watch_item=False,
            order_type=OrderType.LIMIT, quantity=quantity, dollar_amount=dollar_amount,
            limit_price=limit_price, is_fractional=False,
            cash_before=settled, cash_after=(settled - dollar_amount).quantize(Decimal("0.01")),
            reasons=[*reasons, f"whole-share limit buy: {quantity} × limit ${limit_price}"],
        )

    if not fractional_tradable:
        return _reject(
            ticker, sleeve, settled,
            [*reasons, f"${spend} buys < 1 share and {ticker} is not fractional-tradable"],
        )
    quantity = (spend / price).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
    return SizingDecision(
        ticker=ticker, sleeve=sleeve, approved=True, watch_item=False,
        order_type=OrderType.MARKET,  # forced: Robinhood forbids fractional limit orders
        quantity=quantity, dollar_amount=spend, limit_price=None, is_fractional=True,
        cash_before=settled, cash_after=(settled - spend).quantize(Decimal("0.01")),
        reasons=[*reasons,
                 "fractional MARKET buy — forced because the quantity is fractional; "
                 "no price cap on this fill (Robinhood bans fractional limit orders)"],
    )
