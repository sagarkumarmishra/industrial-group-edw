# The ten data traps

Every trap in this project is one I have hit on production multi-company ERP consolidation work. They share one characteristic, and it is the reason they matter:

> **Nothing fails.** The load succeeds, row counts reconcile, no key is null, and the numbers look plausible.

Traps that crash a pipeline are cheap — you find them in minutes. These are the expensive ones. They get reported for weeks before someone questions the figure, and by then the damage is to trust in the warehouse rather than to the data.

---

## 1. Mixed time zones

**The trap.** The modern ERP subsidiaries store timestamps in UTC. The legacy accounting and workshop systems store local time. Both arrive in the same column of the same union view.

**Why it is silent.** Applying the conversion globally shifts the three legacy subsidiaries by an hour or two. Applying it nowhere shifts the three modern ones. Either way, dates still look like dates, and a transaction dated 23:30 on the 31st quietly moves into the next month — which is exactly the kind of error that shows up as an unexplained month-end variance.

**Handled by** a conditional conversion driven by a flag on the company dimension, defined once in `macros/helpers.sql`:

```sql
case when stores_utc = 1
     then convert_timezone('UTC', 'Europe/Oslo', ts)
     else ts
end
```

**The trap inside the trap.** A reconciliation that does *not* apply the same rule as the load will report enormous false mismatches. On a real project my first accuracy comparison omitted the conversion and reported roughly **480,000 false mismatches per company**. The data was fine; my test was wrong. That cost half a day and it is why `docs/` says this out loud.

---

## 2. Mixed units in one column

**The trap.** `work_hours` is booked in **seconds** on a minority of manufacturing rows and in hours everywhere else. The unit lives in a separate `hours_unit` column.

**Why it is silent — and why this is the most dangerous trap here.** Nothing about the *values* reveals it. A `SUM()` over the raw column returns a number, and that number looks like a plausible total of hours. Row counts reconcile. Keys are unique. Nothing is null. And affected manufacturing hours are **3,600 times** too large.

Measured on this project's data:

```
rows the source booked in SECONDS           5,245
SUM if the unit is ignored             106,580,988   'hours'
SUM after reading the unit column          29,605.8  hours
overstatement avoided                       3,600x
```

**Handled by** reading the unit before using the measure, in `stg_time_entries_modern.sql`:

```sql
case when upper(hours_unit) = 'SEC' then work_hours / 3600.0
     else work_hours
end
```

**The generalisable rule.** Whenever a source carries a unit-of-measure column, the measure column cannot be trusted on its own. Always read the unit.

**Also tested, not just commented.** `tests/assert_seconds_converted_to_hours.sql` asserts that every row the source booked in `SEC` now holds a smaller value and lands in a plausible range. A comment claiming a conversion happened is worth nothing; an assertion is worth something.

---

## 3. Sub-day grain

**The trap.** The transaction timestamp is unique to the *second*, and the line sequence number does **not** restart per day — it is overwhelmingly `1`.

**Why it is silent.** The obvious design is to key the fact on the transaction *date*. Nobody reports inventory at second granularity, a date key is what joins to the date dimension, and carrying a timestamp in a fact key feels wrong. So you truncate.

But two genuine receipts of the same item, into the same warehouse, against the same cost component, on the same day are then **indistinguishable**. They collapse onto one key.

Measured here:

```
source rows                               200,009
distinct keys, DATE only                  199,904   <- the naive grain
distinct keys, WITH timestamp             200,000   <- the correct grain
transactions destroyed by truncating          105
```

On a real project this produced **325,318 duplicate rows** in a 3.6 million row fact. It was found by a QA analyst weeks later, after the fact had already been reported from.

**Handled by** carrying the full timestamp as a grain column, while keeping the truncated date key as the date dimension foreign key so nothing downstream changes.

**How to catch it in one minute, before writing the fact.** Count distinct on the candidate key in the *source*, with and without the timestamp, and compare both to the row count. That is `analyses/prove_the_grain.sql`. If the version with the timestamp matches the row count and the version without it does not, the timestamp belongs in the key. This is the single highest-value query in the repository.

---

## 4. Corrupt quantities

**The trap.** A tiny number of source rows carry absurd quantities — hundreds of millions. The source's own `total_amount` column is correct.

**Why it is silent.** `quantity × unit_cost` is a reasonable-looking formula that compiles and returns a number. 52 bad rows out of 200,009 is enough to make the grand total meaningless:

```
rows with a corrupt quantity                   52   (out of 200,009)
total cost, taken FROM SOURCE          236,733,952.20
total cost, recomputed qty x rate    3,938,331,944,074.65
inflation from recomputing                 16,636x
```

On a real project this exact mistake reported **$28.87 billion** of estimated labour cost against **$3.06 million** of actual cost. The fix was not to filter outliers — that hides the problem — but to read the finished amount column the ERP already held. The same rows then produced **$29.68 million**.

