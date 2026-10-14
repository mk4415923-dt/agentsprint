# AEGIS V4 Reliability Release

**Start here:** See [V4_UPGRADE_NOTES.md](V4_UPGRADE_NOTES.md) for up-to-date upgrade/migration, Gmail status semantics and testing instructions.

# AEGIS Enterprise — Live Gmail Delivery Edition

**Updated for a local, authenticated, operator-controlled helpdesk.** This build preserves the existing AEGIS V3 agent, 20 supplied competition sample messages, policy editor, analytics, audit records and operator workspace, and adds administrator-enabled Gmail sending.

## Start on Windows (one click)

1. Extract `AEGIS_V3_Enterprise_Live.zip`, open the `AEGIS_V3_Enterprise_Live` folder.
2. Double-click **`RUN.bat`**. Enter a local administrator password **at least 8 characters long** when prompted; keep the terminal open. The process starts only at `127.0.0.1:8765`.
3. Open `http://127.0.0.1:8765`, sign in with that password, and configure your Gemini API key under Settings if you plan to analyze new messages.
4. Under **Settings → Gmail OAuth**, configure a Google Cloud OAuth *Web application* with redirect URI `http://127.0.0.1:8765/api/gmail/callback`. The Gmail API must be enabled and the OAuth consent screen correctly configured for the account.
5. Click **Connect / Reauthorize Gmail**. Accept the requested scopes **`gmail.readonly` and `gmail.send`**. If you connected with read-only permission in the previous app, reconnect: a prior login does not silently gain send permission.
6. Click **Enable live delivery**. Only the administrator can change this setting; it is OFF until deliberately enabled and is saved as a non-secret local setting.
7. **Import latest 10** to retrieve messages. Run the agent, open a case, inspect/modify the proposed draft, approve it, then click **Send with Gmail**. Verify recipient and type `SEND` to authorize the provider call.
8. Case becomes **Sent** only after the Gmail API returns a message ID. If Google rejects or times out, the app reports an error without claiming success; check Gmail Sent folder before retrying an ambiguous timeout to prevent duplicates.

**Do not automatically send to the 20 fictional sample email records.** Use a designated account and consented recipients to validate Gmail integration before handling genuine university messages. This application has not been security audited for public Internet exposure, regulatory compliance, multi-user production deployment, or tenant isolation. Gmail and Gemini data processing requires appropriate authorization and privacy assessment.

### What this changes

- No contest-specific “safe mode” message appears in the enterprise Settings UI; delivery now has a clear **Enable/Disable live delivery** switch.
- Gmail OAuth requests read + send scopes when started using `RUN.bat`. **The API key and OAuth tokens remain in memory** and must be reconfigured after restart.
- The delivery setting persists in SQLite (`state/aegis.db`), but no sending is allowed without a fresh Gmail connection, an admin login, an approved latest-policy draft, and per-message confirmation.
- Disabling delivery blocks new send requests immediately and records an audit event.
- Existing policy history, case statuses and audit data remain available if you copy your previous `state/aegis.db` into the new extracted directory **before launching**, after making a backup. Alternatively, use `MIGRATE_FROM_V3.bat` to transfer case data with backups. `MIGRATE_FROM_V2.bat` is for older V2 data.
- `RUN_DEMO.bat` is the intentionally separate local demo launcher.

### Critical distinctions

A returned Gmail message ID confirms that Gmail **accepted the send operation**; it does not prove that the email reached the recipient's inbox or was read. `sent` in the application means provider-accepted, not guaranteed delivery. The app never silently sends AI output without human approval.

**Compatibility / limits:** OAuth needs Google Cloud credentials and user consent, a valid Gmail account and Google API connectivity. Live OAuth and actual Gmail sending were **not** exercised in the package build because no user credentials were available. Unit tests mock provider responses. For a real multi-user enterprise rollout, additional work is required for hardened authentication, managed secrets, encrypted token storage, access review, retention controls, tenancy, monitoring and compliance.

---

## Historical V3 documentation

# AEGIS V3 — Student Helpdesk Command Center

**Engineer's Code 2026, Module 2 · Student Helpdesk Triage Agent**

A *local-first demo* upgrade of AEGIS Pro. The original competition data (20 student emails and 22 fictional policy codes) and Python agent remain intact. The application layers are: **Python FastAPI + vanilla HTML/CSS/JS + SQLite + Gemini REST + optional Gmail OAuth**. No Node.js, React, pandas, Streamlit, Redis or Docker are required.

> **Important:** This is a single-computer demonstration system, **not** an audited enterprise product or an operational university service. Gmail and Gemini integration require your credentials and network access. Real student data needs a separate privacy, permission and security review.

