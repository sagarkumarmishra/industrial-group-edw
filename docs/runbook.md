# Runbook

Everything needed to build this from nothing, plus what to do when a step fails.

---

## Requirements

| | |
|---|---|
| Python | 3.10 or newer (built on 3.12) |
| Disk | ~350 MB for the DuckDB file and parquet |
| Warehouse | None. DuckDB is a local file and needs no account. |
| Time | ~90 seconds for a full cold build |

No Snowflake account, no credentials and no cloud cost are required to run any of this.

---

## Install

```bash
git clone https://github.com/sagarkumarmishra/industrial-group-edw.git
cd industrial-group-edw

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1

pip install -r requirements.txt        # dbt, duckdb, pandas
pip install -r requirements-ml.txt     # scikit-learn, matplotlib, scipy, reportlab
```

The two requirement files are separate on purpose: the warehouse builds and passes all 70
tests without any ML dependency installed. If you only want to see the dimensional model,
you can skip the second file entirely.

---

## Build everything

```bash
make all
```

That runs the full chain. To do it by hand, or to understand what `make` is doing:

```bash
python -m ingest.generate            # 1. synthesise source extracts + ground truth
python -m ingest.load                # 2. land raw parquet into DuckDB
dbt build --profiles-dir .           # 3. transform and run 70 tests
python -m ml.detect_anomalies        # 4. score anomaly detection against SQL rules
python -m ml.volume_monitor          # 5. detect volume outages and spikes
python -m ml.charts                  # 6. regenerate every figure
python -m docs.build_data_dictionary # 7. regenerate the data dictionary
python -m docs.build_report_pdf      # 8. build the PDF report
```

### Individual targets

| Command | Does |
|---|---|
| `make data` | generate and load only |
| `make build` | `dbt build` |
| `make test` | `dbt test` only, no rebuild |
| `make ml` | data + build + both ML scripts |
| `make charts` | regenerate the five figures |
| `make docs` | data dictionary + PDF |
| `make clean` | delete `data/`, `target/`, `logs/` |
| `make all` | everything, in order |

---

## What a healthy run looks like

### Step 1 — generate

```
inventory_cost_txn           200,079  sub-day grain, bad qty, prices, dupes, epoch (3,4,5,9,10)
TOTAL                        396,055

Ground truth -> data\labels\defect_labels.parquet
CONTEXTUAL_PRICE                 162
CORRUPT_QUANTITY                  57
EPOCH_DATE                       300
SOURCE_DUPLICATE                   9
LABELLED TOTAL                   528
```

The row counts vary by a few rows between platforms because the volume-incident injection
depends on floating-point timestamp arithmetic. The defect counts are stable.

### Step 3 — dbt build

```
Found 21 models, 1 analysis, 70 data tests, 10 sources, 623 macros
58 of 91 WARN 9 inventory_cost_grain_is_unique_with_timestamp
Done. PASS=90 WARN=1 ERROR=0 SKIP=0 NO-OP=0 TOTAL=91
```

**The single warning is expected and must not be "fixed".** It reports the nine genuine
duplicate rows in the Rauma Service Partners extract — trap 9. The test is configured
`warn_if: ">0"` and `error_if: ">9"`, so it surfaces the known baseline on every run and
fails the build the moment duplicates get worse. Making it pass silently would mean
asserting the source is clean, which it is not.

### Step 4 — detection

```
CORRUPT_QUANTITY  (57 rows)
  sql_rule_quantity_gt_1e6      precision 1.000  recall 1.000  F1 1.000
  isolation_forest              precision 0.097  recall 0.684  F1 0.170

CONTEXTUAL_PRICE  (162 rows)
  sql_rule_unit_cost_p99.9      precision 0.537  recall 0.667  F1 0.595
  isolation_forest              precision 0.362  recall 0.895  F1 0.515
  isolation_forest ranking (avg prec)  0.831

  top-20 alert budget           precision 1.000  recall 0.123
```

The forest losing to the rule on corrupt quantity is the correct result, not a bug. See
[ml-anomaly-detection.md](ml-anomaly-detection.md).

### Step 5 — volume monitor

```
5,400 company-days checked   Poisson residual threshold 5.0
5 anomalous company-days found

2024-05-15  Aalborg Fluid Systems     0   38   -6.1  DROP
2024-05-16  Aalborg Fluid Systems     0   36   -6.0  DROP
2024-05-17  Aalborg Fluid Systems     0   36   -6.0  DROP
2024-09-12  Gotland Valve Company   157   36   19.9  SPIKE
2024-09-13  Gotland Valve Company    94   37    9.4  SPIKE
```

