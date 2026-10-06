# Gmail setup (free, local on your Mac)

```
Student Gmail -> n8n Gmail Trigger (polls inbox) -> FastAPI POST /email/incoming
   -> existing evaluator + SQLite -> n8n Gmail "Reply" -> Student Gmail
```
Daily lessons: n8n (every 5 min) -> GET /daily/due -> POST /students/{id}/daily-message -> n8n Gmail "Send"
-> POST /students/{id}/daily-result. Admin alerts and Workflow C errors are Gmail emails to `ADMIN_EMAIL`.

## 1. Environment
`.env` (project folder, git-ignored): `API_KEY`, `N8N_ENCRYPTION_KEY`, `ADMIN_EMAIL` (see `.env.example`).
No Gmail password/token is stored anywhere in the project. Gmail is connected by OAuth inside n8n.

## 2. Start (two terminals)
    cd learning_system && source .venv/bin/activate && python -m app serve      # FastAPI
    cd learning_system && ./scripts/start_n8n.sh                                 # n8n -> http://127.0.0.1:5678

The database upgrades itself on start (adds `students.email` and the `processed_emails` table).

## 3. n8n credentials (Credentials -> Add)
1. **Header Auth** named `FastAPI X-API-Key`: header name `X-API-Key`, value = your `API_KEY`.
2. **Gmail OAuth2 API** named `Gmail account (learning system)`: create a Google Cloud OAuth client (type *Web application*,
   redirect URI `http://localhost:5678/rest/oauth2-credential/callback`, Gmail API enabled, your tutor Gmail added as test user),
   paste Client ID/Secret into n8n, click *Sign in with Google* and allow access. No password is used.

## 4. Import and activate
1. Import `n8n/workflow_MAIN_gmail.json` and `n8n/workflow_C_error_handler_gmail.json` (Workflows -> Import from file).
2. In both, open every node with a credential warning and select the credentials above:
   - MAIN: `Gmail Trigger`, `Reply to student (Gmail)`, `Send admin alert (Gmail, incoming)`, `Alert: delivery failure (Gmail)`,
     `Alert: FastAPI failure (Gmail)`, `Send student Gmail`, `Send admin Gmail (daily)`, `Send admin alert (Gmail, daily)` -> Gmail OAuth2;
     `POST /email/incoming`, `GET /daily/due`, `POST /students/{id}/daily-message`, `POST /students/{id}/daily-result` -> Header Auth.
   - C: `Send admin alert` -> Gmail OAuth2.
3. MAIN -> Settings -> **Error Workflow** = `Workflow C - Global error handler`. Save.
4. Activate **MAIN** only. C is never activated by itself: it runs because MAIN points to it.

## 5. Students (admin)
    python -m app add-student "Asha" --email asha@example.com --time 08:00
    python -m app set-email "Asha" asha@example.com          # attach/change an email for an existing student
    python -m app students                                  # progress table

The sender address of an incoming email must equal the student's registered email (case-insensitive).
Unknown senders get one polite "not registered" reply per day and you get an alert email; nothing breaks.

## 6. What the student does
- Daily at their send time they get an email "Python lesson: ...".
- They **reply to that email** with only their answer (code, or just the letter for a quiz). Plain-text mode is best so indentation survives.
- Commands (as the first line of the reply): `/hint`, `/lesson`, `/next`, `/progress`, `/help`.
- The system replies in the same thread with the result.

## 7. Duplicates
FastAPI stores each email's Message-ID in `processed_emails` before grading. The same email is never evaluated twice.

## Note
If FastAPI is down when an email arrives, n8n's Gmail Trigger will not hand that email over again. MAIN emails you the sender and subject;
ask the student to write again.