## Legacy local-demo instructions (use the enterprise steps above instead)

1. **Extract the ZIP** (do not run `.bat` inside the ZIP). Open the extracted **`AEGIS_V3_Enterprise`** folder.
2. **Double-click `RUN_DEMO.bat`**. It creates a reusable `.venv` with `--system-site-packages` so it can reuse FastAPI and Uvicorn already installed on your computer. On a fresh machine it installs *only* those two lightweight packages.
3. Browser opens **http://127.0.0.1:8765**. Keep the terminal open.
4. In **Settings & API**, enter a valid Gemini API key. It stays in server memory (lost on restart). Never share or commit credentials.
5. Click **Run AI agent**. It reads the 20 supplied emails, chooses its next actions through Gemini-assisted classification and policy-grounded processing, writes `outbox.json` and `agent_log.jsonl`, and persists workflow records in SQLite.
6. Open any email to see the **original message**, policy citations, priority/escalation, **editable reply**, save/approve/reject/reopen actions, **assignment**, private notes and case history. Nothing is sent in competition mode.
7. Check **Live execution**, **Analytics**, **Policy Library** and **Activity & Reasoning**. Evidence is based on actual recorded data, not invented progress.

**PowerShell alternative** (from the extracted folder):

```powershell
.\RUN.bat
```

**Secure local login:** run `RUN_SECURE.bat` instead; it prompts for a hidden administrator password. To add a separate reviewer password, set the `AEGIS_REVIEWER_PASSWORD` environment variable before launching. Admin can update policies/Gmail settings; reviewer can manage case notes, assignments and draft approvals. The legacy `RUN_DEMO.bat` intentionally runs without a password in local competition demonstration mode and binds only to loopback (`127.0.0.1`). **Do not expose this web server on a public network.**

### Move older V2 work into V3

If you've already analyzed cases in **AEGIS Pro**, close both apps. In the **new** V3 folder, run **`MIGRATE_FROM_V2.bat`** and paste the full path to your previous extracted `AEGIS_Pro_Helpdesk` folder. This migrates your `state/aegis.db`, `outbox.json`, `agent_log.jsonl`, policy and inbox. Old target files are backed up in `migration_backups/` before they are replaced. It does not import external API credentials. Run this *before* starting V3 on your real data; **migration replaces any current V3 database** after backing it up.

## Email processing and lifecycle

`Received → AI planning → Policy retrieval → Decision → Safety validation → Draft saved → Human review → Approved (not sent)`

- **Unread dot** disappears only when the email is actually opened (recorded in SQLite).
- **Draft ready** means there is proposed text, not delivery.
- **Needs human review** means the agent detected an escalation or the operator edited the draft.
- **Rejected** needs correction/reanalysis. **Reopened** moves back to human review.
- **Approved** is not a send action. **Sent ✓** can only result from Gmail returning a provider message ID, which is persisted with a timestamp.
- Case assignees and internal notes are private workflow records; there is no automatic forwarding or interaction with a real department.
- Agent failure defaults to conservative human review rather than claiming success.

The official task requires **one-command autonomous analysis without manual steps**. The later human review and Gmail capabilities are OPTIONAL product demonstrations *outside* the autonomous competition processing chain. Nothing is automatically delivered.

## Real-time monitoring

`GET /api/events` streams actual SQLite audit records through **Server-Sent Events** (SSE) with event IDs, automatic reconnection, and a bounded backlog cursor. The UI displays *summaries* of logged planning, policy lookup, validation, retries, drafts, edits and other actions. It does **not** reveal hidden model reasoning. The dashboard's batch progress comes from genuinely completed items and does not use fake percentage timers.

## Policy management

- Open **Policy Library → Update policy**, edit the Markdown and publish a new immutable version.
- **Import policy file** accepts `.md`, `.txt`, and `.docx`. Word is converted using Python's standard-library ZIP/XML parser. Word import displays a confirmation and immediately publishes after validation; Markdown/text open the editor first. PDF import is **not included** (convert a PDF to text first).
- **Compare versions** shows a real unified text diff. Earlier versions are retained in SQLite and can be opened for review and republished as a new version.
- Critical ESC safety codes cannot be removed; duplicates are rejected. Any old-policy draft must be reanalyzed before approval.
- Only the rules currently stored in `data/policy.md` are authoritative. If information is missing the agent must not invent it.

## Gmail OAuth (optional designated test inbox)

Gmail integration requires a **separate Google Cloud OAuth Web application**; authorizing Gmail inside ChatGPT does not automatically connect this standalone FastAPI app.

