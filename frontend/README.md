# Atlas Dashboard

Vite + React 18 + TypeScript + Tailwind + Recharts + TanStack Query. Dark theme, eight views (Portfolio Overview, Performance, Positions, Market Pulse, Ticket Review, Brief Reader, Track Record, Sector Heat). Every chart carries a plain-language caption — the dashboard is a teaching surface. No Redux, no localStorage.

```bash
npm install
npm run dev        # http://localhost:5173 — expects the API on http://127.0.0.1:8000
npm test           # Vitest component tests
npm run build      # typecheck + production build
```

The backend must be running: `uv run atlas serve` (binds 127.0.0.1:8000). Point at a different API with `VITE_API_URL=http://127.0.0.1:8000 npm run dev`. Approving a ticket here only marks it approved and shows the CLI command — **orders are never placed from the browser**; execution is `uv run atlas execute --ticket-id <id>` in the terminal, by design.
