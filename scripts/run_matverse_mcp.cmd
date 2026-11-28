@echo off
setlocal
cd /d "%~dp0\.."
if not defined MATVERSE_WEB_ORIGINS (
  echo MATVERSE_WEB_ORIGINS is empty. Configure explicit HTTPS origins in your MCP client environment.
  echo Example: https://github.com,https://zenodo.org,https://orcid.org,https://arxiv.org
)
set PYTHONUTF8=1
python -m app.matverse_mcp
