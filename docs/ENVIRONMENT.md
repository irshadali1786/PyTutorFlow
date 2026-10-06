# Environment, secrets and versions

## One rule

Real secrets exist in exactly two places: your local `.env` (git-ignored) and your password manager
(and later inside n8n's encrypted credential store). They never appear in `.env.example`, docs, scripts,
tests, commits, chat messages or screenshots.

## Variables

"Read by" tells you who actually uses the value today.

| Variable | Secret? | Read by | Purpose |
|---|---|---|---|
| `API_KEY` | **yes** | FastAPI, `smoke_test.sh` | Required header `X-API-Key` for every endpoint except `/health`. n8n sends it. |
| `N8N_BLOCK_ENV_ACCESS_IN_NODE` | no | n8n | Set to `false` by `scripts/start_n8n.sh` so the workflows can read `ADMIN_EMAIL`. Security trade-off: any workflow node can then read n8n's environment, which is acceptable for a single-user local install. |
| `N8N_ENCRYPTION_KEY` | **yes** | `start_n8n.sh` -> n8n | Encrypts n8n's stored credentials. Back it up; never change it later. |
| `FASTAPI_BASE_URL` | no | `smoke_test.sh`, n8n (later) | `http://127.0.0.1:8000`. Must stay loopback. |
| `N8N_LISTEN_ADDRESS` | no | `start_n8n.sh` | Must be `127.0.0.1`; the script refuses anything else. |
| `N8N_PORT` | no | `start_n8n.sh` | Default `5678`. |
| `N8N_USER_FOLDER` | no | `start_n8n.sh` | Optional. Default `~/.n8n-learning-system`; must be outside the project. |
| `API_HOST` / `API_PORT` | no | FastAPI | Keep `127.0.0.1` / `8000`. |
| everything else | no | FastAPI | Engine settings from Phase 1 (timezone, send time, limits, DB paths, sandbox). Unchanged. |

## How `.env` is read

- Python reads it with a small built-in reader (`app/config.py`): one `KEY=value` per line, no spaces around
  `=`, **no trailing `# comments`**, optional surrounding quotes. A variable exported in your shell overrides the file.
- The two shell scripts parse the same file **without executing it** (so odd characters can't run commands).
- **n8n does not read `.env`.** `start_n8n.sh` passes it the few settings it needs.
- Python ignores variables it doesn't know, so the `N8N_*` lines are harmless to the API.

## Generating secrets

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"      # run once per secret; never reuse one
```

Rotate `API_KEY` by changing `.env`, restarting FastAPI, and updating the n8n credential. If a Gmail OAuth
credential ever leaks, revoke it in your Google Account (Security > Third-party access) and reconnect it in n8n.

## Versions

| Component | Requirement | Status |
|---|---|---|
| Python | **3.10+ required**, 3.12 recommended (`.python-version`) | 3.10 floor comes from reading the code (pydantic models use `int \| None` at runtime); not tested on 3.10 or 3.11 |
| Python packages | minimum versions in `requirements.txt` | not yet locked; see below |
| SQLite | the version bundled with Python | schema uses only basic SQLite features (WAL, partial indexes) |
| Node.js | an LTS release inside `npm view n8n engines` | **fill in:** ______ |
| n8n | pinned at install time | **fill in:** ______ |
| macOS | any currently supported release | **fill in:** ______ (not tested by me) |

### Locking exact Python versions (recommended once tests pass on your Mac)

```bash
source .venv/bin/activate
python -m pytest -q                       # must pass first
pip freeze > requirements.lock.txt        # commit this file
# later, to rebuild identically:  pip install -r requirements.lock.txt
```

I deliberately did not invent pinned version numbers: they must come from a set that you have actually
installed and tested.

## Checklist before committing anything

```bash
git status                       # .env must NOT be listed
grep -rnE "^(API_KEY|N8N_ENCRYPTION_KEY)=." . \
  --exclude-dir=.venv --exclude-dir=.git --exclude=.env      # should print nothing
```
