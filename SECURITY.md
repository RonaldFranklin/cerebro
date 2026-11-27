# Security policy

## Reporting a vulnerability

Please **do not open a public issue, pull request or discussion** with details of a security problem.

Report it privately through GitHub: open the repository's **Security** tab and choose **Report a vulnerability**.
If that button is unavailable, open an issue that only asks the maintainer for a private contact, without any
technical detail.

Include the affected commit, how you run Cérebro, the steps to reproduce and the impact you expect. Use synthetic
data: never send real tokens, keys or notes.

This is a single-maintainer project. Reports are handled on a best-effort basis, with no guaranteed response time
and no bounty. Please allow time for a fix before disclosing details.

## Supported versions

Only the latest commit on `main` receives fixes.

## Scope

Of particular interest: bypassing authentication or the access ceiling, learning about notes above that ceiling,
writing without the required scope, getting secrets or restricted plaintext into logs, Git or backups, session or
injection flaws in the web UI, and escaping the filesystem or subprocess protections.

## Known limitations

These are documented decisions. See [accepted risks](docs/architecture.md#accepted-risks-owner-decision-2026-11-25),
[the secret scanner's limits](docs/domain-model.md#6-secrets) and
[ADR-0028](docs/adr/0028-token-decides-public-access.md).

- A leaked bearer token works, within its ceiling and scopes, until it is revoked.
- The signed-in owner has full access on the public listener too. Cérebro does not verify the identity layer in
  front of it ([ADR-0029](docs/adr/0029-owner-browser-full-access-on-public-listener.md)).
- Token rows in the state database are protected only by file permissions.
- Raising a note to `restricted` leaves earlier plaintext in Git history and backups.
- The secret scanner is best-effort.
- The application has no per-client request rate limit; add one at the public proxy.
- The search index on the server contains the plaintext of restricted notes.
