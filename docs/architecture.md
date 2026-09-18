# Architecture

Four layers, each with exactly one job. The discipline of not mixing those jobs is what keeps the project maintainable, and it is the single most important structural decision here.

```
SOURCE SYSTEMS       Modern ERP  |  Legacy accounting  |  Workshop system
                                 v
raw                  landing. One table per extract, zero transformation.
                                 v
staging              one model per source table. Rename, trim, cast, convert.
                                 v
intermediate         common schema. Union all subsidiaries into one shape.
                                 v
marts                dimensional model. 6 dimensions, 4 facts.
```

---

## `raw` — the landing layer

**Job:** hold exactly what the source sent, with no transformation whatsoever.

**Why it exists.** When a number is disputed — and it will be — you need to separate two questions that get conflated in the argument: *what did the source say*, and *what did the warehouse do with it*. Without a raw layer both are the same query and you cannot answer either cleanly.

**Rules.** One table per extract. No renaming, no casting, no filtering, no deduplication. Reserved identifiers are quoted, never renamed, so the mapping back to source survives.

Loaded by `ingest/load.py`, which targets DuckDB by default and Snowflake optionally. dbt reads *tables*, not files, which is what keeps the models engine-agnostic.

---

## `staging` — one model per source table

**Job:** make one source table safe to use, without changing its shape or grain.

**What happens here:**

- **Rename** to project conventions — `co` and `company_code` both become `company_key`
- **Trim** every text column, because source codes arrive fixed-width padded
- **Cast** explicitly, never relying on inference
- **Convert units** — this is where seconds become hours
- **Convert time zones**, conditionally per subsidiary
- **Guard the epoch** — `1970-01-01` becomes `NULL`
- **Flag** rather than filter: `is_quantity_reliable`, `is_lead_time_plausible`

**What does not happen here:** no joins between fact sources, no aggregation, no business logic, no deduplication. One source table in, one model out, same grain.

**Materialised as views.** They are thin, they are cheap to rebuild, and materialising them as tables would double storage for no benefit.

---

## `intermediate` — the common schema layer

**Job:** turn six incompatible sources into one shape.

**This is the layer that makes the project work.** It deserves the most attention of anything in the repo.

The problem it solves: the subsidiaries' extracts differ in column names, granularity, units, timezone behaviour and data types. One uses a numeric module key, another a text job type. One has a unit column, another does not have one at all.

If the fact models read those sources directly, **every fact has to know about every subsidiary.** Onboarding an acquisition then means editing and re-testing every model in the project — which is how consolidation projects become unmaintainable.

With this layer, everything downstream sees one shape. **Adding a seventh subsidiary is one staging model plus one branch here.** No fact changes.

### Two decisions worth explaining

**`UNION ALL`, not `UNION`.** `UNION` deduplicates across the entire result set. That is expensive at volume and *actively wrong* here — two subsidiaries can legitimately book identical hours for the same employee code on the same day. Deduplication, where genuinely needed, belongs in the fact at a defined grain, not as a side effect of a set operator.

**Honest nulls.** Where a source genuinely lacks a column, the value is `NULL` and the reason is stated in the model. `has_time_of_day` is carried through so downstream consumers know which rows cannot support shift-level analysis. The assumed unit on the legacy source is labelled `'HR (assumed, no unit column in source)'` rather than just `'HR'`, because the difference between *known* and *assumed* should survive into the warehouse.

---

## `marts` — the dimensional model

**Job:** answer business questions efficiently and unambiguously.

Classic star schema. Six dimensions, four facts, materialised as tables because they are read repeatedly.

### Dimensions

