from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse

from app.zenodo_bridge import ZenodoAuthError, runtime_from_env

app = FastAPI(
    title="MatVerse Zenodo OAuth Bridge",
    version="1.0.0",
    description="User-authorized Zenodo OAuth callback for the governed MatVerse publication bridge.",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "matverse-zenodo-oauth"}


@app.get("/zenodo/status")
def status() -> dict:
    try:
        _, store, _, _, _ = runtime_from_env()
        return store.status()
    except ZenodoAuthError as exc:
        return {"connected": False, "configuration_error": str(exc)}


@app.get("/zenodo/oauth/start")
def oauth_start():
    try:
        _, _, state_manager, oauth, _ = runtime_from_env()
        state = state_manager.issue()
        return RedirectResponse(oauth.authorization_url(state), status_code=302)
    except ZenodoAuthError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/zenodo/oauth/callback", response_class=HTMLResponse)
def oauth_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
):
    if error:
        message = error_description or error
        raise HTTPException(status_code=400, detail=f"Zenodo authorization denied: {message}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing OAuth code/state")
    try:
        config, store, state_manager, oauth, _ = runtime_from_env()
        state_manager.validate_once(state)
        token_payload = oauth.exchange_code(code)
        store.save_token_response(token_payload)
        scopes = store.status().get("scopes", [])
    except ZenodoAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    mcp_port = int(os.getenv("ZENODO_MCP_PORT", "8800"))
    return HTMLResponse(
        """
        <!doctype html>
        <html lang="pt-BR">
        <head><meta charset="utf-8"><title>MatVerse × Zenodo</title></head>
        <body>
          <h1>Zenodo conectado ao MatVerse</h1>
          <p>A autorização OAuth foi concluída. Nenhum token foi exibido no navegador.</p>
          <p>Escopos autorizados: <code>{scopes}</code></p>
          <p>Agora o servidor MCP pode usar a sessão Zenodo em <code>http://127.0.0.1:{mcp_port}/mcp</code>.</p>
          <p>Você pode fechar esta janela.</p>
        </body>
        </html>
        """.format(scopes=" ".join(scopes), mcp_port=mcp_port)
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.zenodo_oauth_app:app",
        host=os.getenv("ZENODO_OAUTH_HOST", "127.0.0.1"),
        port=int(os.getenv("ZENODO_OAUTH_PORT", "8788")),
        reload=False,
    )
