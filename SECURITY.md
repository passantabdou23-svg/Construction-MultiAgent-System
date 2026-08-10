# Security policy

## Supported scope

Security updates apply to the current `main` branch. This repository is a controlled,
single-workstation academic prototype; it is not an internet-facing approval service.

In scope are authentication and authorization bypasses, separation-of-duties failures,
audit-chain integrity defects, unsafe secret handling, dependency risks, database
integrity failures, and paths that allow unapproved procurement or scheduling actions.

## Reporting a vulnerability

Do not place passwords, database contents, project records, or exploit details in a public
issue. Contact the repository owner privately, or use GitHub private vulnerability
reporting when it is enabled. Include the affected commit, reproduction steps, impact,
and the smallest safe evidence needed to validate the report.

## Existing controls

- Passwords are derived with uniquely salted `scrypt`; raw passwords are not stored.
- Local accounts have server-validated roles, lockout, idle expiry, and absolute expiry.
- Package preparation and approval require different authenticated users.
- Approval requires fresh password verification and is single-use.
- Procurement and scheduling remain blocked until an authorized approval is recorded.
- SQLite foreign keys, immutable snapshots, and an ordered SHA-256 audit chain detect
  invalid state and ordinary event tampering.
- Release backups use SQLite's online-backup API and include a SHA-256 integrity manifest.
- Runtime databases, secrets, generated indexes, and backups are excluded from Git.

## Trust boundary and limitations

Local operating-system and database administrators remain trusted. The audit chain is
tamper-evident, not externally anchored or digitally signed. The application does not
provide MFA, enterprise SSO, account recovery, legal electronic signatures, encrypted
backup custody, or a managed multi-user database. Use localhost by default and an HTTPS
reverse proxy before any LAN exposure.

Approved Documents A, C, K, and 7 apply to England. Retrieved citations are technical
guidance, not regulatory approval; procurement outputs are unverified estimates; and the
system does not replace review by a licensed engineer.
