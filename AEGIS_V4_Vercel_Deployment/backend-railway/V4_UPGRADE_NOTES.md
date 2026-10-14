# AEGIS V4 — Reliability Upgrade

This release extends the existing AEGIS V3 Enterprise application. It retains the original 20 competition emails, 22 policy codes, local SQLite state model, Gemini-based agent, static HTML/CSS/JavaScript interface, Gmail OAuth, case management, and `RUN.bat`.

## Quick start (Windows)

1. Extract this ZIP into a **new** `AEGIS_V4_Reliable` folder. Do not run directly inside a ZIP.
2. **Back up the earlier app first.** To preserve its case history, run `MIGRATE_FROM_V3.bat` from the new folder and paste the old project's folder path. Close both app instances first. Migration backs up overwritten files; **OAuth tokens / Gemini API keys cannot be migrated** and need reconnecting.
3. Double-click `RUN.bat`, set a local admin password (8+ characters), log in at `http://127.0.0.1:8765`.
4. In Settings, configure Gemini, reconnect Gmail with Gmail read/send scopes, and enable Live Email Delivery. Use a designated, authorized Gmail account, not the competition's fictional `example.com` recipients.
5. Use **Sync Gmail now** (or wait for the **120-second polling** after the Gmail OAuth connection). New Gmail messages are deduplicated by provider message ID. Existing message threads can be inspected from the email details drawer.
6. Analyze the email with Gemini, review policy codes and safety escalation, edit as required, approve, then click **Quality check & send**. The server checks policy/citation/length/safety conditions and runs an independent Gemini semantic review before any external send.
7. Confirm recipient and type `SEND`. If Google responds with a message ID, AEGIS displays **Google accepted — recipient delivery unverified**. This does **not** guarantee it arrived in the recipient inbox or was read.
8. For a definite API rejection, the case becomes **Send rejected**; read audit, fix error, manually authorize a retry. For a timeout or unknown outcome, the case becomes **Delivery uncertain** and further sends are blocked (to prevent duplicate mail). Inspect the Gmail Sent folder and audit manually.

## What changed

| Requested priority | New functionality |
|---|---|
| P0 Gmail delivery lifecycle | Atomic send attempts in SQLite, persistent provider-accepted / send-failed / send-uncertain status, safe crash recovery and retry authorization, raw Gmail provider receipt ID |
| P0 Gmail threading / sync | RFC `Message-ID` and `References` in replies, thread ID propagation, Gmail thread lookup with cached metadata fallback, incremental Gmail history sync with per-account cursor and duplicate detection; periodic 120-second polling while connected |
| P0 AI reply quality | Strict deterministic policy/recipient/150-word/safety gate + independent Gemini quality review **before** Gmail send, fail closed on reviewer errors |
| P1 High-risk escalation | Automatic **internal queue assignment** for hazardous/harassment/wellbeing/security referrals, audited by code; human approval still required. **This does not automatically notify an office** |
| P1 Audit/retries/errors | Timestamped send attempt records, typed audit events, limited retry/backoff for **read-only** Gmail calls, no automatic retries for outgoing Gmail requests, better delivery/status explanations in UI |

## Privacy and operation constraints

- Live sending defaults OFF and requires the local admin login, OAuth `gmail.send`, the draft approval, current policy, independent QA, and explicit per-email `SEND` confirmation.
- Gmail/Gemini secrets and access tokens remain in server memory rather than stored in project files. You will reconfigure them after restart.
- The configured Gemini service receives student email text and drafted replies for reasoning/QA. Only use data you are authorized to process.
- The system does **not** have an inbox-delivered/read-receipt guarantee. Gmail accepted message IDs do not prove delivery. Monitor non-delivery notifications manually.
- Synchronization is **polling, not Gmail push notifications**. The cursor is scoped to the connected account, and invalid/expired Gmail history ranges fall back to a recent inbox scan.
- This is a **local-first prototype**, not a certified multi-user enterprise service. For deployment to staff across a university, add proper roles/authentication hardening, managed OAuth storage, CSRF enforcement, Postgres, worker queues, institutional privacy review and monitored operational alerts.
- Existing legacy `sent` statuses are shown as "Google accepted — delivery unverified"; no false inbox-delivery inference is made.

## Verifications

`RUN_TESTS.bat` runs the offline unit/API tests. New tests cover provider receipt states, transport ambiguity, crash recovery, explicit retry, high-risk routing, Gemini quality rejection, RFC headers, and incremental sync deduplication. Tests use stubs/mocks; **no live Gmail/Gemini acceptance has been proven by these tests**. A live authorized Gmail + Gemini integration test is required before operational deployment.
