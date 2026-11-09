# Python framework security refresh — November 2026

## Finding

The repository declared `fastapi==0.116.1`. Its package metadata constrains Starlette to `starlette<0.48.0,>=0.40.0`. The resolved `starlette==0.47.3` release has published security advisories, including CVE-2025-62727 (quadratic-time Range parsing) and 2026 advisories fixed in Starlette 1.1.0.

FastAPI 0.140.13 and 0.162.0 metadata allow `starlette>=0.46.0` without the old upper bound. As of this refresh, PyPI lists FastAPI 0.162.0 and Starlette 1.10.0 as current releases. FastAPI's own version guidance recommends not pinning Starlette independently; this change therefore updates FastAPI and lets its resolver select a compatible Starlette release, while CI asserts the resolved Starlette is at least 1.1.0.

## Changes in this PR

- Upgrade FastAPI to `0.162.0`.
- Upgrade pytest to `9.0.3` (the version used in the Manus isolated validation).
- Add `httpx2==2.16.0` for Starlette 1.x TestClient, avoiding the deprecation of the legacy httpx TestClient path. Keep `httpx==0.28.1` because it remains an explicit project dependency and may be used by other consumers.
- Add a dedicated GitHub Actions workflow to check resolved framework versions, `pip check`, `pip-audit`, bytecode compilation, and the full test suite.

## Scope and safeguards

This is separate from the ingress P0 PR. It does not change application routes, auth, nonce handling, Ω-Gate, ledger semantics, or the main branch. The dependency changes remain a draft until the CI audit and complete tests pass on this exact branch.

`pip-audit` reports known advisories in the resolved environment; a clean report is not a guarantee of zero risk. Re-run it at release time and retain the generated dependency resolution/SBOM with the release artifacts.

## Validation gate

- [ ] GitHub dependency-security workflow succeeds.
- [ ] Full existing CI succeeds.
- [ ] Resolved Starlette version is >= 1.1.0.
- [ ] No known advisories reported by pip-audit.
- [ ] No regression in API contracts or test behavior.
