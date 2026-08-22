.PHONY: init up prod down logs test smoke backup sbom checksums package

VERSION := $(shell python3 -c "import tomllib; print(tomllib.load(open('apps/api/pyproject.toml', 'rb'))['project']['version'])")

init:
	@test ! -f .env || (echo '.env already exists' && exit 0)
	python3 scripts/generate_env.py

up:
	docker compose up -d --build

prod:
	docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=200

test:
	PYTHONPATH=apps/api python3 -m pytest apps/api/tests
	node --check apps/web/app.js
	python3 -m compileall -q apps/api/projectvault scripts

smoke:
	GPB_API_TOKEN=$$(grep '^GPB_API_TOKEN=' .env | cut -d= -f2-) scripts/smoke_test.sh

backup:
	scripts/backup.sh

sbom:
	python3 scripts/generate_sbom.py > SBOM.spdx.json

checksums:
	python3 scripts/checksums.py > SHA256SUMS

package: test sbom checksums
	mkdir -p dist
	zip -qr dist/gpt-project-bridge-$(VERSION).zip . -x '.git/*' 'data/*' 'backups/*' 'dist/*' '.env' '*.pyc' '__pycache__/*'
