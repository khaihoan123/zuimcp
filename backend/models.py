import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    String,
    Table,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db import Base

Table("user", Base.metadata, Column("id", String(255), primary_key=True))


class Connection(Base):
    """A user's connection to an external service (mailbox, Odoo, etc.).

    user_id references Better Auth's "user" table, which this codebase does
    not model directly — Better Auth owns that table's schema and migrations.
    """

    __tablename__ = "connections"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("user.id", ondelete="CASCADE"), index=True
    )
    service_type: Mapped[str] = mapped_column(String(50))
    display_name: Mapped[str] = mapped_column(String(255))
    # Fernet-encrypted JSON blob (access/refresh tokens, IMAP password, etc.)
    # — never stored in plaintext. See core/security.py for encrypt/decrypt.
    credentials_encrypted: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_used_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    permissions: Mapped[list["Permission"]] = relationship(
        back_populates="connection", cascade="all, delete-orphan"
    )


class Permission(Base):
    """A single capability toggle for a connection, e.g. ("email.send", True).

    Capabilities are namespaced free-text strings, not an enum, so new
    services (Odoo, etc.) can introduce their own capabilities without a
    migration.
    """

    __tablename__ = "permissions"
    __table_args__ = (UniqueConstraint("connection_id", "capability"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    connection_id: Mapped[int] = mapped_column(
        ForeignKey("connections.id", ondelete="CASCADE"), index=True
    )
    capability: Mapped[str] = mapped_column(String(100))
    allowed: Mapped[bool] = mapped_column(Boolean, default=False)

    connection: Mapped["Connection"] = relationship(back_populates="permissions")


class AuditLog(Base):
    """The activity log: what happened to the user's accounts, never changed afterwards.

    actor:
    - 'agent': one MCP tool call (core.middleware.AuditLoggingMiddleware).
    - 'user': a change in the dashboard (core.audit.record_account_event).
    - 'system': ZuiMCP itself, e.g. flagging a refused login.

    The account an entry is about is copied at the time: its name, connector
    type and target (mailbox address, Odoo host + database + version). So an
    entry keeps saying what was touched after the account is renamed, edited
    or disconnected. connection_id is only a link to the account, emptied when
    it is disconnected. `inferred` marks entries whose account was filled in
    afterwards (see migration 7c2e9b4d1a3f).

    No tool arguments or secrets by design: arguments can contain email bodies
    (PII). `detail` holds an error message or a description of a change.
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("user.id", ondelete="CASCADE"), index=True
    )
    actor: Mapped[str] = mapped_column(String(10), default="agent", server_default="agent")
    # Dashboard/system changes: e.g. account.connected, permission.on (core.audit).
    action: Mapped[str | None] = mapped_column(String(50), nullable=True)
    tool_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    capability: Mapped[str | None] = mapped_column(String(100), nullable=True)
    connection_id: Mapped[int | None] = mapped_column(
        ForeignKey("connections.id", ondelete="SET NULL"), nullable=True, index=True
    )
    account_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    service_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    account_target: Mapped[str | None] = mapped_column(String(500), nullable=True)
    inferred: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    status: Mapped[str] = mapped_column(String(20))  # success | rate_limited | error
    detail: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
