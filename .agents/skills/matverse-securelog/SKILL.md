---
name: matverse-securelog
description: Use when adding encrypted Fernet audit logging, HTTP lifecycle telemetry, secure keyring provisioning, or AgentSkill validation to MatVerse Python/FastAPI. Prefer opt-in configuration and fail-closed startup while keeping auth, Ω-Gate, EvidenceOS and ledger unchanged.
---

# MatVerse SecureLog integration

Scope: instrument the existing Python/FastAPI entrypoints through `app.securelog_adapter.configure_securelog`.

- Discovery: inspect the repository and confirm intended service targets before changes.
- Default: MATVERSE_SECURELOG_MODE=off; never activate in production without explicit approval.
- Enforce: requires installed reviewed matverse-fernet-logs Python runtime and initialized *private* journal and keyring in distinct directories.
- Never copy keys or user data into a repository, trace, tests, CI artifacts, or prompt.
- Preserve constitutional authorization and proof semantics; HTTP audit is NOT an authorization decision, evidence receipt, or witness.
- Use docs/SECURELOG_INTEGRATION.md for environment variables and tests.
- Run: python -m pytest -q tests/test_securelog_adapter.py; when the optional runtime is installed, run the encrypted-flow tests.
- Release status without real runtime provisioning and validation: HOLD_PRODUCTION.
