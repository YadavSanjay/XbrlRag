# Security policy

XbrlRag is a personal project maintained on a best-effort basis.

## Reporting a vulnerability

Please report a vulnerability privately through GitHub's **Report a vulnerability** button on
the repository's **Security** tab (private vulnerability reporting), not in a public issue.
Include the affected file or endpoint, steps to reproduce, and the impact you expect.

There is no fixed response time. Only the latest commit on `main` gets fixes.

## Scope notes

- The REST API (`/api`, `/api/v1`) and the MCP server have **no authentication**: they assume a
  trusted network. The compose stack binds every port to `127.0.0.1`; put an authenticating
  gateway in front before exposing either beyond your machine.
- Every answer spends LLM tokens on the operator's provider keys. A per-run budget caps eval
  runs, but an exposed API lets anyone who reaches it spend them.
- Filing text retrieved from SEC EDGAR is treated as untrusted data in prompts and SQL. A way
  around that is in scope.
- Never put real credentials in an issue, pull request or commit. Secrets belong in `.env`
  (gitignored) or a `<NAME>_FILE` mount.
