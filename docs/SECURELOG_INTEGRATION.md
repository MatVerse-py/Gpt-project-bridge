# MatVerse SecureLog: opt-in integration v1

Scope: app/main.py and app/institutional_service.py. The new code imports app.securelog_adapter and configures an HTTP lifecycle middleware at app construction. The mode defaults to off, and no external SecureLog package is imported unless audit is explicitly enabled. No edits to app/core.py, Ω-Gate, EvidenceOS, ledger or app/auth.py.

## Installation (staging only until independently verified)

The reviewed package is provided outside this Git repository as **matverse-securelog-agentskill-v1.1-hardened.zip**. Obtain and validate the original archive separately, then install its runtime with pip (local path, no deployment secrets).

1. pip install -e /path/to/matverse-securelog/assets/runtime
2. Create a keyring and journal using SecureLog.initialize(log_path, keyring_path) in **different private directories**.
3. Ensure only the service account can read the keyring and journal; never commit these files.
4. Set the following environment variables for both processes if both are enabled:
   - MATVERSE_SECURELOG_MODE=enforce
   - MATVERSE_SECURELOG_LOG_PATH=/absolute/private/logs/audit.fernetlog
   - MATVERSE_SECURELOG_KEYRING_PATH=/absolute/private/keys/keys.json
   - MATVERSE_SECURELOG_MAX_BYTES=16777216 (optional)
5. Restart in staging, exercise the protected APIs, verify the journal and recovery procedure.
6. Keep mode off until the upstream runtime is installed in the exact deployment image and volume permissions are verified.

## Verification

- Run: python -m pytest -q tests/test_securelog_adapter.py.
- If the optional runtime is not installed, the two encrypted-flow tests are **skipped**; a green baseline CI is not proof of active encryption.
- Audit failed startup for missing/invalid journal, credentials, or corrupted ciphertext.
- Before production, perform load testing and archive/restore with external anchors.

## Limits and behavior

- HTTP events contain only a generated correlation identifier, component, status code and coarse lifecycle categories. They do not contain path, headers, query, request/response bodies, or IP.
- HTTP status 401 or 403 appears in the journal but does **not** demonstrate whether authentication was denied by any particular internal policy. Semantic auth instrumenting is a separate follow-up.
- No new authority, receipts or scientific validity claims are created by Fernet.
- The existing runtime scans the full journal on each append (O(n) per event); a 16 MiB maximum limits growth but is not a throughput guarantee.
- Fernet tokens expose creation timestamps; external anchors are required to detect total or tail deletion.
- An audit write may fail after the underlying operation has committed. Do not automatically retry a business operation based on the HTTP failure alone.
- Streaming after initial response headers is not covered by this middleware.
- Explicit output status: WIRING_COMPLETE, DEPLOYMENT_HOLD.
