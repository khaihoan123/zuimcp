import sys

import uvicorn
from fastapi import FastAPI

from api.audit_log import router as audit_log_router
from api.connections import router as connections_router
from core.mcp_server import MCP_PATH, auth, mcp
import services.email.service  # noqa: F401  registers the email_* tools on `mcp`

mcp_app = mcp.http_app(path=MCP_PATH)
well_known_routes = auth.get_well_known_routes(mcp_path=MCP_PATH)

# lifespan=mcp_app.lifespan is required for FastMCP's streamable-http
# transport to start its session manager; omitting it breaks /mcp at
# request time even though the app appears to start fine.
app = FastAPI(title="ZuiMCP Backend", lifespan=mcp_app.lifespan)
app.router.routes.extend(well_known_routes)


@app.get("/api/health")
def health_check():
    return {"status": "healthy"}


app.include_router(connections_router)
app.include_router(audit_log_router)
app.mount("/", mcp_app)


def main():
    # If "stdio" is passed as an argument, run the MCP stdio server
    if len(sys.argv) > 1 and sys.argv[1] == "stdio":
        sys.argv.pop(1)
        mcp.run()
    else:
        # Otherwise, run the combined FastAPI and MCP application using Uvicorn
        uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
