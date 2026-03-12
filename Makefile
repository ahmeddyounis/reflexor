.DEFAULT_GOAL := help

PYTHON ?= python3
VENV ?= .venv
VENV_BIN := $(VENV)/bin
PY := $(VENV_BIN)/python
VENV_MARKER := $(VENV)/.installed

.PHONY: help venv lint format typecheck test coverage audit-deps db-upgrade prod-preflight validate-manifests docker-build ci clean

help:
	@echo "Targets:"
	@echo "  venv       Create venv and install dev deps"
	@echo "  format     Auto-format with ruff"
	@echo "  lint       Lint with ruff"
	@echo "  typecheck  Run mypy"
	@echo "  test       Run pytest"
	@echo "  coverage   Run tests with coverage report"
	@echo "  audit-deps Run pip-audit and enforce the dependency gate"
	@echo "  db-upgrade Run alembic migrations (upgrade head)"
	@echo "  prod-preflight Validate prod-oriented settings via the CLI"
	@echo "  validate-manifests Validate Kubernetes deployment manifests"
	@echo "  docker-build Build the production runtime image"
	@echo "  ci         Run format-check, lint, typecheck, coverage"
	@echo "  clean      Remove venv and caches"

$(VENV_MARKER): pyproject.toml
	$(PYTHON) -m venv $(VENV)
	$(VENV_BIN)/python -m pip install -U pip
	$(VENV_BIN)/python -m pip install -e ".[dev]"
	@touch $@

venv: $(VENV_MARKER)

format: $(VENV_MARKER)
	$(PY) -m ruff format .

lint: $(VENV_MARKER)
	$(PY) -m ruff check .

typecheck: $(VENV_MARKER)
	$(PY) -m mypy src

test: $(VENV_MARKER)
	$(PY) -m pytest

coverage: $(VENV_MARKER)
	$(PY) -m pytest --cov=reflexor.domain

audit-deps: $(VENV_MARKER)
	mkdir -p advisory
	$(PY) -m pip_audit -s osv -f json --desc off --aliases on --progress-spinner off -o advisory/pip-audit.json
	$(PY) scripts/pip_audit_gate.py --audit-json advisory/pip-audit.json --allowlist .github/pip-audit-allowlist.txt --min-severity high

db-upgrade: $(VENV_MARKER)
	$(PY) -m reflexor.infra.db.migrate upgrade

prod-preflight: $(VENV_MARKER)
	$(VENV_BIN)/reflexor --profile prod config validate --strict --json

validate-manifests: $(VENV_MARKER)
	$(PY) scripts/validate_k8s_manifests.py deploy/k8s

docker-build:
	docker build -f docker/Dockerfile -t reflexor:dev .

ci: $(VENV_MARKER)
	$(PY) -m ruff format --check .
	$(PY) -m ruff check .
	$(PY) -m mypy src
	$(PY) -m pytest --cov=reflexor.domain
	$(MAKE) audit-deps

clean:
	rm -rf $(VENV) .pytest_cache .mypy_cache .ruff_cache dist build *.egg-info .coverage coverage.xml htmlcov