1. Enable the Gmail API in Google Cloud, configure the OAuth consent screen and test users.
2. Create an OAuth **Web application**, with authorized redirect `http://127.0.0.1:8765/api/gmail/callback`.
3. In **Settings & API**, enter the client ID and secret, press Connect, then authorize your **permitted test account**.
4. Import up to 10 designated test messages. The importer preserves sender, subject, thread ID, timestamp and attachment **metadata** where provided, and deduplicates Gmail message IDs.
5. In normal mode the OAuth request asks only for `gmail.readonly`. Sending is **disabled by default** and must remain off during the contest.

**Outside the competition**, for an expressly authorized test inbox and test recipient only, a process started with `AEGIS_ENABLE_LIVE_GMAIL_SEND=1` can request Gmail send permission. An authorized administrator must review, approve, and twice confirm a send. If Gmail cannot confirm a provider message ID, no `Sent ✓` is shown. Do not enable against real students under the competition's rules.

External tokens are held only in memory and cleared at restart. **This is not production-grade credential storage or OAuth token management.**

## Analytics and audit

The dashboard computes actual case statuses, classifications, high-priority cases, escalation rates, retries, errors, confirmed provider deliveries and daily draft activity from SQLite and `outbox.json`. The **audit timeline** is append-only at the application level and exports JSON or CSV. Records include operator actions and tool summaries, not full message bodies or secret keys. Note that database administrators could still modify a local SQLite file: it is not cryptographically tamper-evident.

## Command-line modes

```powershell
python agent.py --check-input    # 20 email and policy format validation, no API calls
python agent.py                  # Batch run, requires GEMINI_API_KEY
python verify_outbox.py          # Check JSON schema and policy compliance
python -m unittest discover -s tests -v    # Tests (FastAPI TestClient needs httpx in dev environment)
```

`RUN_AGENT.bat` is a terminal-only option and does not require FastAPI/Uvicorn. `RUN_TESTS.bat` requires developer test dependencies for TestClient (httpx), but these are **not** installed in normal fast-launch mode.

## Project structure

```text
AEGIS_V3_Enterprise/
  RUN.bat, RUN_SECURE.bat, RUN_SECURE.ps1, RUN_AGENT.bat
  MIGRATE_FROM_V2.bat, migrate_v2.py, RUN_TESTS.bat
  agent.py                   Autonomous two-stage Gemini policy-grounded processing
  server.py                  FastAPI routes + real SSE stream
  security.py                Optional local admin/reviewer sessions
  storage.py                 SQLite persistence, version migrations, audit, notes
  gmail_oauth.py             Optional Google OAuth and Gmail REST, no extra SDK
  launcher.py                Opens localhost in the browser
  static/index.html          Operator desktop/mobile UI
  static/styles.css          Original professional visual system + V3 + dark theme
  static/app.js              Browser controls, views, SSE, case management
  data/inbox/*.json          20 original fictional student emails
  data/policy.md             Original fictional policy
  demo_inputs/               Difficult unseen test scenario (not in live inbox)
  tests/                     Offline, network-free regression tests
  ARCHITECTURE.md, PRESENTATION_GUIDE.md, README.md, .env.example
  state/aegis.db             Created automatically after first launch
  outbox.json                Created after running the agent
  agent_log.jsonl            Created after running the agent
```

## Common errors

| What you see | Why | What to do |
| --- | --- | --- |
| Install taking long | First install requires network | Wait for FastAPI/Uvicorn only; V3 skips installed packages |
| Port already in use | Old AEGIS `RUN.bat` still running | Close previous terminal and relaunch |
| Gemini key missing | API not configured | Add key in Settings |
| Gemini 429 | Quota/rate-limit hit | Wait for quota reset or choose permitted model/key |
| Gemini 401/403 | Invalid key/access | Check AI Studio key/model permissions |
| Gmail redirect mismatch | Wrong OAuth redirect URL | Use exact `http://127.0.0.1:8765/api/gmail/callback` |
| Not sent | Live delivery disabled intentionally | Use draft export in competition; do not enable real send |
| Cannot approve | Outdated policy or no draft | Re-run affected case and review |
| Blank dashboard | FastAPI process stopped | Keep RUN.bat window open; reload |
| Cannot publish policy | Missing/duplicate ESC codes | Keep critical rules, remove duplicates, retry |
| Administrator sign-in required | Secure mode is on/session expired | Enter local admin/reviewer password |

### Limitations and future production work

V3 **does not** include multi-machine worker queues, PostgreSQL migration execution, hosted TLS, cryptographic audit immutability, persistent encrypted OAuth token vaults, full semantic vector search, SSO/enterprise IAM or hardened deployment. Its modules and migrations are laid out to make these feasible later; see `ARCHITECTURE.md`. It does not claim to meet certified production compliance standards.
