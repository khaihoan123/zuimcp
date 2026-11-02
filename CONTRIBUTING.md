# Contributing

Thanks for helping out. Bug reports, fixes and new connectors are all welcome.

## Before you start

- For anything bigger than a small fix, open an issue first so we can agree on the approach.
- Contributions require signing the Contributor License Agreement (CLA). The bot will ask on
  your first pull request. The CLA lets the project be offered under other licenses (for
  example a commercial license) alongside AGPL-3.0; you keep the copyright to your work.

## Development

See [README.md](README.md#local-development) for setup.

- Backend: keep tools thin; every tool must pass its `capability` when resolving a
  connection, and new tools need an entry in `TOOL_CAPABILITY` (`backend/core/middleware.py`).
- Database changes go through Alembic: `uv run alembic revision --autogenerate -m "..."`.
- Frontend: run `npm run check` before opening a pull request.
- Never commit `.env` files, credentials, or real mailbox data in tests or screenshots.

## Pull requests

- One topic per pull request, with a short description of what changed and why.
- Describe how you tested it (which MCP client, which mail server).
