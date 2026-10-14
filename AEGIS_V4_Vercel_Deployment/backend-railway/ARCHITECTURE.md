# V4 Additional architecture

- `quality.py` — deterministic + second Gemini semantic QA before live sending.
- `delivery.py` — classify Gmail failures; ambiguous failures are not automatically retried.
- `storage.py` — SQLite `send_attempts` and `gmail_threads` plus send-state recovery.
- `gmail_oauth.py` — OAuth, RFC reply headers, Gmail thread read, incremental history polling.
- `server.py` — atomic send path, sync locking, read-only retry, quality and history endpoints.
- `static/` — thread panel, accurate provider status, retry and sync controls.
- Send lifecycle: `approved -> sending -> provider_accepted | send_failed | send_uncertain`. Provider acceptance is not recipient inbox confirmation.
- Email sync polling every 120 sec begins after Gmail OAuth connect, with deduplication by `gmail_message_id`.

---

# AEGIS V3 — architecture and trust boundaries

```
Local browser (HTML/CSS/vanilla JavaScript)
  ├─ Fetch JSON API ────────────────┐
  └─ SSE /api/events <──────────────┤
                                    ▼
             FastAPI (server.py / optional security.py)
             ├─ Case lifecycle / human approvals
             ├─ Policy administration and immutable snapshots
             ├─ Audit, real-time updates, metrics
             └─ Background single-run thread ───────┐
                                                      ▼
             Autonomous agent.py: Plan (Gemini)
                → tool: policy retrieval (ranked + full text safety context)
                → decide/draft (Gemini)
                → deterministic safety/output validation
                → JSON + logs + case persistence
             ├── Gemini REST via stdlib HTTPS (external)
             ├── SQLite storage.py (local persisted workflow)
             └── Gmail OAuth REST (test inbox only, optional)
```

## Why these choices

- **Python stdlib Gemini/Gmail REST:** Avoid installing heavyweight AI client stacks during a timed hackathon.
- **Existing agent pipeline preserved:** Input handling is generic and policy-grounded; no scripted results for original emails.
- **Safety decisions:** deterministic critical escalation guardrails run *after* model decisions, including API failure fallback; these are policy safeguards, not hardcoded responses for individual sample IDs.
- **SQLite:** atomic transactions, append-only application audit, policy versions, simple backups. Additive `ALTER TABLE` migrations preserve V2 case rows.
- **SSE:** browser sees genuinely persisted events, no fake streaming or chain-of-thought disclosure. DB-poll-based SSE scales to one local operator, not 10,000 concurrent clients.
- **Admin/reviewer:** optional password-protected local sessions in memory; admin may mutate policy/connect Gmail, reviewer may review cases. No production SSO.
- **Provider confirmation:** only a successful Gmail API response with ID marks a message as sent; operator approval alone never means delivery.

## Data governance

Student emails are provided as fictional JSON test data. They are read from disk; message content is transmitted to configured Gemini models during inference. API keys and tokens are not written to source or DB; session cookie is httpOnly + SameSite Strict on localhost. Never expose this demo to a public host or connect a real university inbox without authorization. OAuth scope defaults to read-only. Dangerous tool instructions inside student emails are treated as untrusted content.

## Next scaling phase (not implemented)

Use PostgreSQL with Alembic migrations, durable async worker queue (Celery/Redis or managed work queues), stateless API behind TLS, OIDC SSO + fine-grained RBAC, signed audit trails, encrypted secret vault, per-tenant data boundaries, structured schema validation, retention/deletion policy, rate limiting across instances, request tracing and provider webhook ingestion. The current demo is **not** that system; it is designed for local judging and rapid iteration.

## Enterprise live delivery extension

- Launcher sets `AEGIS_ENTERPRISE_MODE=1` and an ephemeral admin password; localhost only.
- `storage.app_settings` persists the `live_delivery` operator opt-in with audit event `delivery_setting_changed`.
- Gmail OAuth requests `gmail.readonly gmail.send`. The token response's `scope` list is checked before enabling delivery.
- `POST /api/gmail/delivery` is administrator-only and refuses enablement without connected Gmail + send scope.
- Existing `POST /api/cases/{id}/send` still enforces case approval, current policy, SEND confirmation, unique in-flight send guard, provider receipt and audit result.
- No automatic bulk sending. All provider tokens and OAuth client secrets remain process-memory only.
