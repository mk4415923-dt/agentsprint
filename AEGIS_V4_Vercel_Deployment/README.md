# AEGIS V4 — Deploy with Vercel (Updated README)

**Stack:** Vercel (HTML/CSS/JavaScript frontend) + Railway (Python FastAPI backend) + Railway **persistent volume** (SQLite, policy, inbox and audit data) + Gemini + optional Gmail OAuth.

> **Why two services?** Vercel supports FastAPI functions, but the **current AEGIS V4 is not stateless**: it writes SQLite, email JSON, policy, outbox and audit logs locally; holds login and Google OAuth sessions in process memory; and runs Gmail polling every 120 seconds. Deploying this unmodified as only a Vercel Function could lose changes, interrupt jobs and break Gmail authorization. **Do not upload the old ZIP directly to Vercel expecting enterprise email delivery to work.**

> This release is a **single-instance, small-team prototype**, not a production security-certified enterprise deployment. Never process real student data without appropriate authorization and security/privacy review. In the competition, emailing real people is prohibited: leave live delivery off.

## A. Contents of this ZIP

```text
AEGIS_V4_Vercel_Deployment/
├── README.md                       ← This updated guide
├── frontend-vercel/               ← Set as Vercel Root Directory
│   ├── vercel.json                 ← Edit the backend URL before deploying
│   └── public/
│       ├── index.html
│       └── static/{app.js,styles.css,favicon.svg}
└── backend-railway/               ← Set as Railway Root Directory
    ├── server.py, agent.py, gmail_oauth.py, storage.py ...
    ├── Dockerfile, railway.json, requirements.txt
    ├── DEPLOYMENT_ENV.example
    ├── data/                      ← Fictional competition samples, optional seed
    ├── tests/
    └── README.md                  ← Original local/enterprise documentation
```

## B. Prerequisites

- A GitHub account and a **private** repository, or a repository containing **no secrets or real student data**.
- Railway account for persistent FastAPI hosting. **Check its current plan pricing** before deployment.
- Vercel account for the web frontend.
- Optional: Gemini key from Google AI Studio; Google Cloud project with **Gmail API enabled** and OAuth Web application for Gmail.
- A strong administrator password of at least 12 characters.

Neither the frontend nor the backend needs Node.js/React in the application itself. Railway runs Python from the Dockerfile; Vercel serves static web assets.

## C. Step 1 — Upload the deployment folder to GitHub

1. Extract this ZIP.
2. Create an empty GitHub repository, e.g. `aegis-v4-deploy`.
3. Upload **both folders** `frontend-vercel/` and `backend-railway/`, plus this `README.md`, so the repository root contains those two folders.
4. **Never commit** `.env`, Google client secrets, Gmail tokens, a filled-in `DEPLOYMENT_ENV.example`, personal messages, `state/aegis.db`, or `outbox.json`.
5. Update `frontend-vercel/vercel.json` later when your Railway domain is known.

## D. Step 2 — Deploy the FastAPI backend on Railway

