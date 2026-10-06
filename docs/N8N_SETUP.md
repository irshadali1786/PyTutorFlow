# Self-hosted n8n on macOS (no Docker, no n8n Cloud, ₹0)

n8n runs as a normal Node.js program on your Mac, bound to **127.0.0.1 only**. No account with n8n,
no cloud, no public URL, no tunnel. This part only installs and starts n8n; the Gmail workflows are imported afterwards ([GMAIL_SETUP.md](GMAIL_SETUP.md)).

Why npm and not Docker: Docker Desktop is a heavier install and a second thing to keep running. A global npm
install is a single command, and your data stays in one plain folder. Docker is not needed.

## 1. Install Node.js (LTS)

n8n refuses to run on Node versions it does not support (brand-new "Current" releases are the usual culprit),
so use an even-numbered **LTS** release.

```bash
brew install node@22
```

`node@22` is "keg-only", so put it on your PATH (the brew output prints the exact line; typically):

```bash
echo 'export PATH="$(brew --prefix node@22)/bin:$PATH"' >> ~/.zprofile
```

Open a new Terminal tab and check `node --version` and `npm --version`. Then ask the npm registry which Node
versions the current n8n accepts:

```bash
npm view n8n engines
```

If your Node version is outside that range, install a different LTS (for example `brew install node@24`) and
repoint the PATH line. I could not verify the supported range from this side, so trust that command, not me.

## 2. Install n8n (pinned)

```bash
npm view n8n version          # note the current version, e.g. 2.x.y
npm install -g n8n@<that-version>
n8n --version
```

Pinning means an accidental update cannot change behaviour under you. Write the version you installed in the
table at the bottom of [ENVIRONMENT.md](ENVIRONMENT.md). If `npm install -g` says `EACCES`, do **not** use `sudo`;
Homebrew's Node normally installs globals without it - re-check that `which npm` points into the Homebrew folder.

## 3. Create the encryption key

n8n encrypts the credentials you store in it (FastAPI API key, Gmail OAuth tokens) with `N8N_ENCRYPTION_KEY`.
Set it **before the first start**.

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Paste the result after `N8N_ENCRYPTION_KEY=` in your `.env`, and **store a copy in a password manager**.
Losing it means every saved n8n credential becomes unreadable and must be re-entered. Never change it
afterwards on an existing n8n folder (n8n will refuse to start with a mismatch).

## 4. Where n8n keeps its data

Default: `~/.n8n-learning-system` (outside the project, so it can never be committed or zipped by accident).
`scripts/start_n8n.sh` creates it with permissions `700` and refuses any folder inside the project.
To change it, uncomment `N8N_USER_FOLDER=` in `.env` and give an absolute path.

## 5. Start n8n

```bash
cd ~/path/to/learning_system
./scripts/start_n8n.sh
```

The script reads `.env` (n8n does not read it by itself), checks the key is set, refuses any listen address
except `127.0.0.1`, and then runs `n8n start` with:

| Setting | Value |
|---|---|
| `N8N_LISTEN_ADDRESS` | `127.0.0.1` |
| `N8N_HOST` / `N8N_PORT` / `N8N_PROTOCOL` | `127.0.0.1` / `5678` / `http` |
| `N8N_USER_FOLDER` | `~/.n8n-learning-system` |
| `N8N_DIAGNOSTICS_ENABLED`, `N8N_VERSION_NOTIFICATIONS_ENABLED`, `N8N_PERSONALIZATION_ENABLED` | `false` |
| `WEBHOOK_URL` | deliberately not set |

Open <http://127.0.0.1:5678>. On first visit n8n asks you to create a local owner account (email + password
stored only on your Mac). If a screen offers a free license key or a paid plan, you can skip it.
Stop n8n with `Ctrl+C`.

## 6. Verify it is local-only

```bash
lsof -nP -iTCP:5678 -sTCP:LISTEN     # NAME must say 127.0.0.1:5678, not *:5678
curl -s http://127.0.0.1:5678/healthz
```

Also confirm from your phone (same Wi-Fi) that `http://<your-mac-ip>:5678` does **not** open.

## 7. Notes for the Gmail workflows

The workflows and the Gmail OAuth credential are covered in [GMAIL_SETUP.md](GMAIL_SETUP.md) and [WORKFLOW_MAIN.md](WORKFLOW_MAIN.md).

- Store the FastAPI key as an n8n **Header Auth credential** (header name `X-API-Key`, credential name `FastAPI X-API-Key`).
  Credentials are encrypted at rest with `N8N_ENCRYPTION_KEY`.
- FastAPI is reached from n8n at `http://127.0.0.1:8000`, same machine, no tunnel, no public URL.

## 8. Backup, update, remove

- **Backup:** stop n8n, copy the whole data folder (`cp -R ~/.n8n-learning-system ~/Backups/`), and keep
  the encryption key in your password manager.
- **Update:** back up first, then `npm install -g n8n@<new-version>`, start, check the UI, update ENVIRONMENT.md.
  Do not skip major versions blindly; read the release notes.
- **Remove:** `npm uninstall -g n8n`; delete the data folder only if you really want to lose workflows/credentials.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `n8n: command not found` | PATH missing the Homebrew Node `bin` folder (step 1), new Terminal tab |
| "Node.js version ... not supported" | install a Node version inside `npm view n8n engines`, re-link/PATH |
| `N8N_ENCRYPTION_KEY is empty` | fill it in `.env` (step 3) |
| Key mismatch error on start | the data folder was created with a different key; restore the original key (or, if there is nothing to keep, delete the folder and start fresh) |
| Port 5678 in use | another n8n is running; `lsof -nP -iTCP:5678 -sTCP:LISTEN`, or change `N8N_PORT` |
| Browser warns about a secure-cookie / https problem | use `http://127.0.0.1:5678` exactly; as a last resort for this local-only setup, add `N8N_SECURE_COOKIE=false` to your shell before starting |
| Mac sleeps, n8n stops | expected for now; start-on-login via launchd is a later part |

## Cost and licence

No n8n Cloud account, no paid features, no Docker, no tunnel service: ₹0. Self-hosting the community edition
is free for personal/internal use under n8n's Sustainable Use License; re-read it if this ever becomes a
paid service for others.
