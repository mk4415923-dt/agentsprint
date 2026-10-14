# Enterprise operator checklist

**1. Start with RUN.bat:** Create a local administrator password at the hidden prompt. Sign in to the dashboard.

**2. Connect Gmail:** In Google Cloud Console enable Gmail API, configure OAuth consent, create a Web OAuth client and allow `http://127.0.0.1:8765/api/gmail/callback`. Paste the client ID and secret in Settings. Click Connect / Reauthorize Gmail. Grant both read and send permissions.

**3. Enable delivery:** In Settings, click **Enable live delivery**. The app cannot send without an authenticated admin and the Gmail `send` scope.

**4. Test:** Import a message from a mailbox you control (send yourself a sample inquiry). Analyze → Edit draft → Approve → **Send with Gmail**. Check Gmail Sent folder, the AEGIS case status and the audit log.

**5. Troubleshooting:**
- *Read-only Gmail connection*: Reauthorize in Settings and accept Gmail send permission.
- *Enable button disabled*: Connect Gmail and grant send permission; refresh the page.
- *Unauthorized*: Sign in as administrator (RUN.bat) rather than launching the demo server.
- *Sent not shown*: The app records Sent only when Google responds with a message ID.
- *Timeout after sending*: The outcome may be unknown; inspect your Gmail Sent folder before retrying.
- *App opens old UI*: Shut down old RUN.bat instances, restart with the new RUN.bat and press Ctrl+Shift+R.

**Security note:** This build is intended for controlled local operation, not direct public Internet exposure. Use appropriate consent and data governance before processing real student email.

**Existing data migration:** If you already used AEGIS V3, stop both instances, then run `MIGRATE_FROM_V3.bat` in the new folder. Enter the full path to your older extracted AEGIS folder. The migration backs up files in the new folder before replacing them, transfers the old SQLite database, policy, outbox, logs and JSON inbox, and never transfers OAuth tokens. Start the new app afterward.
