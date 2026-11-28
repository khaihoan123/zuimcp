"""audit_log: the account each entry is about, and account changes

- connection_id, account_name, service_type, account_target: the account an
  entry is about, copied at the time (account_target is what it pointed at,
  e.g. the mailbox address or the Odoo host and database).
- actor: 'agent' (an MCP tool call), 'user' (a change in the dashboard) or
  'system' (ZuiMCP itself, e.g. a refused login). action names the change.
- tool_name becomes optional: dashboard changes call no tool.
- inferred: the account was filled in afterwards, see below.

Rows logged before this have no account. Where the user had exactly one
account for the tool's service at the time (email_* -> imap/gmail, odoo_* ->
odoo), that is the account the call used: fill it in and mark it inferred.
Rows where several accounts were possible stay empty; the log never guesses.

Revision ID: 7c2e9b4d1a3f
Revises: 1f0d7df65c12
Create Date: 2026-11-27 04:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7c2e9b4d1a3f'
down_revision: Union[str, Sequence[str], None] = '1f0d7df65c12'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('audit_log', sa.Column('actor', sa.String(length=10), server_default='agent', nullable=False))
    op.add_column('audit_log', sa.Column('action', sa.String(length=50), nullable=True))
    op.add_column('audit_log', sa.Column('connection_id', sa.Integer(), nullable=True))
    op.add_column('audit_log', sa.Column('account_name', sa.String(length=255), nullable=True))
    op.add_column('audit_log', sa.Column('service_type', sa.String(length=50), nullable=True))
    op.add_column('audit_log', sa.Column('account_target', sa.String(length=500), nullable=True))
    op.add_column('audit_log', sa.Column('inferred', sa.Boolean(), server_default=sa.false(), nullable=False))
    op.alter_column('audit_log', 'tool_name', existing_type=sa.String(length=100), nullable=True)
    op.create_foreign_key(
        'audit_log_connection_id_fkey', 'audit_log', 'connections',
        ['connection_id'], ['id'], ondelete='SET NULL',
    )
    op.create_index('ix_audit_log_connection_id', 'audit_log', ['connection_id'])

    op.execute(
        r"""
        WITH candidates AS (
            SELECT a.id AS log_id, c.id AS connection_id, c.display_name, c.service_type,
                   count(*) OVER (PARTITION BY a.id) AS possible
            FROM audit_log a
            JOIN connections c
              ON c.user_id = a.user_id
             AND c.created_at <= a.created_at
             AND (
                  (a.tool_name LIKE 'email\_%' AND c.service_type IN ('imap', 'gmail'))
               OR (a.tool_name LIKE 'odoo\_%' AND c.service_type = 'odoo')
             )
        )
        UPDATE audit_log a
           SET connection_id = k.connection_id,
               account_name = k.display_name,
               service_type = k.service_type,
               inferred = true
          FROM candidates k
         WHERE a.id = k.log_id AND k.possible = 1
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DELETE FROM audit_log WHERE tool_name IS NULL")
    op.drop_index('ix_audit_log_connection_id', table_name='audit_log')
    op.drop_constraint('audit_log_connection_id_fkey', 'audit_log', type_='foreignkey')
    op.alter_column('audit_log', 'tool_name', existing_type=sa.String(length=100), nullable=False)
    for column in ('inferred', 'account_target', 'service_type', 'account_name',
                   'connection_id', 'action', 'actor'):
        op.drop_column('audit_log', column)
