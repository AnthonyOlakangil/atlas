"""Atlas CLI. `atlas execute` is the ONLY path to order placement (§1.2)."""

from __future__ import annotations

import typer

app = typer.Typer(name="atlas", help="Atlas — personal market research & portfolio agent.", no_args_is_help=True)


@app.command()
def cycle() -> None:
    """Run the full daily research cycle now."""
    from atlas.research.orchestrator import run_daily_cycle

    run_daily_cycle()


@app.command()
def brief() -> None:
    """Show today's brief path (generates nothing)."""
    from atlas.research.synthesis import latest_brief_path

    path = latest_brief_path()
    typer.echo(path if path else "No brief yet — run `atlas cycle`.")


@app.command()
def tickets() -> None:
    """List proposed tickets and their statuses."""
    from atlas.execution.tickets import list_tickets

    for line in list_tickets():
        typer.echo(line)


@app.command()
def execute(
    ticket_id: int = typer.Option(..., "--ticket-id", help="ID of an APPROVED ticket."),
    yes: bool = typer.Option(False, "--yes", help="Skip the interactive confirmation (still runs review)."),
) -> None:
    """Execute one approved ticket. Runs `review_equity_order` first; warnings require a second confirmation."""
    from atlas.execution.approval import execute_ticket

    execute_ticket(ticket_id=ticket_id, assume_yes=yes)


@app.command()
def approve(ticket_id: int = typer.Option(..., "--ticket-id")) -> None:
    """Mark a PENDING_APPROVAL ticket APPROVED (does NOT execute it)."""
    from atlas.execution.approval import approve_ticket

    approve_ticket(ticket_id)


@app.command()
def reject(ticket_id: int = typer.Option(..., "--ticket-id")) -> None:
    """Reject a proposed ticket."""
    from atlas.execution.approval import reject_ticket

    reject_ticket(ticket_id)


@app.command()
def filled(
    ticket_id: int = typer.Option(..., "--ticket-id"),
    price: float = typer.Option(..., "--price", help="Your actual fill price from the app."),
) -> None:
    """Manual broker mode: record a fill YOU placed in Robinhood (bookkeeping for scoring)."""
    from decimal import Decimal

    from atlas.execution.approval import record_manual_fill

    record_manual_fill(ticket_id, Decimal(str(price)))


@app.command()
def report(track_record: bool = typer.Option(False, "--track-record")) -> None:
    """Render reports. --track-record: hit rate, alpha vs SPY, drawdown."""
    from atlas.portfolio.scoring import render_track_record

    if track_record:
        typer.echo(render_track_record())
    else:
        typer.echo("Nothing to report. Try --track-record.")


@app.command()
def serve(port: int = typer.Option(8000, help="Port on 127.0.0.1")) -> None:
    """Run the FastAPI backend (binds 127.0.0.1 only — no auth by design)."""
    import uvicorn

    uvicorn.run("atlas.api.app:app", host="127.0.0.1", port=port)


@app.command()
def schedule() -> None:
    """Start the APScheduler loop (06:30 PT cycle, 13:15 PT update, stop monitor)."""
    from atlas.scheduler import start_scheduler

    start_scheduler()


def main() -> None:
    """Entrypoint (see pyproject.toml [project.scripts]).

    Known, already-clearly-messaged operational errors (missing API key, gate
    violations, malformed portfolio.yaml, broker/network failures) are caught
    here and printed as a one-line message — never a stack trace (§3.1, §12).
    Anything else is a real bug and should surface with its full traceback.
    """
    import sys

    from atlas.core.llm import LLMError
    from atlas.execution.approval import ApprovalError
    from atlas.execution.transport import MCPToolError, MCPTransportError
    from atlas.portfolio.state import PortfolioConfigError

    try:
        app()
    except (LLMError, ApprovalError, PortfolioConfigError, MCPTransportError, MCPToolError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
