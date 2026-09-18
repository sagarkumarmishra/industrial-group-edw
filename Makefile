# Convenience targets. Every one is a thin wrapper -- the underlying commands are listed in
# docs/runbook.md, so nobody has to read a Makefile to understand the project.
#
#   make all       full rebuild from nothing: data, warehouse, ML, charts, docs
#   make build     transform and test only
#   make ml        detection and volume monitoring
#   make charts    regenerate every figure from the current run
#   make report    regenerate the data dictionary and the PDF
#   make snowflake full rebuild against Snowflake (requires env vars)

DBT := dbt --profiles-dir .
ROWS ?= 200000

.PHONY: all setup setup-ml generate load deps build test data ml detect volume \
        charts report dictionary pdf demo docs grain clean snowflake

# The full chain, in dependency order. Charts read what ML wrote; the PDF reads what the
# charts wrote. Running these out of order produces a report full of stale figures, so the
# ordering here is the contract.
all: data build ml charts report demo

setup:
	pip install -r requirements.txt

setup-ml:
	pip install -r requirements-ml.txt

# ------------------------------------------------------------------ data
generate:
	python -m ingest.generate --rows $(ROWS)

load:
	python -m ingest.load

data: generate load

# ------------------------------------------------------------------ warehouse
deps:
	$(DBT) deps

build: deps
	$(DBT) build

test:
	$(DBT) test

# ------------------------------------------------------------------ ml
detect:
	python -m ml.detect_anomalies

volume:
	python -m ml.volume_monitor

ml: detect volume

charts:
	python -m ml.charts

# ------------------------------------------------------------------ docs
dictionary:
	python -m docs.build_data_dictionary

pdf:
	python -m docs.build_report_pdf

report: dictionary pdf

demo:
	python demo_defects.py

# The one-query grain diagnosis. Run this before writing any fact table.
grain:
	$(DBT) compile -s prove_the_grain
	@echo "Compiled query is in target/compiled/industrial_group_edw/analyses/prove_the_grain.sql"

# dbt's own generated docs site, separate from the hand-written docs/ directory.
docs:
	$(DBT) docs generate
	$(DBT) docs serve

clean:
	$(DBT) clean
	rm -rf data/raw/*.parquet data/labels/*.parquet data/ml \
	       data/edw.duckdb data/edw.duckdb.wal \
	       build.log demo.txt ml_run.txt vol_run.txt chart_run.txt pdf_run.txt

# Requires SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PASSWORD,
# SNOWFLAKE_ROLE, SNOWFLAKE_WAREHOUSE, SNOWFLAKE_DATABASE.
snowflake: generate
	python -m ingest.load --target snowflake
	$(DBT) deps
	$(DBT) build --target snowflake
