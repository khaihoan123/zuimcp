"""Picks the EmailProvider for the account a tool call resolves to."""

from core.accounts import Account, resolve_account
from services.email.providers.base import EmailProvider, MessageFilter
from services.email.providers.gmail import GmailProvider
from services.email.providers.imap import ImapProvider, get_email_credentials, imap_credentials

__all__ = ["EmailProvider", "MessageFilter", "open_provider", "provider_for"]


def provider_for(account: Account) -> EmailProvider:
    """The provider class is chosen by the connection's service_type."""
    if account.service_type == "imap":
        return ImapProvider(imap_credentials(account.credentials, account.name))
    if account.service_type == "gmail":
        return GmailProvider(account.id, account.name, account.credentials)
    raise ValueError(f"Email provider '{account.service_type}' is not supported")


def open_provider(user_id: str | None, account: str | None, capability: str) -> EmailProvider:
    """The provider for the one mailbox this call may use (core.accounts rules).

    Without a user_id (local stdio mode) it's the single IMAP mailbox from .env.
    """
    if not user_id:
        return ImapProvider(get_email_credentials(None, account, capability))
    return provider_for(resolve_account(user_id, "email", account, capability))
