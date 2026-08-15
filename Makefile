.PHONY: all install test test-fast coverage clean clean-results clean-cache docs sample-bin seed-text-small seed-corpora copy-refbins sweep sweep-rar14 sweep-rar20 sweep-rar29 sweep-rar30 sweep-rar40 sweep-rar50 sweep-rar70 sweep-all-wine sweep-quick triage

VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest
RARS_BENCH := $(VENV)/bin/rars-bench

all: install test coverage

install: $(VENV)/.installed

$(VENV)/.installed: pyproject.toml
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"
	touch $@

test: install
	$(PYTEST)

test-fast: install
	$(PYTEST) -m 'not slow'

coverage: install
	$(PYTEST) --cov=rars_bench --cov-report=term --cov-report=html

sample-bin: install
	./scripts/sample-bin.sh 50

seed-text-small:
	./scripts/seed-text-small.sh

seed-corpora:
	./scripts/seed-corpora.sh

copy-refbins:
	./scripts/copy-refbins.sh

# Wipe everything under results/ (including run sqlite DBs and the work/
# tree of intermediate archives + extract dirs). Useful before a fresh
# sweep so old rows don't bloat the triage tables.
clean-results:
	rm -rf results/*

# Drop the oracle encode cache. Use after upgrading a wineprefix or the
# RAR1_402.EXE binary; otherwise the cache reuses old archives keyed on
# the prior driver `version` string.
clean-cache:
	rm -rf _refs/cache

# Backwards-compat alias — the original slice1 sweep over text-small.
sweep: sweep-rar14

sweep-rar14: install seed-corpora copy-refbins clean-results
	mkdir -p results
	$(RARS_BENCH) sweep --format rar14 --corpus all --out results/rar14.db

sweep-rar20: install seed-corpora copy-refbins clean-results
	mkdir -p results
	$(RARS_BENCH) sweep --format rar20 --corpus all --out results/rar20.db

sweep-rar29: install seed-corpora copy-refbins clean-results
	mkdir -p results
	$(RARS_BENCH) sweep --format rar29 --corpus all --out results/rar29.db

sweep-rar30: install seed-corpora copy-refbins clean-results
	mkdir -p results
	$(RARS_BENCH) sweep --format rar30 --corpus all --out results/rar30.db

sweep-rar40: install seed-corpora copy-refbins clean-results
	mkdir -p results
	$(RARS_BENCH) sweep --format rar40 --corpus all --out results/rar40.db

sweep-rar50: install seed-corpora copy-refbins clean-results
	mkdir -p results
	$(RARS_BENCH) sweep --format rar50 --corpus all --out results/rar50.db

sweep-rar70: install seed-corpora copy-refbins clean-results
	mkdir -p results
	$(RARS_BENCH) sweep --format rar70 --corpus all --out results/rar70.db

# Quick sweep — one canonical format per writer family. rars's 15-40 family
# is exercised by rar40 (latest 4.x oracle), the 50/70 family by rar50.
# rar14 covers the DOS oracle. Skips rar29/rar30/rar70 since they share
# rars writers with the canonical formats and only the oracle byte counts
# differ (typically <1.5% spread across WinRAR 2.90/3.00/4.20, and
# byte-identical across rar50/rar70 today).
sweep-quick: install seed-corpora copy-refbins clean-results
	mkdir -p results
	$(RARS_BENCH) sweep --format rar14 --corpus all --out results/rar14.db
	$(RARS_BENCH) sweep --format rar40 --corpus all --out results/rar40.db
	$(RARS_BENCH) sweep --format rar50 --corpus all --out results/rar50.db

# Run all wine-oracle sweeps in sequence into one combined DB.
sweep-all-wine: install seed-corpora copy-refbins clean-results
	mkdir -p results
	$(RARS_BENCH) sweep --format rar29 --corpus all --out results/all-wine.db
	$(RARS_BENCH) sweep --format rar30 --corpus all --out results/all-wine.db
	$(RARS_BENCH) sweep --format rar40 --corpus all --out results/all-wine.db
	$(RARS_BENCH) sweep --format rar50 --corpus all --out results/all-wine.db
	$(RARS_BENCH) sweep --format rar70 --corpus all --out results/all-wine.db

triage: install
	@db=$$(ls -t results/*.db 2>/dev/null | head -1); \
	if [ -z "$$db" ]; then echo "no results/*.db found — run a sweep first"; exit 2; fi; \
	echo "triaging $$db"; \
	$(RARS_BENCH) triage "$$db" --by all

clean:
	rm -rf $(VENV) .pytest_cache htmlcov .coverage build dist src/*.egg-info

docs:
	@echo "docs target not yet wired up"
