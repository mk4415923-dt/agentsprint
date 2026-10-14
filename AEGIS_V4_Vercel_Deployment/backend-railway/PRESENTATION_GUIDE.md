# Judges' demonstration — AEGIS V3 (7–10 minutes, spoken)

**0:00–0:45 — Problem**: A fictional student helpdesk must handle 20 diverse emails while checking policy, classifying risk, drafting answers and flagging humans. The challenge output is a JSON outbox, not messages sent to real students.

**0:45–1:45 — Architecture**: Open Overview. Explain original student JSON inputs → Gemini planning → policy retrieval → Gemini decision/draft → deterministic policy/safety guardrails → JSON deliverable → SQLite audit. Distinguish LLM-selected parts from procedural validation.

**1:45–3:00 — Actual execution**: With a working Gemini API key, press Run AI agent (or process one E005/E015 for a fast demonstration). Watch the **Live agent execution** feed. It contains real server event IDs and timestamps, not a fake thinking animation.

**3:00–4:30 — Human email workspace**: Open a processed email. Show original message (left), policy refs and decision explanation, editable draft (right). Show staff assignment, a private note, save, reject/reopen and approval. Every action adds an audit event. Approval does NOT send email.

**4:30–5:30 — Extreme test and robustness**: Import `demo_inputs/E999_complex_unseen.json` through Inbox → Import JSON. Analyze that email. Expected: high priority, human escalation, multiple relevant ESC policy codes and disregard of malicious instructions. Verify under 150 words. Failures route to human review; do not pretend an API outage was successful.

**5:30–6:30 — Policy governance**: Show version history. Change a harmless example rule, publish; older drafts become stale and block approval. Compare policy versions. Do not remove critical ESC codes.

**6:30–7:30 — Analytics & audit**: Show real category distributions, status chart, daily output activity, filtering and JSON/CSV audit export. Distinguish model confidence from measured accuracy.

**7:30–8:30 — Gmail architecture and restrictions**: Gmail OAuth can optionally import from a specifically permitted TEST inbox. In competition mode it is read-only and sending is disabled by design. A Sent state would require provider-confirmed ID outside the event (do not demo real sending during competition).

**8:30–9:30 — Questions & limitations**: Offline tests cover safety, workflow, roles, policy versions and mocked provider transitions; Gemini/Gmail live integration requires credentials. The current app is local-first single-computer, not enterprise certified. Production would use Postgres, durable queues, SSO and managed secrets.

## Common judge questions

- **How is this not a static script?** Gemini dynamically plans policy topics and classifications for each unseen input; the orchestrator calls retrieval and validation tools based on results. Critical emergency rules are fixed safety constraints, not sample-ID decision tables.
- **How are mistakes handled?** Invalid AI JSON/citations or model failure produces conservative escalation rather than a hallucinated approval. Malicious instructions inside student text are not authoritative.
- **Why human-in-the-loop if autonomous?** One-command autonomous triage fulfills the challenge. Human review happens *after* that run; staff approval is a separate workflow.
- **How can we verify?** Run unseen E999 and inspect `outbox.json`, individual policy codes, `agent_log.jsonl`, and SQLite audit exports.
- **Why does dashboard say unsent?** The task expressly prohibits sending to real people. Drafts are the deliverable.
