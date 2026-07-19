"""Brief Reader data (§9 view 6): markdown + glossary for hover tooltips."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.api.deps import get_session
from atlas.core.models import Brief, GlossaryTerm

router = APIRouter(tags=["briefs"])


@router.get("/brief/latest")
def latest(session: Session = Depends(get_session)) -> dict[str, Any]:
    brief = session.scalars(select(Brief).order_by(Brief.brief_date.desc(), Brief.id.desc())).first()
    if brief is None:
        raise HTTPException(status_code=404, detail="No brief yet — run `uv run atlas cycle`.")
    glossary = {
        t.term: t.definition
        for t in session.scalars(select(GlossaryTerm).where(GlossaryTerm.first_shown_at.is_not(None)))
    }
    return {
        "date": brief.brief_date.isoformat(),
        "kind": brief.kind,
        "status": brief.status,
        "critic_rounds": brief.critic_rounds,
        "markdown": brief.content_md,
        "glossary": glossary,
    }


@router.get("/briefs")
def index(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    rows = session.scalars(select(Brief).order_by(Brief.brief_date.desc()).limit(60)).all()
    return [
        {"id": b.id, "date": b.brief_date.isoformat(), "kind": b.kind, "status": b.status}
        for b in rows
    ]