Exactly five, all genuine. If you see twelve, the Poisson residual has been swapped back for
a z-score.

---

## Running against Snowflake instead

The models are engine-portable. No DuckDB-specific syntax is used.

```bash
export SNOWFLAKE_ACCOUNT=xy12345.ap-southeast-2
export SNOWFLAKE_USER=your_user
export SNOWFLAKE_PASSWORD=your_password
export SNOWFLAKE_ROLE=your_role
export SNOWFLAKE_WAREHOUSE=your_wh
export SNOWFLAKE_DATABASE=DEMO_EDW

python -m ingest.load --target snowflake
dbt build --profiles-dir . --target snowflake
```

**There are no credentials in this repository.** `profiles.yml` reads all six values from
the environment. If any are unset the connection fails rather than falling back to
something.

The ML scripts read DuckDB directly and are not wired to Snowflake. Pointing them at it
means changing the connection in `ml/features.py` and `ml/volume_monitor.py`; the feature
logic is engine-agnostic pandas.

---

## Troubleshooting

### `data/edw.duckdb not found`

Run steps 1 and 2 first. The ML scripts read the built marts, not the parquet.

```bash
python -m ingest.generate && python -m ingest.load && dbt build --profiles-dir .
```

### `CatalogException: Table fct_inventory_transaction_cost does not exist`

`dbt build` has not completed. On Windows PowerShell, chaining with `;` does **not** wait
for the previous command in every case — run the build as its own command and confirm you
see the `Done.` line before continuing.

### `No labels found. Re-run ingest.generate`

`ml.defect_labels` is missing. Most likely the generator ran from an older version that did
not emit labels, or `data/labels/` was deleted. Regenerate and reload.

### dbt cannot find `profiles.yml`

Pass `--profiles-dir .`. The profile is committed in the project root rather than
`~/.dbt/`, so the repository is self-contained. Every `dbt` command in this project needs
that flag.

### `ImportError: No module named sklearn`

`requirements-ml.txt` is not installed. The warehouse builds fine without it; only steps 4
to 8 need it.

### Charts are unreadable, or `matplotlib` complains about a display

`ml/charts.py` forces the `Agg` backend, so it works headless. If you have overridden
`MPLBACKEND` in your environment, unset it.

### The unique test errors instead of warning

Duplicates have exceeded nine. That is the test doing its job — something upstream changed.
Find them before touching the threshold:

```sql
select company_key, item_key, warehouse_key, cost_component_key,
       txn_timestamp, line_seq, count(*)
from main_marts.fct_inventory_transaction_cost
group by 1,2,3,4,5,6
having count(*) > 1
order by 7 desc;
```

### A deprecation warning about `accepted_values` top-level arguments

Cosmetic, from dbt 1.12 tightening test argument syntax. It does not affect results and the
test still runs.

---

## Verifying the numbers yourself

Every published figure is regenerated from the database, so you can check any of them.

**Row counts by layer**

```sql
select 'source' as layer, count(*) from raw.inventory_cost_txn
union all
select 'staging', count(*) from main_staging.stg_inventory_cost_txn
union all
select 'mart', count(*) from main_marts.fct_inventory_transaction_cost;
```

**The 192× unit trap**

```sql
select c.company_name,
       sum(t.work_hours)            as correct_hours,
       sum(t.work_hours_as_sourced) as naive_hours,
       sum(t.work_hours_as_sourced) / sum(t.work_hours) as overstatement
from main_marts.fct_labour_hours t
join main_marts.dim_company c on t.company_key = c.company_key
group by 1 order by 4 desc;
```

**Why the date-truncated grain fails**

```sql
select count(*) as collapsed_rows
from (
    select company_key, item_key, warehouse_key, cost_component_key,
           cast(txn_timestamp as date) as txn_date, line_seq, count(*) as n
    from main_marts.fct_inventory_transaction_cost
    group by 1,2,3,4,5,6
    having count(*) > 1
);
```

**Machine-readable ML results**

```bash
cat data/ml/metrics.json
```

---

## Regenerating documentation after a change

```bash
make charts        # figures, from the current run's outputs
make docs          # data dictionary + PDF
```

The data dictionary is generated from the live schema and the dbt manifest, so it cannot
drift from the models. Do not edit `docs/data-dictionary.md` by hand — it is overwritten.

---

## Clean rebuild

```bash
make clean && make all
```

Deletes `data/`, `target/` and `logs/`, then rebuilds everything. Worth doing before
trusting any published figure.
