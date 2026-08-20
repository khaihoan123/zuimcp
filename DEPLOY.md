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
- `MCP_DOMAIN` — `/mcp/email` and its protected-resource metadata.

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
