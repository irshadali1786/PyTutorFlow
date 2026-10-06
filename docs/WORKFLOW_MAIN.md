# MAIN workflow (Gmail)

`n8n/workflow_MAIN_gmail.json` - one workflow, two flows. Setup: [GMAIL_SETUP.md](GMAIL_SETUP.md).

**Incoming mail:** `Gmail Trigger` (every minute, INBOX, full format) -> `Extract email` (sender, Message-ID, Gmail id, subject, text body)
-> `Loop Over Emails` -> `POST /email/incoming` (X-API-Key) -> `Prepare reply` -> `Anything to send?` -> `Is student reply?`
-> `Reply to student (Gmail)` (same thread) or admin alert -> `Check delivery` -> `Delivery failed?` -> `Next email`.
If FastAPI fails, the `Build FastAPI-failure alert` branch emails the admin (max one per 10 min) and the loop continues.

**Daily lessons / catch-up:** `Every 5 minutes` -> `GET /daily/due` -> `Split Out students` -> `POST /students/{id}/daily-message`
-> `Prepare Gmail messages` -> `Send student Gmail` -> `Build delivery results` -> `POST /students/{id}/daily-result`
-> `Build admin alerts` -> `Send admin alert (Gmail, daily)`. Skips and errors are also recorded through daily-result.

**FastAPI /email/incoming** takes `{sender, message_id, subject, body}` and returns
`{outcome, send, to, subject, body, admin_alert}`. Outcomes: PASS, LESSON_DONE, MASTERED, RETRY, WEAK, FINISHED, COMMAND,
BAD_INPUT, UNKNOWN_USER, DUPLICATE, IGNORED (robots such as no-reply), ERROR. `send=false` means nothing is emailed.

Settings: `ADMIN_EMAIL` (env), credentials `FastAPI X-API-Key` and `Gmail account (learning system)`, Error Workflow = Workflow C.
