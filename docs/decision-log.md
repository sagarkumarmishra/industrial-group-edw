# Decision log

What was chosen, what was rejected, and what I got wrong first. Recorded because the
reasoning is the transferable part — anyone can read the final SQL, but the discarded
options are where the engineering lives.

---

## D1 — Fact grain carries the full timestamp, not a date

**Decision.** The grain of `fct_inventory_transaction_cost` is
`(company, item, warehouse, cost_component, txn_timestamp, line_seq)` with the timestamp at
second precision.

**The alternative, and why it is a trap.** Truncating to a date looks natural — reporting is
daily, and 80% of rows have `line_seq = 1`, so a date-based key appears unique when you spot
check it. But `line_seq` does **not** restart per day. Two genuinely distinct transactions
for the same item and warehouse on the same day, both with `line_seq = 1`, collapse onto one
key. You lose rows and the total quietly drops.

**How it is enforced.** A uniqueness test on the full grain. It currently reports 9 warnings
— the known source duplicates from trap 9 — configured as `warn` rather than `error` because
those duplicates are real in the source, not a modelling defect. Set to `error` and you
would be asserting the source is clean, which it is not.

**Cost of the decision.** Daily aggregation has to truncate at query time rather than
relying on the key. That is the right trade: cheap to do downstream, impossible to undo
upstream.

---

## D2 — Timezone conversion happens once, in staging, per source system

**Decision.** `MODERN_ERP` subsidiaries store UTC and are converted to each company's
reporting timezone. Legacy and workshop systems already store local time and are left
alone.

**Why it is not optional.** Convert everything and you shift the legacy companies by one to
two hours, which pushes late-evening transactions into the wrong day and corrupts the date
key. Convert nothing and you do the same to the modern ones. There is no consistent
treatment — the behaviour genuinely differs per source system, so the model must too.

**Why staging.** Any transformation applied in more than one place will eventually be
applied inconsistently. Staging is the only layer where "this happens exactly once" can be
verified by reading a single file.

**This one bit me.** See M1 below.

---

## D3 — Unit conversion is explicit and the original is retained

**Decision.** `fct_labour_hours` carries three columns: `work_hours` (always hours),
`work_hours_as_sourced` (untouched), and `was_unit_converted` (a flag).

**Why keep the raw value.** Two reasons. First, reconciliation against the source system
becomes possible — without it you cannot prove your conversion was correct. Second, the
comparison is the most persuasive artefact in the project: summing the as-sourced column
overstates labour by **192×** for the affected companies, and the chart that shows it needs
both figures.

**Rejected.** Converting in place and discarding the original. Saves a column, destroys
auditability, and makes the trap invisible to anyone reading the mart.

---

## D4 — Epoch dates become NULL in staging, never in marts

**Decision.** `1970-01-01` means "no date" in all four source systems. It is converted to
`NULL` in staging.

**Why not later.** If an epoch value reaches a fact table it becomes a date key pointing at
1 January 1970, which then joins successfully to `dim_date` and produces a plausible-looking
row 53 years in the past. A `NULL` fails loudly at the first join and cannot be mistaken for
data.

**Why not a sentinel key.** Considered pointing epoch rows at an "unknown date" member.
Rejected for the inventory fact because a missing transaction timestamp is a genuine data
quality problem worth surfacing, not a category to be tidied away. Work orders are different
— an open order legitimately has no completion date, so those are flagged rather than
treated as defects.

---

## D5 — Unknown dimension members use key `-1`, not NULL foreign keys

**Decision.** Every dimension carries an `Unknown` member at key `-1`. Facts referencing a
missing dimension row point there.

**Why.** Inner joins silently drop rows with NULL keys, and a dropped row is a wrong total
with no error message. With `-1` the row survives, the total stays correct, and the problem
is visible as a count against `Unknown` that anyone can query.

**Cost.** Every dimension needs a union with a hardcoded member, which is mildly ugly. Worth
it.

---

## D6 — Aggregate before joining, in the variance model

**Decision.** `fct_labour_hours_variance` aggregates labour bookings to the operation grain
*before* joining to planned operations.

**Why.** Planned operations are one row per routing step; bookings are many rows per step.
Join first and the planned hours are duplicated once per booking, inflating planned totals
by the booking count — a classic fan-out. The bug is invisible on data where most operations
have exactly one booking, which is most spot checks.

---

## D7 — Planned-but-never-booked operations return NULL variance, not −100%

