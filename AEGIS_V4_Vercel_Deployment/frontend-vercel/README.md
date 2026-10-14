# AEGIS V4 Vercel frontend

**Root Directory on Vercel:** `frontend-vercel`.
**Framework:** Other.
**Output Directory:** `public`.

Before deploying, edit the Railway HTTPS hostname in `vercel.json` so `/api/:path*` correctly proxies to the persistent FastAPI backend.

The frontend is static HTML/CSS/JS, not a standalone working AI agent. The original Python/SQLite backend must be deployed separately with persistent storage. See the parent `README.md` for the full deployment workflow.
