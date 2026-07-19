"""FastAPI backend for the dashboard. Binds 127.0.0.1 only; no auth by design (§12).

The API can approve/reject tickets. It can NEVER execute one — execution is
CLI-only (`atlas execute`), so a compromised browser tab cannot fire an order.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from atlas.api.routes import briefs, market, portfolio, tickets, track


def create_app() -> FastAPI:
    app = FastAPI(title="Atlas", docs_url="/api/docs")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.include_router(portfolio.router, prefix="/api")
    app.include_router(tickets.router, prefix="/api")
    app.include_router(briefs.router, prefix="/api")
    app.include_router(market.router, prefix="/api")
    app.include_router(track.router, prefix="/api")
    return app


app = create_app()
