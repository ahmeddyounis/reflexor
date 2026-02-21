.DEFAULT_GOAL := help

PYTHON ?= python3
VENV ?= .venv
VENV_BIN := $(VENV)/bin
PY := $(VENV_BIN)/python
VENV_MARKER := $(VENV)/.installed

.PHONY: help venv lint format typecheck test coverage ci clean

help:
	@echo "Targets:"
	@echo "  venv       Create venv and install dev deps"
	@echo "  format     Auto-format with ruff"
	@echo "  lint       Lint with ruff"
	@echo "  typecheck  Run mypy"
	@echo "  test       Run pytest"
	@echo "  coverage   Run tests with coverage report"
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
	$(PY) -m pytest --cov

ci: $(VENV_MARKER)
	$(PY) -m ruff format --check .
	$(PY) -m ruff check .
	$(PY) -m mypy src
	$(PY) -m pytest --cov

clean:
	rm -rf $(VENV) .pytest_cache .mypy_cache .ruff_cache dist build *.egg-info .coverage coverage.xml htmlcov
