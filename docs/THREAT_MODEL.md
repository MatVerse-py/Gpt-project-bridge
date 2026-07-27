# Threat Model

## Protected assets

- imported conversations and files;
- project membership and provenance;
- internal web-to-API token;
- OIDC access tokens;
- SQLite database and backups;
- audit records.

## Main threats and controls

| Threat | Control |
|---|---|
| Browser token theft | Token is held only by the web proxy and injected upstream. |
| Unauthorized MCP reads | OIDC signature, issuer, audience, expiration and scope validation. |
| ZIP Slip / traversal | Canonical destination validation rejects absolute and parent paths. |
| Decompression bomb | Count, member-size and aggregate expanded-size limits. |
| Archive symlink escape | Symlink members are rejected. |
| False project attribution | Only explicit metadata or owner mapping; conflicts abort. |
| Secret leakage in Git | `.env` ignored; examples contain no valid secret. |
| Container privilege escalation | Non-root API image, dropped capabilities, read-only filesystems and `no-new-privileges`. |
| Data exfiltration through telemetry | No external telemetry is configured by default. |
| Multi-writer SQLite corruption | Architecture explicitly prohibits concurrent API replicas. |

## Residual risks

- The official export format can change and requires parser updates.
- Static internal tokens must be rotated when exposed to an untrusted operator.
- OIDC provider availability affects external MCP access.
- Extracted text from complex documents may be incomplete; the source hash and provenance remain available.
- The declared-dependency SBOM is not a transitive vulnerability scan. Production CI should add Syft/Grype or an equivalent scanner.
