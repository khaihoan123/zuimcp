# MCP IMAP Server

**Goal:** Multi-tenant MCP server for live IMAP email access (ephemeral, no storage).
**Tech Stack:** Python (FastAPI, FastMCP, SQLAlchemy, PostgreSQL), SvelteKit (Svelte 5 Runes, Vite, Tailwind CSS).

## Architecture Rules
- Write clean, simple code with minimal comments.
- No email data is ever stored in the database.
- Database only handles users, IMAP credentials, and folder permissions.
- FastMCP handles the tool execution for the LLM.


mcp-imap/
├── backend/
│   ├── src/
│   │   ├── __init__.py
│   │   ├── database.py      # SQLAlchemy engine/session
│   │   ├── models.py        # User, IMAPSettings, Permissions
│   │   ├── email_client.py  # Stateless imap-tools logic
│   │   └── main.py          # FastMCP tools & FastAPI routes
│   ├── .env
│   ├── init_db.py           # Migration script
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── lib/             # API helpers/components
│   │   ├── routes/          # +page.svelte (Dashboard)
│   │   └── app.html
│   ├── vite.config.ts       # Proxy configuration
│   └── package.json
└── README.md                # Project context for AI

## Current State
- **Backend:** FastAPI server running with PostgreSQL.
- **Frontend:** SvelteKit proxying `/api` to backend to bypass CORS.

## Development Setup

### Backend (Port 8000)
```bash
cd backend
uv run fastapi dev main.py