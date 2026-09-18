"""
Loads the generated parquet files into the raw layer of the target warehouse.

Two targets are supported and the dbt models are identical for both:

    python -m ingest.load                      # DuckDB  (default, no account needed)
    python -m ingest.load --target snowflake   # Snowflake

Why this module exists at all, rather than pointing dbt straight at the parquet files:
it mirrors how a real landing layer works. Source extracts arrive as files, get loaded
into raw tables that mirror the source one-for-one, and the load is instrumented so you
can prove what landed. dbt then reads TABLES, not files, so the models stay
engine-agnostic.

It also reproduces a real failure worth showing. Snowflake's write_pandas does not quote
identifiers, so a source table named ENTITY -- a reserved word -- fails to load. The
tempting fix is to rename the table. That is the wrong fix: it permanently breaks the
mapping back to source, so every future attempt to trace a column becomes guesswork.
The right fix is to quote the identifier. See load_snowflake() below.
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

import pandas as pd

RAW_DIR = os.path.join("data", "raw")
LABEL_DIR = os.path.join("data", "labels")
DUCKDB_PATH = os.path.join("data", "edw.duckdb")
RAW_SCHEMA = "raw"

# Ground-truth defect labels land here, deliberately apart from the raw layer.
#
# This separation is the point. sources.yml declares nothing in this schema, so no dbt
# model can reference it even by accident. The warehouse is built without ever seeing
# which rows are defective; the labels exist only so that detection can be scored
# afterwards. On a real platform this schema is where your confirmed incident log lives.
ML_SCHEMA = "ml"

# Identifiers that are reserved in one or more target engines and therefore must be
# quoted on load. Deliberately not renamed.
RESERVED = {"entity", "order", "table", "select", "group", "values", "case"}


def parquet_files(src: str) -> list[str]:
    files = sorted(glob.glob(os.path.join(src, "*.parquet")))
    if not files:
        sys.exit(
            f"No parquet files found in {src!r}.\n"
            "Run 'python -m ingest.generate' first."
        )
    return files


# --------------------------------------------------------------------------- duckdb
def load_duckdb(files: list[str], db_path: str, schema: str = RAW_SCHEMA) -> list[tuple[str, int, str]]:
    import duckdb

    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    con = duckdb.connect(db_path)
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")

    results = []
    for path in files:
        table = os.path.splitext(os.path.basename(path))[0]
        # Always quote. Costs nothing and removes a whole class of failure.
        fq = f'{schema}."{table}"'
        con.execute(f"DROP TABLE IF EXISTS {fq}")
        con.execute(
            f"CREATE TABLE {fq} AS SELECT * FROM read_parquet(?)",
            [path.replace("\\", "/")],
        )
        n = con.execute(f"SELECT COUNT(*) FROM {fq}").fetchone()[0]
        note = "quoted (reserved word)" if table.lower() in RESERVED else ""
        results.append((table, n, note))

    con.close()
    return results


# --------------------------------------------------------------------------- snowflake
def load_snowflake(files: list[str], database: str, schema: str) -> list[tuple[str, int, str]]:
    """
    Loads via write_pandas, falling back to a quoted CREATE + COPY for any table whose
    name is a reserved word. Connection comes from environment variables so nothing
    sensitive lives in the repo.
    """
    try:
        import snowflake.connector
        from snowflake.connector.pandas_tools import write_pandas
    except ImportError:
        sys.exit(
            "snowflake-connector-python is not installed.\n"
            "Install the optional extra:  pip install -r requirements-snowflake.txt"
        )

    required = ["SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_WAREHOUSE", "SNOWFLAKE_ROLE"]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        sys.exit("Missing environment variables: " + ", ".join(missing))

    con = snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        password=os.environ.get("SNOWFLAKE_PASSWORD"),
        authenticator=os.environ.get("SNOWFLAKE_AUTHENTICATOR", "snowflake"),
        warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
        role=os.environ["SNOWFLAKE_ROLE"],
    )
    cur = con.cursor()
    cur.execute(f'CREATE DATABASE IF NOT EXISTS "{database}"')
    cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{database}"."{schema}"')
    cur.execute(f'USE SCHEMA "{database}"."{schema}"')

    results = []
    for path in files:
        table = os.path.splitext(os.path.basename(path))[0]
        df = pd.read_parquet(path)
        df.columns = [c.upper() for c in df.columns]
        target = table.upper()

        if table.lower() in RESERVED:
            # write_pandas would emit unquoted DDL here and fail. Create the table with
            # quoted identifiers, then load. Source name preserved.
            cols = ", ".join(f'"{c}" {_sf_type(df[c])}' for c in df.columns)
            cur.execute(f'CREATE OR REPLACE TABLE "{target}" ({cols})')
            success, _chunks, nrows, _out = write_pandas(
                con, df, target, quote_identifiers=True, auto_create_table=False
            )
            note = "quoted fallback (reserved word)"
        else:
            success, _chunks, nrows, _out = write_pandas(
                con, df, target, quote_identifiers=True, auto_create_table=True,
                overwrite=True,
            )
            note = ""

        if not success:
            sys.exit(f"Load failed for {table}")
        results.append((table, nrows, note))

    cur.close()
    con.close()
    return results


def _sf_type(series: pd.Series) -> str:
    import pandas.api.types as t

    if t.is_integer_dtype(series):
        return "NUMBER(38,0)"
    if t.is_float_dtype(series):
        return "FLOAT"
    if t.is_datetime64_any_dtype(series):
        return "TIMESTAMP_NTZ(9)"
    if t.is_bool_dtype(series):
        return "BOOLEAN"
    return "VARCHAR(16777216)"


# --------------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(description="Load raw parquet into the target warehouse.")
    ap.add_argument("--target", choices=["duckdb", "snowflake"], default="duckdb")
    ap.add_argument("--src", default=RAW_DIR)
    ap.add_argument("--labels", default=LABEL_DIR)
    ap.add_argument("--db", default=DUCKDB_PATH, help="DuckDB file path")
    ap.add_argument("--database", default=os.environ.get("SNOWFLAKE_DATABASE", "DEMO_EDW"))
    ap.add_argument("--schema", default=RAW_SCHEMA.upper())
    args = ap.parse_args()

    files = parquet_files(args.src)
    print(f"Loading {len(files)} source extracts into target '{args.target}'\n")

    if args.target == "duckdb":
        results = load_duckdb(files, args.db)
        where = args.db
    else:
        results = load_snowflake(files, args.database, args.schema)
        where = f"{args.database}.{args.schema}"

    print(f"{'table':<26}{'rows loaded':>13}  note")
    print("-" * 78)
    for table, n, note in results:
        print(f"{table:<26}{n:>13,}  {note}")
    print("-" * 78)
    print(f"{'TOTAL':<26}{sum(r[1] for r in results):>13,}")
    print(f"\nLoaded into: {where}")

    # Ground-truth labels, into their own schema. Optional: absent if the generator was
    # run without them, in which case the ML evaluation simply cannot be scored.
    label_files = sorted(glob.glob(os.path.join(args.labels, "*.parquet")))
    if label_files:
        if args.target == "duckdb":
            lab = load_duckdb(label_files, args.db, schema=ML_SCHEMA)
        else:
            lab = load_snowflake(label_files, args.database, ML_SCHEMA.upper())
        for table, n, _note in lab:
            print(f"Ground truth: {ML_SCHEMA}.{table:<22}{n:>10,} rows  "
                  "(not declared in sources.yml -- unreachable from dbt)")
    else:
        print(f"No label files in {args.labels} -- ML scoring will be unavailable.")

    print("\nNext:  dbt build --profiles-dir ." + ("" if args.target == "duckdb" else " --target snowflake"))


if __name__ == "__main__":
    main()
