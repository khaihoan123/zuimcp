# ZuiMCP

Give AI agents (Claude, n8n, any MCP client) safe access to your email, with you
deciding exactly what each agent may do.

- **Per-account, per-action permissions** — e.g. let an agent read and draft, but never send.
- **Standard MCP OAuth 2.1** — clients sign in through the browser (discovery, dynamic
  client registration, PKCE). No API keys to copy around.
- **Activity log** — every tool call is recorded with its capability and result.
- **Rate limiting** — per-user cap on tool calls, so a runaway agent loop can't hammer your mailbox.
- **No email stored** — messages are fetched live; only encrypted connection credentials are kept.

> Status: early. IMAP/SMTP and Gmail (native API, OAuth) work today; a Microsoft 365 adapter is planned.

## How it works

```
MCP client ──OAuth 2.1──▶ frontend (SvelteKit + Better Auth)   issues JWTs, dashboard
     │                                                          
     └──Bearer JWT──────▶ backend (FastAPI + FastMCP)            verifies JWT, runs tools
                                   │
                                   └──▶ your mailbox (IMAP / SMTP, Gmail API)
```

| Part | Stack | Role |
|---|---|---|
| `frontend/` | SvelteKit, Svelte 5, Tailwind v4, shadcn-svelte, Better Auth | Sign-in, OAuth authorization server, dashboard |
| `backend/` | FastAPI, FastMCP, SQLAlchemy, Alembic | MCP resource server at `/mcp`, REST API for the dashboard |
| Postgres | — | Users and sessions (Better Auth), connections, permissions, audit log |

## MCP tools

One endpoint, `/mcp`, serves every service. Tools are named `<service>_<action>`.

| Tool | Capability |
|---|---|
| `list_accounts` | none (lists the user's accounts and what each allows) |
| `email_list_folders`, `email_list`, `email_read`, `email_get_attachment` | `email.read` |
| `email_search` | `email.search` |
| `email_draft` | `email.draft` |
| `email_send` | `email.send` |
| `email_mark_seen` | `email.mark_seen` |

Each connected account has its own set of granted capabilities, managed on the dashboard; all are off by default. An agent only sees the tools at least one of the user's accounts allows.

Every tool takes an `account` argument. With one account per service it can be left out. With several, it is required and never guessed: a call without it fails and lists the choices, so an agent can't act on the wrong account by accident.

## Local development

Requirements: Python 3.12+ with [uv](https://docs.astral.sh/uv/), Node 20+, Postgres.

```bash
# 1. Frontend (auth server + dashboard), http://localhost:5173
cd frontend
cp .env.example .env          # fill in DATABASE_URL and BETTER_AUTH_SECRET
npm install
npx @better-auth/cli migrate  # creates the user/session/oauth tables
npm run dev

# 2. Backend (MCP + API), http://localhost:8000
cd backend
# create backend/.env with DATABASE_URL and CREDENTIALS_ENCRYPTION_KEY
uv sync
uv run alembic upgrade head   # run after the Better Auth migration (foreign key to user.id)
uv run fastapi dev main.py
```

Then sign up at http://localhost:5173, connect a mailbox, and add
`http://localhost:8000/mcp` as a custom connector in your MCP client.

Generate secrets with:

```bash
openssl rand -base64 32                                                                    # BETTER_AUTH_SECRET
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # CREDENTIALS_ENCRYPTION_KEY
```

Defaults live in `backend/config.yaml`; environment variables override them.

### Running tests

The backend tests need Postgres. They use a separate database whose name ends in
`_test` (by default your `DATABASE_URL` with `_test` appended — create it once with
`createdb mcp_platform_test`), and drop all its tables on every run.

```bash
cd backend && uv run pytest              # or set TEST_DATABASE_URL explicitly
cd frontend && npm run check
```

GitHub Actions runs both on every push and pull request.

### Single-user stdio mode

For a quick local test without the dashboard, set `EMAIL_ADDRESS` and `EMAIL_PASSWORD`
in `backend/.env`, point `IMAP_HOST`/`SMTP_HOST` in `config.yaml` at your server, and run:

```bash
uv run python main.py stdio
```

## Self-hosting

Docker Compose with Caddy for TLS — see [DEPLOY.md](DEPLOY.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Security issues: [SECURITY.md](SECURITY.md).

## License

[AGPL-3.0](LICENSE).
