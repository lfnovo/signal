# Security policy

Please report vulnerabilities privately through GitHub's **Report a vulnerability** flow for this
repository. Do not open a public issue with credentials, exploit details or personal source data.

Only the latest release is supported with security fixes. Signal is designed for one user. Hosted
instances must set `SIGNAL_PASSWORD`, use HTTPS through a reverse proxy, keep SurrealDB private and
protect `.env` plus the persistent data volumes. Compromise of the host or database is outside the
application's isolation boundary.
