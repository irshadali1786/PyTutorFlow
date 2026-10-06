# macOS setup (Python + FastAPI + SQLite)

Everything here is free (₹0) and runs only on your Mac. FastAPI stays on `127.0.0.1:8000`.
Commands are for **Terminal** (zsh, the macOS default). n8n is covered in [N8N_SETUP.md](N8N_SETUP.md),
variables in [ENVIRONMENT.md](ENVIRONMENT.md).

## 1. Prerequisites

1. **Xcode command line tools** (gives you `git` and `curl` tooling): `xcode-select --install`
2. **Homebrew** (free package manager): follow the one-line installer at <https://brew.sh>.
   On Apple Silicon it lives in `/opt/homebrew`, on Intel Macs in `/usr/local`; the installer prints the
   `eval "$(brew shellenv)"` line to add to `~/.zprofile` - do that, then open a new Terminal tab.
3. **Python 3.12** (3.10 is the minimum): `brew install python@3.12`

Check: `python3.12 --version`

## 2. Get the project and create the virtual environment

```bash
cd ~/path/to/learning_system          # the folder that contains app/, tests/, docs/
python3.12 -m venv .venv
source .venv/bin/activate             # prompt now starts with (.venv)
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Every new Terminal tab needs `source .venv/bin/activate` again before you run `python -m app ...`.

## 3. Create your `.env` (secrets live only here)

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Open `.env` in a text editor and paste the printed value after `API_KEY=` (no spaces, no quotes needed).
Leave `N8N_ENCRYPTION_KEY` for now (n8n step uses the last one). `.env` is git-ignored.
Do not paste the key into chat, screenshots or commits.

## 4. Run the tests

```bash
python -m pytest -q
```

Phase 1 shipped with **93 tests** and the README says to expect `93 passed`. Treat that number as the
target; if anything fails on your Mac, stop and send me the output before going further.

## 5. SQLite setup

SQLite is built into Python, so there is nothing to install. The database file is created for you:

```bash
python -m app init                    # creates data/learning.db, applies schema, loads the curriculum
python -m app add-student "Asha"      # optional now: prints a one-time join code
python -m app students                # progress table
```

- The file is `data/learning.db` (plus `-wal`/`-shm` files while the API runs; this is normal).
- It is git-ignored, and so is `data/backups/`.
- Safe to re-run `init`: it only creates what is missing and reloads the YAML curriculum.
- A manual backup, while the API is running, is `POST /admin/backup` (keeps the last 7 in `data/backups/`).

## 6. Start FastAPI (Terminal tab 1)

```bash
source .venv/bin/activate
python -m app serve
```

It listens on `http://127.0.0.1:8000` only and refuses to start if `API_KEY` is empty. Leave the tab open.
Stop it with `Ctrl+C`. Interactive docs: <http://127.0.0.1:8000/docs> (Authorize with your API key).

## 7. Smoke test (Terminal tab 2)

```bash
cd ~/path/to/learning_system
./scripts/smoke_test.sh
```

Expected: six `PASS` lines and `Result: 6 passed, 0 failed`. It checks that the server is reachable,
`/health` answers without a key, and a protected endpoint returns 401 without a key and with a wrong key,
and 200 with your key. It only calls read-only endpoints and never prints the key.

## 8. Confirm it is local-only

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN
```

The NAME column must read `127.0.0.1:8000` (not `*:8000` and not your Wi-Fi IP). If it does not, check that
`API_HOST` in `.env` is `127.0.0.1` or unset.

## 9. Everyday use

| Task | Command |
|---|---|
| Start API | `source .venv/bin/activate && python -m app serve` |
| Check API | `./scripts/smoke_test.sh` |
| Add student | `python -m app add-student "Name" --time 08:00` |
| Progress | `python -m app students` |
| Stop API | `Ctrl+C` in its tab |

## Troubleshooting

| Symptom | Fix |
|---|---|
| `python3.12: command not found` | `brew install python@3.12`, open a new tab |
| `API_KEY is not set` | `.env` missing or `API_KEY=` empty; also check `echo $API_KEY` is empty (an exported shell variable overrides `.env`) |
| `Address already in use` (port 8000) | `lsof -nP -iTCP:8000 -sTCP:LISTEN` shows who has it; stop that process or change `API_PORT` **and** `FASTAPI_BASE_URL` |
| Smoke test says "cannot reach" | API not running, or wrong `FASTAPI_BASE_URL` |
| Smoke test: correct key gives 401 | the running server was started with an older `.env`; restart it |
| `.env` value ignored | no inline comments and no spaces around `=` (the reader is intentionally tiny) |

## Known limitations on a Mac

- Student code runs in a restricted subprocess with a timeout, import allow-list and CPU/file-size limits.
  macOS may not enforce the memory limit (`RLIMIT_AS`). The code ignores that failure silently. This is fine
  for you and a few trusted students; it is not a hardened sandbox. Never expose the API to the internet.
- If the Mac sleeps, FastAPI (and later n8n) pause. Automatic start-on-login (launchd) comes in a later part.
