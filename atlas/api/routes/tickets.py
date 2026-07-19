"""Ticket Review data + approve/reject actions (§9 view 5).

Approve marks the ticket APPROVED and returns the CLI command to run —
it does NOT and CANNOT fire an order from the browser.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.api.deps import get_session
from atlas.core.howto import robinhood_howto
from atlas.core.models import Ticket, TicketStatus
from atlas.core.settings import get_settings

router = APIRouter(tags=["tickets"])


def _ticket_json(t: Ticket) -> dict[str, Any]:
    return {
        "robinhood_howto": robinhood_howto(
            action=t.action.value,
            ticker=t.ticker,
            order_type=t.order_type.value,
            is_fractional=t.is_fractional,
            quantity=t.quantity,
            dollar_amount=t.dollar_amount,
            limit_price=t.limit_price,
        ),
        "id": t.id,
        "status": t.status.value,
        "mode": t.mode.value,
        "created_at": t.created_at.isoformat(),
        "ticker": t.ticker,
        "action": t.action.value,
        "sleeve": t.sleeve,
        "order_type": t.order_type.value,
        "quantity": float(t.quantity),
        "dollar_amount": float(t.dollar_amount),
        "limit_price": float(t.limit_price) if t.limit_price is not None else None,
        "is_fractional": t.is_fractional,
        "cash_before": float(t.cash_before),
        "cash_after": float(t.cash_after),
        "thesis": t.thesis,
        "catalyst": t.catalyst,
        "risk": t.risk,
        "bear_case": t.bear_case,
        "falsifier": t.falsifier,
        "time_horizon": t.time_horizon,
        "sizing_rationale": t.sizing_rationale,
        "portfolio_impact": t.portfolio_impact,
        "learning_note": t.learning_note,
        "exit_stop": float(t.exit_stop) if t.exit_stop is not None else None,
        "exit_target": float(t.exit_target) if t.exit_target is not None else None,
    }


@router.get("/tickets")
def list_all(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    rows = session.scalars(select(Ticket).order_by(Ticket.created_at.desc()).limit(100)).all()
    return [_ticket_json(t) for t in rows]


@router.post("/tickets/{ticket_id}/approve")
def approve(ticket_id: int, session: Session = Depends(get_session)) -> dict[str, str]:
    from atlas.execution.approval import ApprovalError, approve_ticket

    try:
        approve_ticket(ticket_id, session)
    except ApprovalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if get_settings().broker_mode == "manual":
        return {
            "status": "APPROVED",
            "next_step": (
                "Place this order yourself in the Robinhood app (see the how-to on this "
                f"ticket), then record your fill:  uv run atlas filled --ticket-id {ticket_id} --price <fill>"
            ),
            "note": "Broker not connected (manual mode) — Atlas never places orders for you.",
        }
    return {
        "status": "APPROVED",
        "next_step": f"Run in your terminal:  uv run atlas execute --ticket-id {ticket_id}",
        "note": "Execution is CLI-only by design — the browser can never place an order.",
    }


@router.post("/tickets/{ticket_id}/reject")
def reject(ticket_id: int, session: Session = Depends(get_session)) -> dict[str, str]:
    from atlas.execution.approval import ApprovalError, reject_ticket

    try:
        reject_ticket(ticket_id, session)
    except ApprovalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": TicketStatus.REJECTED.value}
