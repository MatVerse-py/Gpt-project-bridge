# MatVerse Local Web Operator + PHOENIX Receipts v0.2

The local operator is the primary no-per-call-fee web acquisition adapter. TinyFish remains optional and is not called by this service.

## Current capabilities

- `matverse_http_read`: bounded HTTP GET for HTML, plain text and JSON; explicit origin allowlist; direct connection to a pinned DNS result; rejects non-public DNS answers except an explicitly allowlisted loopback fixture; redirects disabled; response limited to 512 KiB; extracted text limited to 24,000 characters.
- `matverse_web_read`: optional Chromium renderer. Disabled by default. Its URL routing is not an OS egress sandbox and must not be used with untrusted pages or remote callers until an independently enforced network sandbox is deployed and tested.
- `matverse_phoenix_verify_receipts`: verifies the local append-only PHOENIX receipt hash chain.

Page content is always `OBSERVACAO_NAO_VERIFICADA` and `UNTRUSTED_PAGE_CONTENT`. The receipt proves what this local process recorded, not authorship, truth, external timestamp or causal validity.

## Start from the existing Bridge checkout

Use the repository's existing Python environment and run:

    export MATVERSE_WEB_ORIGINS="https://github.com,https://zenodo.org"
    python -m app.local_web_mcp

Windows PowerShell:

    $env:MATVERSE_WEB_ORIGINS="https://github.com,https://zenodo.org"
    python -m app.local_web_mcp

The server uses stdio MCP. Register this command in the MCP client that runs locally. Do not expose it as a public HTTP service. Existing Bridge HMAC credentials are needed only for the Bridge's existing remote/agent tools; local web reads do not forward or import those credentials.

## PHOENIX receipt journal

Default directory: `data/phoenix/web-receipts/`.

- `web_receipts.jsonl`: append-only application journal, hash chained.
- `web_receipts.jsonl.lock`: cross-process append lock.
- `objects/<sha256>.bin`: optional captured response bodies, content-addressed and never overwritten.

The journal stores URL hashes and origin, not query strings or page text. By default, response bodies are not retained. To opt in to local body capture:

    export MATVERSE_PHOENIX_CAPTURE_BODY=1

To change the receipt directory:

    export MATVERSE_PHOENIX_RECEIPTS_DIR="/absolute/local/path"

Verify the chain through MCP tool `matverse_phoenix_verify_receipts` or by calling `verify_receipts()` locally.

This journal is tamper-evident, not immutable or independently anchored. Protect the device and export checkpoints to a separately controlled location when needed.

## Network and security limits

- Configure exact origins yourself. The allowlist is empty by default.
- HTTP is accepted only for explicitly allowlisted loopback origins; remote origins must use HTTPS.
- DNS answers for non-loopback hosts are checked and the selected IP is used directly for the connection, reducing DNS-rebinding/TOCTOU exposure in the HTTP engine.
- Redirects are rejected and must be reviewed as a new URL.
- No forms, POST/PUT/DELETE, downloads, cookies, browser profiles, password vault, arbitrary shell, or transactions are exposed.
- The Chromium renderer is experimental and disabled by default. Playwright route filtering is not equivalent to OS-level egress enforcement.
- The HTTP engine is a bounded read adapter, not a complete TinyFish replacement or an autonomous planner.
- Internet, electricity and local hardware are not cost-free; the software makes no per-call TinyFish/API charge.

## Tests

    python -m unittest discover -s tests -p 'test_local_web_mcp.py' -v

Tests use a local loopback HTTP fixture and do not call paid services or external sites. CI is configured separately for the local-web branch. A successful unit test does not prove production egress isolation.
