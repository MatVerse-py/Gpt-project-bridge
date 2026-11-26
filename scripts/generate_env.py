from pathlib import Path
import secrets

path = Path('.env')
if path.exists():
    raise SystemExit('.env already exists; refusing to replace secrets')
token = secrets.token_urlsafe(48)
path.write_text(
    'GPB_API_TOKEN=' + token + '\n'
    'GPB_PUBLIC_API_URL=http://localhost:8787\n'
    'GPB_DOMAIN=bridge.example.com\n'
    'PROJECTVAULT_AUTH_MODE=static\n'
    'PROJECTVAULT_OIDC_ISSUER=https://idp.example.com/\n'
    'PROJECTVAULT_OIDC_AUDIENCE=https://bridge.example.com\n'
    'PROJECTVAULT_OIDC_JWKS_URL=https://idp.example.com/.well-known/jwks.json\n'
    'PROJECTVAULT_REQUIRED_SCOPE=projects.read\n',
    encoding='utf-8',
)
print('Created .env with a random internal API token.')