| Dimension | Note |
|---|---|
| `dim_company` | Conformed — every fact joins to it. Carries `stores_utc`, the flag driving every timezone decision upstream. Small table, outsized importance. |
| `dim_service_module` | The discriminator that keeps operationally different businesses apart. |
| `dim_item` | Surrogate key, because the natural code is a padded string and expensive to join on at volume. |
| `dim_warehouse` | Keyed on company **and** warehouse code — codes are only unique within a subsidiary, and every company has a warehouse called `MAIN`. |
| `dim_cost_component` | Small, but note that inventory cost is held *per component*, so a whole unit cost is the sum across components. |
| `dim_date` | Generated, range derived from the data. Deliberately excludes the epoch. |

**Every dimension has an unknown member at key `-1`.** A fact row whose dimension lookup fails is still a real transaction and must never disappear because of an inner join downstream. Resolving to a real row rather than a null makes the gap visible and countable instead of turning it into a mystery.

### Facts

| Fact | Grain |
|---|---|
| `fct_inventory_transaction_cost` | company, item, warehouse, cost component, **timestamp**, line seq |
| `fct_labour_hours` | one time-entry booking |
| `fct_work_order_lead_time` | one work order |
| `fct_labour_hours_variance` | company, production order, operation |

**Every fact model states its grain in a header comment before any SQL.** If the grain cannot be written in one sentence, the model is not ready to be written.

### Conventions applied consistently

- **Left joins to dimensions, resolving to the unknown member.** Never lose a fact row to a missing dimension entry.
- **Degenerate dimensions retained** — source codes stay on the fact for traceability.
- **Deleted rows retained**, carrying `is_deleted`. Filtering them would make the fact disagree with source for a reason nobody can find six months later.
- **Aggregate before joining.** The variance model aggregates actuals to the operation grain *before* joining to planned. One level too coarse and a whole order's actual hours attach to every operation on it — inflating actuals while every row count and key test still passes.
- **Flag alongside raw.** `lead_time_days` keeps implausible values so the fact reconciles; `lead_time_days_clean` is pre-filtered so reporting excludes them deliberately.

---

## `ml` — the monitoring layer

The four layers above produce correct numbers and prove it with 70 tests. This layer exists
because of what those tests structurally cannot do.

Every dbt test inspects rows that are **present**. That leaves two blind spots:

| Blind spot | Why tests cannot reach it |
|---|---|
| A value wrong only **in context** | A unit cost of 850 is normal for a pump and absurd for a washer. Both plausible, so no threshold separates them. |
| Rows that **never arrived** | There is nothing to assert against. The fact reconciles perfectly to a source that is itself short. |

Both need a model of what *normal* looks like, per item or per company per day.

### Components

| File | Role |
|---|---|
| `ml/features.py` | Contextual features + leakage controls. Reads the **marts**, not raw. |
| `ml/detect_anomalies.py` | Isolation Forest, benchmarked against the SQL rules it might replace. |
| `ml/volume_monitor.py` | Poisson-residual time-series monitor for outages and spikes. |
| `ml/charts.py` | Every published figure, regenerated from live outputs. |

### Why it reads the marts and not raw

Monitoring should watch the tables the business actually reports from. A defect introduced by
a transformation is then as visible as one that arrived in the source. Watching raw only tells
you the source was fine, which is the less interesting half of the question.

### The isolation of ground truth

The generator knows which rows it broke, and writes them to an `ml` schema that
**`sources.yml` does not declare**. No dbt model can reference it, even accidentally — there
is no `source()` that resolves to it, so the mistake is not available to make.

```
data/labels/defect_labels.parquet  →  ml.defect_labels     (528 labelled rows)
```

The warehouse is built without ever seeing the answers. They exist only to score detection
afterwards, which is the same role a confirmed incident log plays on a real platform.

### Leakage control, enforced rather than documented

Two mart columns encode the answer and are excluded from features:

| Column | Why it leaks |
|---|---|
| `is_quantity_reliable` | *is* the rule output, `quantity > 1e6`, already computed |
| `amount_if_recomputed` | becomes astronomic exactly when quantity is corrupt |

`build_features()` raises `AssertionError` if either appears. A comment would be ignored by
the person who adds a column to improve a score six months from now.

