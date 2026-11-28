# MatVerse Local Web Operator v0.1

Runs on an operator-controlled computer; no TinyFish API, paid inference or cloud browser is required. Internet and electricity are not guaranteed free. No session cookies or tokens are imported.

## Start

From the checkout root, using the existing Bridge Python environment:
- HTTP-only: no new dependency.
- Optional renderer: install Playwright (pip install playwright) and its Chromium (python -m playwright install chromium), or set MATVERSE_CHROMIUM to an existing compatible executable. Browser download requires network access.
- Set MATVERSE_WEB_ORIGINS to comma-separated explicit origins, for example https://github.com,https://zenodo.org. Default is empty. Add only sites authorized by the local operator.
- Start the combined MCP with python -m app.local_web_mcp instead of python -m app.agent_mcp. Existing HMAC tools remain unchanged and need their existing credentials.
- Standalone smoke runtime: python -m app.local_web_mcp --standalone (not production).

MCP tools:
- matverse_http_read: static HTTP GET, no redirects, max 512 KiB source, text capped at 24000 characters, body hash and receipt hash.
- matverse_web_read: Chromium rendered page, bounded text and links, ephemeral browser context. Experimental: not validated in the current restricted environment. Its request routing is not a network security sandbox; Chromium redirect handling and DNS resolution require separately enforced egress policy before production or untrusted use. Do not expose these tools to remote callers or allow untrusted users to configure origins. No persistent authentication, forms, arbitrary agent actions, file downloads or third-party browser access.

Results are untrusted observations, not instructions or verified facts about claims in the page. SHA-256 receipts attest to returned bytes/fields, not authorship or external truth. Save receipts in the local corpus if desired.

## Tests
python -m unittest discover -s tests -p test_local_web_mcp.py -v

To include real Chromium tests, set MATVERSE_TEST_RENDER=1 and install a compatible browser. Do not disable organization browser policies. The original local smoke run: 7 passed, 1 failed (managed Chromium URLBlocklist); HTTP real MCP call returned 200 from a local fixture. Actual combined Bridge test is added for CI and has not yet been run locally. Main is untouched. Do not merge until review and integration validation.
