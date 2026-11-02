# Security policy

This project holds credentials that give access to people's mailboxes, so we take
security reports seriously.

## Reporting a vulnerability

Please **do not open a public issue**. Report privately through
[GitHub private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
on this repository.

Include what you found, how to reproduce it, and the impact you expect. We aim to reply
within 3 working days and will credit you in the fix unless you prefer otherwise.

## Scope

Especially interested in:

- Bypassing per-account capability checks (e.g. sending when only `email.read` is granted)
- Token handling: JWT verification, audience/resource checks, OAuth flows
- Exposure of stored credentials or `CREDENTIALS_ENCRYPTION_KEY`
- Cross-user access to connections or audit logs

## Supported versions

Only the latest release on `main` receives security fixes.