**Handled by** taking cost from `total_amount` as recorded by the source, never recomputing. `amount_if_recomputed` is exposed on the fact so a reviewer can see what recomputation *would* have produced and why it is not used, and `is_quantity_reliable` flags the bad rows rather than silently dropping them.

**The generalisable rule.** If the source already holds a computed amount, prefer it. Recomputing from components inherits every defect in every component.

---

## 5. Epoch as null

**The trap.** ERPs routinely write `1970-01-01` to mean "no date" rather than writing `NULL`. Here, open work orders carry the epoch in their completion timestamp.

**Why it is silent.** `19700101` is a perfectly valid-looking date key. It joins to the date dimension. It sorts. It just quietly attaches fifty years of nothing to the beginning of every trend chart, and makes any lead-time calculation involving it absurd.

**Handled by** a guard in `macros/helpers.sql` that nulls any timestamp on or before the configured `epoch_cutoff`, and a `dim_date` that deliberately excludes the epoch so a stray `19700101` fails referential integrity rather than resolving quietly. `tests/assert_no_epoch_date_keys.sql` checks all four facts.

---

## 6. Fixed-width padded text

**The trap.** Source systems export codes padded to a fixed width: `MAIN` arrives as `MAIN␣␣␣␣␣␣␣␣`.

**Why it is silent.** If padding is inconsistent between extracts — and it is — the same business value becomes two distinct values. A `GROUP BY` produces two rows where there should be one, a join misses, and a dimension lookup falls through to the unknown member. Nothing errors.

**Handled by** a `clean()` macro applied to every text column in every staging model. Trim first, then join or group. Never the other way round.

---

## 7. Reserved words as identifiers

**The trap.** One source table is named `ENTITY`, which is a reserved word in several SQL engines. Snowflake's `write_pandas` does not quote identifiers, so the load fails on it.

**Why it matters more than it looks.** The tempting fix is to rename the table on load. That works immediately and costs the team permanently: the mapping back to source is now broken, and every future attempt to trace a column becomes guesswork.

**Handled by** quoting the identifier and keeping the source name. `ingest/load.py` maintains a set of reserved identifiers and falls back to an explicit quoted `CREATE` before loading those tables. On a real project the same class of problem affected 6 of roughly 410 tables, and the offender was also called `ENTITY`.

---

## 8. Column drift across sources

**The trap.** The legacy extract is not a variant of the modern one — it is a different shape entirely:

| | Modern ERP | Legacy |
|---|---|---|
| Company column | `company_code` | `co` |
| Employee | `employee_code` | `emp_id` |
| Time | full timestamp | **date only** |
| Hours | `work_hours` | `hours_worked` |
| Unit column | `hours_unit` | **absent** |
| Work centre | present | **absent** |
| Delete flag | `is_deleted` | **absent** |
| Module | numeric key | text `job_type` |

**Why it is silent.** It is not, exactly — it fails loudly if you assume the shapes match. The silent failure is subtler: *defaulting* the missing columns to convenient values. Setting `is_deleted = 0` because the column does not exist is an assumption, not a fact, and once it is buried in a `coalesce` nobody knows it was made.

**Handled by** the common schema layer, which reconciles the shapes explicitly. Where information genuinely does not exist it is `NULL` and the reason is stated in the model. `has_time_of_day` is exposed on the fact so nobody attempts shift-level analysis on rows that cannot support it, and the assumed unit is labelled `'HR (assumed, no unit column in source)'` rather than just `'HR'`.

**Related trap not simulated here.** On real ERP data, column *counts* also vary between company instances of the same table — I have seen 213 columns where other companies had 194. The rule that follows is absolute: **never `SELECT *` across company tables.** Use explicit projection or the union silently misaligns.

---

## 9. Genuine source duplicates

**The trap.** One subsidiary double-extracted nine rows. These are **real** duplicates in the source, not a modelling error.

**Why the distinction is everything.** A duplicate caused by a wrong grain and a duplicate that exists in the source require opposite fixes:

- **Wrong grain** → correct the key. Deduplicating would destroy real transactions.
- **Source duplicate** → fix it upstream, or deduplicate deliberately and document it. Correcting the grain will not help.

Getting this backwards means either silently deleting real data or shipping a fact you know is wrong. Telling them apart is what `analyses/prove_the_grain.sql` is for: after correcting the grain, any *residual* duplicate is a genuine source duplicate.

**Handled by** a test that encodes the known baseline rather than asserting zero:

```yaml
config:
  severity: warn
  warn_if: ">0"     # surface it every run, so nobody forgets it exists
  error_if: ">9"    # fail the build the moment it gets worse
```

