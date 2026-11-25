PYTHON ?= python3
VENV   ?= .venv
BIN     = $(VENV)/bin

.PHONY: install test lint typecheck check serve replay eval clean

install:            ## create the virtualenv and install with dev tools
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install -q -e ".[dev]"

test:               ## run the test suite
	$(BIN)/pytest

lint:               ## style and static checks
	$(BIN)/ruff check src tests
	$(BIN)/ruff format --check src tests

typecheck:
	$(BIN)/mypy

check: lint typecheck test   ## everything CI runs

serve:              ## run the API on :8000, state in ./var
	$(BIN)/broker-serve --data-dir var

replay:             ## regenerate docs/RESULTS.md
	$(BIN)/broker-replay --out docs/RESULTS.md

eval:               ## regenerate docs/RETRIEVAL_RESULTS.md
	$(BIN)/broker-eval-retrieval --out docs/RETRIEVAL_RESULTS.md

clean:
	rm -rf $(VENV) var .pytest_cache .mypy_cache .ruff_cache src/*.egg-info