**Decision.** Where an operation has planned hours and no bookings, variance percentage is
`NULL` and `has_actual_booking = 0`.

**Why.** A naive calculation reports exactly −100%, which reads as "we used no hours at all"
rather than "this has not been worked yet". Those are different statements, and averaging
the first drags every roll-up down. `NULL` propagates correctly through `AVG()`; a fake
−100% does not.

---

## D8 — Ten deliberate data traps rather than clean synthetic data

**Decision.** The generator injects ten specific silent defects.

**Why.** Clean synthetic data demonstrates nothing — anyone can model tidy inputs. Each
trap is one I have hit on a real multi-company ERP consolidation, and each is silent: the
load succeeds and the numbers look plausible. The project's value is in handling them, so
they have to be present.

**Constraint accepted.** This makes the repository harder to read at first glance. Mitigated
by documenting every trap in `data-traps.md` with the symptom, the cause, and the fix.

---

## D9 — A tenth trap was added specifically so the ML layer would be justified

**Decision.** Trap 10, contextual price anomalies, was invented *after* the warehouse was
finished, because the nine original traps are all deterministically detectable.

**Why this matters.** An Isolation Forest finding the corrupt quantities would be theatre —
`WHERE quantity > 1000000` catches all 57 perfectly. Adding a model to a solved problem is
how ML budgets get wasted, and demonstrating it would show the opposite of judgement.

Trap 10 injects prices 2–9× an item's own median while keeping every absolute value inside
a plausible range and the row internally consistent (`quantity × unit_cost` really does
equal the amount). No single-row rule can express it.

**Verified, not assumed.** After adding trap 10, all 70 existing dbt tests still pass. The
162 anomalies are genuinely invisible to the test suite — which is the proof that the blind
spot is real and not manufactured.

---

## D10 — Per-item price volatility, so detection is not trivial

**Decision.** Each item gets its own price volatility between 3% and 35%.

**Why.** The first version used a tight ±8% band for every item. The injected anomalies
separated perfectly — clean rows topped out at 1.10× the item median while anomalies started
at 5×. Any detector would have scored 100%, which proves nothing except that the test was
rigged.

With per-item volatility, clean rows now reach **2.34×** and anomalies start at **2.0×**.
The overlap is deliberate: detection becomes a real precision-recall tradeoff rather than a
threshold anyone could eyeball.

**Rejected.** Leaving the clean separation and reporting the 100%. It would have looked
better and meant nothing.

---

## D11 — Ground truth lives in a schema dbt cannot reach

**Decision.** Defect labels are written to `data/labels/` and loaded into an `ml` schema
that `sources.yml` does not declare.

**Why.** If the labels were reachable, a model could reference them and the whole evaluation
would be circular. Physical separation is stronger than a naming convention — there is no
`source()` that resolves to them, so the mistake is not available to make.

---

## D12 — Two leaky columns excluded, with an assertion rather than a comment

**Decision.** `is_quantity_reliable` and `amount_if_recomputed` are excluded from features,
and `build_features()` raises `AssertionError` if either appears.

**Why an assertion.** A comment saying "do not use these" is a comment. Six months later
somebody adds a column to improve a score, the score improves dramatically, and nobody
questions it. The assertion makes the mistake fail loudly.

---

## D13 — Isolation Forest, not a supervised classifier

**Decision.** Unsupervised detection.

**Why.** A supervised classifier would score better on this data and be useless in
production, because it needs the labels you are trying to produce. The labels here exist
only to score the result afterwards; the model never sees them. That is what makes the
approach transferable rather than a demonstration that works only because the answers
happened to be available.

**Also rejected.** Local Outlier Factor — comparable quality, far slower at 200k rows.
Autoencoder — defensible at much larger scale, heavier dependency and slower loop for
results within noise of the forest at 200k rows and 11 features.

---

## D14 — Poisson residual for volume, not a robust z-score

**Decision.** Volume anomalies are scored with `(actual − expected) / sqrt(expected)`, with
the expected value from a rolling median.

**Why.** Daily row counts are count data. Variance grows with the mean, so the spread is
already determined by the expected value. A MAD-based scale underestimates it: on ~36 rows a
day the MAD is about 4, so an ordinary quiet Tuesday of 23 rows scores −4.7 and pages
somebody.