1. Open [Railway Dashboard](https://railway.com/dashboard) → **New Project → Deploy from GitHub repository**.
2. Select your repository and set the **Root Directory** for this service to `backend-railway` (or `/backend-railway` if the UI requires a leading slash).
3. Railway should detect the included `Dockerfile`. There is **no separate build command**.
4. Create a **persistent Volume**, mounted at exactly **`/data`** on this backend service. **Do this before handling data**. Keep **one replica** only (SQLite database and in-memory OAuth require it).
5. Under **Variables**, configure at minimum:

| Variable | Example / guidance |
|---|---|
| `AEGIS_DATA_DIR` | `/data` (must match volume mount) |
| `AEGIS_ADMIN_PASSWORD` | A unique, long random password, at least 12 characters |
| `AEGIS_ENTERPRISE_MODE` | `1` (enables authorized Gmail send OAuth scope; sends still require separate admin enablement and human approval) |
| `GEMINI_API_KEY` | Your secret Gemini key — do **not** put it in GitHub or frontend JS |
| `GEMINI_MODEL` | `gemini-2.5-flash` (change to a model available for your key) |
| `AEGIS_SEED_SAMPLE_DATA` | `1` for a **first-time demo** with 20 fictional emails, otherwise `0` |

6. Deploy. From **Settings → Networking**, create a Railway public HTTPS domain. Example: `https://aegis-api-production.up.railway.app`.
7. Visit `https://YOUR-RAILWAY-DOMAIN/api/health`. You should see a JSON health response. If there is a 500/503 error, check Railway deployment logs, the volume mount and variables.

**Important:** Before setting the real Vercel URL in step F, backend POST requests from Vercel will be blocked by the origin check. That is expected.

## E. Step 3 — Point the Vercel frontend at Railway

Open `frontend-vercel/vercel.json` and replace **only** the backend domain in the `destination` value:

```json
{
  "$schema": "https://openapi.vercel.sh/vercel.json",
  "outputDirectory": "public",
  "rewrites": [
    {
      "source": "/api/:path*",
      "destination": "https://aegis-api-production.up.railway.app/api/:path*"
    }
  ]
}
```

Use **your actual Railway HTTPS domain**, without a trailing slash. Commit the updated file to GitHub.

This **rewrite** means the browser can request `/api/login`, `/api/overview`, `/api/gmail/callback`, etc. on the Vercel domain, while the requests are forwarded to your running FastAPI backend. No browser-side cross-origin API URL or CORS workaround is required.

## F. Step 4 — Deploy the frontend on Vercel

1. Open [Vercel New Project](https://vercel.com/new) and import the same GitHub repository.
2. Select **Root Directory → `frontend-vercel`** (essential).
3. Select **Framework Preset: Other** (this is a static HTML/CSS/JS deployment, **not** the Python backend).
4. If asked for Output Directory, use **`public`**. No Build Command or Install Command is required.
5. Click **Deploy**.
6. Copy the resulting URL, e.g. `https://aegis-v4-demo.vercel.app`.
7. On Railway, add/update these variables and **redeploy** the backend:

```env
AEGIS_PUBLIC_URL=https://aegis-v4-demo.vercel.app
AEGIS_GOOGLE_REDIRECT_URI=https://aegis-v4-demo.vercel.app/api/gmail/callback
```

Use **your exact Vercel production domain**. Do not include a trailing slash in `AEGIS_PUBLIC_URL`.

8. Test `https://YOUR-VERCEL-DOMAIN/api/health` and then open `https://YOUR-VERCEL-DOMAIN`. Sign in with `AEGIS_ADMIN_PASSWORD`.

### Common Vercel settings

| Setting | Value |
|---|---|
| Project Root | `frontend-vercel` |
| Framework | Other |
| Build command | None |
| Output directory | `public` |
| Install command | None |
| Node runtime | Not needed for this frontend |
| API route | Rewritten via `vercel.json` to Railway |

**If Vercel returns a 404 or a blank page:** Check the **Root Directory** and that `public/index.html` is present. If the page loads but API calls fail, check the Railway URL in `vercel.json` and `AEGIS_PUBLIC_URL` on Railway.

## G. Step 5 — Set up Gmail OAuth (optional)

**Do this only with an authorized test mailbox.** The Gemini API key and Google OAuth Client Secret belong in **Railway Variables**, never in Vercel's public files.

1. Open [Google Cloud Console](https://console.cloud.google.com/apis/credentials), enable the **Gmail API**, set up the OAuth consent screen/audience and any required test users.
2. Create an OAuth **Web application**.
3. In **Authorized redirect URIs**, enter exactly:

   `https://YOUR-VERCEL-DOMAIN/api/gmail/callback`

4. Configure the OAuth client ID and secret under Railway **Variables**:

```env
AEGIS_GOOGLE_CLIENT_ID=YOUR_CLIENT_ID.apps.googleusercontent.com
AEGIS_GOOGLE_CLIENT_SECRET=YOUR_GOOGLE_CLIENT_SECRET
AEGIS_GOOGLE_REDIRECT_URI=https://YOUR-VERCEL-DOMAIN/api/gmail/callback
```

5. Redeploy Railway, open the Vercel UI, sign in and use **Settings → Connect Gmail**. Grant only permissions you understand and are authorized to use.
6. For delivery: use **Live Delivery** only after Google has granted `gmail.send`, the administrator enables sending, and an operator approves the exact draft. Gmail's response means **provider accepted**, not guaranteed inbox delivery.

**Limitations:** Gmail OAuth state and access/refresh tokens in this V4 code are kept **in process memory**. Restarting or scaling the Railway service may disconnect Gmail or interrupt OAuth. For production, replace this with encrypted persistent credential storage and shared sessions, and address Google's verification requirements for broader use. Use a stable custom domain if Google OAuth verification is required.

## H. Step 6 — Verify the deployment

Test in this order:

1. `https://YOUR-VERCEL-DOMAIN/` → interface loads, styles and scripts visible.
2. `https://YOUR-VERCEL-DOMAIN/api/health` → backend responds with JSON.
3. Login → `/api/session` reports `authenticated: true`.
4. Dashboard shows the inbox (only if first-time sample seeding was enabled).
5. Policy Library shows 22 fictional policy codes; policy changes remain after backend restart.
6. Add a fictional email → refresh → it remains in inbox. Review/audit records also persist.
7. Configure Gemini key and run **one** test analysis before running the complete batch.
8. Gmail OAuth: connect authorized test account; import a test message; approve, then send to a test mailbox **only if permitted**.
9. View Activity & Reasoning and confirm that statuses are real (`draft_ready`, `approved`, `provider_accepted`, `send_failed` or `send_uncertain`), not hardcoded.
10. Restart Railway: policy, audit, emails, drafts and case records should still be present. Gmail connection will need reauthorization or reconnection after a restart because tokens are memory-only.

## I. Troubleshooting

| Problem | Cause / next action |
|---|---|
| `404` on frontend | Incorrect Vercel Root Directory / output directory |
| `/api/health` returns `404` from Vercel | `vercel.json` not deployed or Railway rewrite URL incorrect |
| `502`/`503` from API | Railway backend stopped/failed; inspect Railway logs |
| `403 Request blocked: cross-origin write` | Set Railway `AEGIS_PUBLIC_URL` to **exact** Vercel origin and redeploy |
| `401 Administrator sign-in required` | Login through Vercel; check secure cookie and correct admin password |
| SQLite database error / state disappears | Railway volume not mounted at `/data`, or missing `AEGIS_DATA_DIR=/data` |
| `redirect_uri_mismatch` | Google Cloud Authorized redirect URI differs from Railway `AEGIS_GOOGLE_REDIRECT_URI` |
| Gmail keeps disconnecting | OAuth tokens are in memory; single worker only and reconnect after restarts |
| `Send with Gmail` unavailable | Admin not signed in, send OAuth scope missing, live delivery disabled or draft not approved |
| UI says `Provider accepted` but recipient got nothing | Check Gmail Sent, message ID, recipient address, spam and bounce notices; provider acceptance is not final delivery |
| AI analysis times out | Use smaller batches, confirm Gemini quota and see Railway logs; existing batch threading is prototype-grade |

## J. Security, limitations and future production upgrades

The above layout lets you get a **functional online prototype with a persistent backend**. For real enterprise deployment, additional work is necessary:

- Replace SQLite-on-volume with managed PostgreSQL for multi-instance scale; move all inbox/policy/outbox file state to database/object storage.
- Replace memory-only login and Gmail OAuth tokens with durable, encrypted shared sessions/token storage and proper operator identity management.
- Put Gmail polling/AI jobs on a durable queue/worker and implement monitored scheduling; the 120-second thread is not a distributed job runner.
- Add CSRF review, rate limiting, secure backup/restore, robust privacy controls, per-user roles, data retention and observability.
- Verify Google's production OAuth consent and domain ownership requirements before opening Gmail scopes to broad use.
- Vercel's **Hobby cron minimum is daily**, so it cannot reproduce AEGIS's 120-second Gmail polling on its own.

**If you insist on hosting the FastAPI backend itself on Vercel**, you must first implement the refactor above; merely adding a `vercel.json` does not make this stateful app serverless-safe. See the [official FastAPI on Vercel guide](https://vercel.com/docs/frameworks/backend/fastapi), [Vercel SQLite limitations](https://vercel.com/kb/guide/is-sqlite-supported-in-vercel) and [Vercel external rewrites documentation](https://vercel.com/docs/routing/rewrites).

## K. Local development and regression tests

From `backend-railway/`:

```powershell
python -m pip install -r requirements.txt
python -m uvicorn server:app --host 127.0.0.1 --port 8765
```

To run original tests:

```powershell
python -m unittest discover -s tests -v
```

Local V4 `RUN.bat` and original sample data are kept in `backend-railway/`. On Windows, you can still use `RUN.bat` for a **localhost-only** server; Vercel does **not** execute `.bat` files.

---

### Official references

- [Vercel: FastAPI](https://vercel.com/docs/frameworks/backend/fastapi)
- [Vercel: external API rewrites](https://vercel.com/docs/routing/rewrites)
- [Vercel: SQLite limitations](https://vercel.com/kb/guide/is-sqlite-supported-in-vercel)
- [Vercel: Cron Jobs plan limits](https://vercel.com/docs/cron-jobs/usage-and-pricing)
- [Railway](https://docs.railway.com/)
- [Google OAuth policy](https://developers.google.com/identity/protocols/oauth2/production-readiness/policy-compliance)