### The headline result

The interesting finding is that ML is the right answer for one defect class and the **wrong**
answer for the other, and the comparison is published both ways.

| Defect class | Best detector | Result |
|---|---|---|
| `CORRUPT_QUANTITY` | **SQL rule** `quantity > 1e6` | precision 1.000, recall 1.000 — the forest manages 0.097 / 0.684 |
| `CONTEXTUAL_PRICE` | **Isolation Forest** | average precision 0.831; top-20 alerts were 20/20 genuine |
| Volume outage / spike | **Poisson residual** | 5 flags from 5,400 company-days, all genuine |

Full treatment including the negative results: [ml-anomaly-detection.md](ml-anomaly-detection.md).

### Where this sits in the flow

```
raw ──► staging ──► intermediate ──► marts ──┬──► reporting
                                             │
                                             └──► ml/  ──► alerts
                                                   ▲
                    ml.defect_labels ──────────────┘
                    (scoring only, unreachable from dbt)
```

The monitoring layer is a **consumer** of the warehouse, not a stage in it. It cannot alter
what the marts contain, which means a failure in detection degrades alerting without
corrupting reporting. That separation is deliberate.

---

## Testing strategy

70 tests, in two deliberately different groups.

**62 schema tests** — the structural floor. Uniqueness and not-null on every key, referential integrity from every foreign key to its dimension, accepted values on every flag and code.

**6 singular tests** — one per class of defect, and these are the ones that matter:

| Test | Catches |
|---|---|
| `assert_inventory_fact_reconciles_to_source` | rows lost or invented between source and fact |
| `assert_seconds_converted_to_hours` | the unit conversion silently not firing |
| `assert_no_implausible_labour_hours` | any booking over 24 hours, from any cause |
| `assert_variance_pct_null_when_unbooked` | the -100% defect returning |
| `assert_no_epoch_date_keys` | an epoch guard missed upstream |
| `assert_no_future_dated_transactions` | timezone conversion applied twice or wrongly |

**Why the split matters.** Structural tests pass straight over the most expensive bugs. A unit-of-measure error produces unique keys, no nulls, valid references and a reconciling row count. Only a plausibility assertion catches it.

**Tests encode known baselines, not aspirations.** The grain uniqueness test expects nine duplicates because nine are genuine source duplicates: `warn_if: ">0"` so it is visible every run, `error_if: ">9"` so it fails on regression. A test that fails constantly gets disabled; a test that says *"no worse than documented"* survives and keeps working.

---

## Portability

The same models build on DuckDB and Snowflake. Engine differences are confined to `macros/helpers.sql` and switch on `target.type`:

```sql
{%- if target.type == 'snowflake' %}
convert_timezone('UTC', '{{ var("reporting_timezone") }}', {{ ts }})
{%- else %}
(({{ ts }} at time zone 'UTC') at time zone '{{ var("reporting_timezone") }}')
{%- endif %}
```

Four things are centralised in macros rather than repeated across models — the timezone rule, the date key, the surrogate key and safe division. Each is easy to get subtly wrong and each appears in many models, so each is defined exactly once.

---

## What is deliberately not here

Named because omitting them is a choice, not an oversight:

- **Snapshots / SCD2.** `dim_item` overwrites standard cost, so historical transactions get today's cost. It should be a slowly changing dimension.
- **Incremental models.** Every fact is a full rebuild. Instant at 200k rows, unacceptable at 200m. The natural keys for incrementality exist and are not used.
- **Orchestration.** Ingest and transform are separate manual commands. Production needs dependencies, retries and alerting.
- **dbt source freshness.** dbt can assert that source data arrived recently, and this project does not use it. Volume monitoring in `ml/` covers the same failure from the other side -- it detects a missing day after the fact rather than refusing to build on stale input. Both would be better than either.
- **Exposures.** Declaring downstream dashboards would make the impact of a model change visible in the lineage graph.