**Evidence.** At threshold 4.0 the z-score flags 14 days against the Poisson residual's 6,
and the extras are quiet Tuesdays. At 6.0 the z-score has already lost three of the five
genuine incidents. The two agree only at exactly 5.0. The Poisson residual gives a usable
operating *range* rather than one lucky threshold.

---

## D15 — The alert budget is the published operating point

**Decision.** The headline detection figure is top-20-by-score, not best-F1.

**Why.** Best F1 is found by sweeping thresholds against the labels. If you have the labels
you do not need the detector, so that number is an upper bound and nothing more. It is
reported and labelled as such at every appearance.

The alert budget needs no labels: a human can triage roughly 20 rows a day, so take the top
20 and accept the resulting recall. **All 20 were genuine — precision 1.000.** Recall of
0.123 looks poor until you compare it to an alert stream nobody reads because two-thirds of
it is noise.

---

## D16 — DuckDB as the default target, Snowflake as an option

**Decision.** `dbt build` runs against a local DuckDB file by default. A Snowflake target
exists and reads credentials from environment variables only.

**Why.** Anyone can clone this and have it running in under a minute with no account, no
credentials and no cost. That matters more for a portfolio project than using the
fashionable warehouse. The SQL is deliberately kept portable — no DuckDB-only syntax — and
the Snowflake target proves the models are not tied to one engine.

**No secrets in the repository.** The Snowflake profile reads `SNOWFLAKE_ACCOUNT`, `_USER`,
`_PASSWORD`, `_ROLE`, `_WAREHOUSE` and `_DATABASE` from the environment. There is nothing to
leak.

---

## D17 — Charts are generated, never pasted

**Decision.** Every figure in the documentation is produced by `ml/charts.py` from the
parquet and JSON the pipeline just wrote.

**Why.** A screenshot in a README is unverifiable and ages badly — it will eventually
contradict the text around it and nobody will notice. A regenerated chart cannot silently
disagree with the numbers, because both come from the same run.

---

# Mistakes

The ones worth recording, because each taught something that changed the design.

## M1 — The label join lost 46% of labels to the project's own timezone trap

**Symptom.** Only 93 of 173 contextual price anomalies and 32 of 57 corrupt quantities
joined to the fact table. The model appeared substantially worse than it was.

**Cause.** The label file keyed on `txn_timestamp`, the obvious choice. Staging converts
timestamps to the reporting timezone for the three subsidiaries storing UTC — **trap 1**,
the first trap this project documents. For half the group the raw timestamp no longer
matched the fact table.

**Fix.** Key on business columns plus `quantity` and `unit_cost`, which pass through staging
unchanged apart from a cast.

**What it taught.** The traps are not hypothetical. This one caught the person who invented
it, while building the tooling designed to measure it. The reasoning is now a comment in
`ingest/generate.py` rather than a silent fix, because that is better evidence than any
claim I could make about the trap being realistic.

## M2 — The first detection run was rigged and I nearly shipped it

**Symptom.** Perfect separation between clean and anomalous rows. Every detector scored
approximately 100%.

**Cause.** A uniform ±8% price band meant clean rows never exceeded 1.10× the item median
while anomalies started at 5×. There was no overlap, so there was no problem to solve.

**Fix.** Per-item volatility of 3–35%, anomaly multipliers widened to 2–9×. Clean rows now
reach 2.34× and the classes genuinely overlap.

**What it taught.** A suspiciously good result is a bug report. The instinct to publish the
100% and move on is exactly the instinct that produces worthless ML.

## M3 — The volume monitor's first version flagged twelve days, nine of them Tuesdays

**Symptom.** 12 anomalous company-days, of which 3 were the injected outage and the rest
were ordinary quiet days.

**Cause.** A robust z-score applied to count data. MAD underestimates Poisson spread, so
routine variation read as significant.

**Fix.** Pearson residual against a median baseline, plus dropping the first and last
calendar day as partially-observed boundary artefacts.

**What it taught.** Choosing the right statistic for the data type is not pedantry. The
wrong one here produces alert fatigue, and alert fatigue means the monitor is muted inside a
week — at which point recall is zero regardless of what the backtest said.

## M4 — Five dimension models written with double-quoted string literals

**Symptom.** `Unknown` treated as an identifier rather than a string; the models would not
compile.

**Cause.** Double quotes delimit identifiers in SQL, not strings. Habit from other
languages.

**What it taught.** Nothing profound, but worth recording as the reason the codebase is
consistent about single quotes throughout.
