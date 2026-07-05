# Fluxion UI (frontend)

Single-page React app for the Fluxion observation deck. Builds into
`../src/fluxion/web/static/`, which the FastAPI server (`fluxion-web`) serves.

## Requirements

- Node ≥ 18 (Vite 5).

## Dev

```bash
# 1. start the Python API on :8765
fluxion-web

# 2. in another shell, start Vite dev with HMR on :5173 (proxies /api to :8765)
cd web
npm install
npm run dev
```

Open http://127.0.0.1:5173.

## Build

```bash
cd web
npm install
npm run build
```

Output lands in `src/fluxion/web/static/` and is served by `fluxion-web` as the
SPA shell.

## Current Features

This is the current port scope:

- Task list (polled every 5s)
- Task detail (lifecycle, changed files, stdout/stderr)
- Token via `localStorage.fluxion.uiToken` → `Authorization: Bearer …`

The richer dashboard surface (tweaks panel, sessions/executors rail, multi-tab detail, sparkline, etc.) is planned for subsequent updates once the data and streaming shapes are fully settled.
