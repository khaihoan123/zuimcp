# Deploy

Docker Compose: Caddy (TLS + routing) in front of the SvelteKit app, the FastAPI
resource server, and one Postgres shared by both.

## Layout

| Service  | Role                                        | Published |
|----------|---------------------------------------------|-----------|
| caddy    | TLS, routes both domains                    | 80, 443   |
| frontend | SvelteKit + Better Auth (auth server)       | no        |
| backend  | FastAPI + FastMCP (resource server)         | no        |
| db       | Postgres, shared schema                     | no        |

Two domains, both A records to the server:

- `APP_DOMAIN` — dashboard, marketing, `/auth/*`, `/oauth2/*`, and `/api/*`
  (Caddy proxies `/api/*` to the backend, replacing Vite's dev proxy).
- `MCP_DOMAIN` — `/mcp` and its protected-resource metadata.

They are separate because both SvelteKit's OAuth discovery documents and
FastMCP's metadata must sit at their domain's root path.

## First deploy

```bash
cp .env.example .env   # then fill it in
```

Generate the two secrets:

```bash
openssl rand -base64 32                                                        # BETTER_AUTH_SECRET
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # CREDENTIALS_ENCRYPTION_KEY
```

Migrations run in order — Better Auth creates `user`, then Alembic adds
`connections`/`permissions`, which have a foreign key to `user.id`:

```bash
docker compose --profile migrate run --rm auth-migrate
docker compose --profile migrate run --rm backend-migrate
docker compose up -d --build
```

## Social sign-in (optional)

Leave `GOOGLE_CLIENT_ID`/`GITHUB_CLIENT_ID` blank in `.env` to skip a
provider — email/password keeps working regardless. To enable one, register
an OAuth app and set its redirect URI to
`https://<APP_DOMAIN>/auth/callback/<provider>`:

- **Google** — [console.cloud.google.com/apis/credentials](https://console.cloud.google.com/apis/credentials),
  create an OAuth client ID (Web application).
- **GitHub** — [github.com/settings/developers](https://github.com/settings/developers),
  a new **OAuth App** (not a GitHub App).

## Gmail mailboxes (optional)

"Connect with Google" on the Connections page needs its **own** Google OAuth
client, separate from the sign-in one above. Leave `GMAIL_OAUTH_CLIENT_ID`
blank to turn Gmail off.

1. In [console.cloud.google.com](https://console.cloud.google.com), create (or
   pick) a project.
2. **APIs & Services → Library**: enable the **Gmail API**.
3. **Google Auth Platform → Branding / Audience** (the OAuth consent screen):
   user type **External**, publishing status **Testing**. Add your app name,
   support email and domain.
4. **Data Access**: add the scope `https://www.googleapis.com/auth/gmail.modify`
   (the only one the app asks for).
5. **Audience → Test users**: add every Google address that will connect
   (at most 100 while in Testing).
6. **Clients → Create client**: type **Web application**. Authorized redirect
   URIs:
   - `https://<APP_DOMAIN>/api/connections/gmail/oauth/callback`
   - for local dev, `http://localhost:5173/api/connections/gmail/oauth/callback`
7. Put the client id and secret in `.env` as `GMAIL_OAUTH_CLIENT_ID` and
   `GMAIL_OAUTH_CLIENT_SECRET` (for local dev: in `backend/.env`).

`gmail.modify` is a Google **restricted** scope. While the app is in Testing,
only the listed test users can connect, and Google expires their refresh
tokens after 7 days — the account then shows **needs reconnecting** in the
dashboard. Opening it to everyone needs Google's app verification plus a
yearly third-party security assessment.

## Notes

- `CREDENTIALS_ENCRYPTION_KEY` has no rotation support (`core/security.py`).
  Back it up. If it changes, every stored mailbox credential is unrecoverable.
- `MCP_RESOURCE_INDICATOR` (frontend) and `MCP_RESOURCE_URL` + `MCP_PATH`
  (backend) must match exactly. compose.yaml derives both from `MCP_DOMAIN` so
  they cannot drift.
- Environment variables override `backend/config.yaml`, which keeps the
  local-dev defaults.
- Postgres is only on the internal network. To back it up:
  `docker compose exec db pg_dump -U mcp mcp | gzip > backup.sql.gz`

## Redeploy

```bash
git pull && docker compose up -d --build
```

Run the migrate commands again first if either migration set changed.
