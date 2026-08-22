from pathlib import Path
import json, re, tomllib

project=tomllib.loads(Path('apps/api/pyproject.toml').read_text())['project']
version=project['version']
packages=[]
for dependency in project.get('dependencies',[]):
    name, _, dependency_version=dependency.partition('==')
    spdx_name=re.sub(r'[^A-Za-z0-9.-]+','-',name).strip('-')
    packages.append({'SPDXID':f'SPDXRef-Package-{spdx_name}','name':name,'versionInfo':dependency_version or 'declared','downloadLocation':'NOASSERTION','filesAnalyzed':False})
packages += [
    {'SPDXID':'SPDXRef-Image-Caddy','name':'caddy','versionInfo':'2.8.4-alpine','downloadLocation':'NOASSERTION','filesAnalyzed':False},
    {'SPDXID':'SPDXRef-Image-Python','name':'python','versionInfo':'3.13-slim','downloadLocation':'NOASSERTION','filesAnalyzed':False},
]
doc={'spdxVersion':'SPDX-2.3','dataLicense':'CC0-1.0','SPDXID':'SPDXRef-DOCUMENT','name':'GPT-Project-Bridge-declared-dependencies','documentNamespace':f'https://github.com/MatVerse-py/Gpt-project-bridge/sbom/{version}','creationInfo':{'creators':['Tool: scripts/generate_sbom.py'],'created':'2026-07-27T00:00:00Z'},'packages':packages}
print(json.dumps(doc,indent=2,sort_keys=True))