**Why not assert zero.** A test that fails on every run gets disabled by the third person who hits it. A test that says *"no worse than the nine we have documented"* stays meaningful and is a real regression guard. This is the difference between a test that passes and a test that is worth having.

---

## 10. Contextual price anomalies

**The trap.** A small number of inventory lines carry a unit cost 2 to 9 times that item's
own normal price. Every absolute value stays inside a plausible range for the catalogue, and
the row is internally consistent — `quantity × unit_cost` really does equal
`total_amount`.

```
item A-1042 (a washer)     unit_cost = 41.80     item's usual price:  4.60   <- 9x wrong
item B-7781 (a pump)       unit_cost = 41.80     item's usual price: 44.20   <- fine
```

Identical value. One is a nine-fold error, the other is a Tuesday.

**Why nothing catches it.** This is the trap that defeats every technique used for the other
nine:

| Technique | Why it fails |
|---|---|
| Range check on `unit_cost` | The catalogue spans parts costing 2 and parts costing 900. Any global threshold misses cheap-item anomalies or drowns in expensive-item false positives. |
| Internal consistency check | Passes. The arithmetic is correct; only the input is wrong. |
| Not-null, uniqueness, referential integrity | All pass. The row is structurally perfect. |
| Comparison to `total_amount` | Passes, because the amount was computed from the bad cost. |
| Per-item thresholds in SQL | Would work, and means maintaining 900 thresholds by hand. Nobody does this, and if they did it would be stale within a quarter. |

**Verified, not assumed.** After injecting 162 of these, **all 70 existing dbt tests still
pass.** The defects are genuinely invisible to the suite. That is the evidence that the blind
spot is real rather than manufactured for the sake of having something for a model to do.

**The fix.** This is the one trap in the project that rules cannot express, and the reason
the `ml/` layer exists. Detection compares each row against *that item's own* price history
using a robust z-score and a ratio to the item median, then scores it with an Isolation
Forest.

Measured against the obvious SQL attempt:

| Detector | Precision | Recall | F1 |
|---|---:|---:|---:|
| SQL rule, `unit_cost` above the global 99.9th percentile | 0.537 | 0.667 | 0.595 |
| Isolation Forest — *ranking quality* (average precision) | | | **0.831** |
| Isolation Forest, top-20 alert budget | **1.000** | 0.123 | 0.220 |

The crude rule misses a third of the anomalies outright. The model ranks them well, and at
a realistic alert budget every one of its top 20 flags was genuine.

Full treatment, including the case where a model is the *wrong* answer:
[ml-anomaly-detection.md](ml-anomaly-detection.md).

**Why this trap was added last.** The nine traps above are all deterministically detectable,
which means an Isolation Forest applied to them would be theatre — `WHERE quantity > 1000000`
catches every corrupt quantity perfectly, and adding a model to a solved problem is how ML
budgets get wasted. Trap 10 exists so that the ML layer has a problem it is genuinely the
right tool for, and so that the comparison against rules is honest in both directions.

---

## 11. The two failures that are not traps at all

Worth separating out, because they are a different category: nothing about the data is
wrong. Rows are simply missing, or there are too many of them.

**Extract outage.** One subsidiary's nightly job fails for three days. No rows arrive. Every
uniqueness check passes, every foreign key resolves, and the fact table reconciles
*perfectly* to the source — because the source is short too.

No row-level test can detect this. **You cannot test rows that are not there.** A `GROUP BY`
cannot even represent the problem: a day with zero rows produces no group, so the absence is
invisible to aggregation. Catching it requires reindexing onto a complete date × company
spine and filling the gaps with explicit zeros.

**Duplicate extract run.** Another subsidiary's job runs twice, producing six times normal
volume for one day. The rows are legitimate, so this is not a defect — but it usually means
something ran twice, and that is worth knowing before it becomes double-counted cost.

Both are handled by `ml/volume_monitor.py`, which found exactly five anomalous company-days
out of 5,400 with no false positives.

![Volume monitor](assets/ml_volume_monitor.png)

This is the most common incident class in production data platforms and the one most projects
have no answer to at all.

---

## The pattern across all ten


1. **Read the source's own metadata.** Unit columns, delete flags, timezone conventions. The measure column is not the whole story.
2. **Prefer what the source computed over what you can recompute.** Recomputation inherits every upstream defect.
3. **Prove the grain before writing the fact.** One query, one minute, prevents the most expensive bug class there is.
4. **Flag, do not filter.** Filtering hides a real problem from the business and breaks reconciliation for reasons nobody can find later.
5. **`NULL` is a legitimate answer.** "Not calculable" is honest. A fabricated number is a lie that looks like a fact.
6. **Test the plausibility, not just the structure.** Uniqueness, not-null and referential integrity all pass straight over a unit-of-measure bug.
7. **Encode the known baseline in the test.** Zero-tolerance assertions on known exceptions get switched off.
