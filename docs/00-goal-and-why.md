# Why this project exists

## The short version

Six factories. Four different computer systems. One question nobody could answer:
**"what did we actually spend on stock last month?"**

This project is the machinery that answers it, plus the monitoring that tells you when the
answer has quietly gone wrong.

---

## The problem, in plain language

Imagine a manufacturing group that has bought up six smaller companies over twenty years.
Each one came with its own computer system, and nobody ever consolidated them:

| Subsidiary | System it runs | How it records time | Where it stores dates |
|---|---|---|---|
| Nordvik Hydraulics | Modern ERP | hours | UTC |
| Baltic Pump Works | Modern ERP | hours | UTC |
| Vantaa Precision Tooling | Modern ERP | hours | UTC |
| Aalborg Fluid Systems | Legacy accounting | hours | local time |
| Gotland Valve Company | Legacy accounting | hours | local time |
| Rauma Service Partners | Workshop system | **seconds** | local time |

Every month, the finance team asks for one number: total inventory cost across the group.
Getting it means combining six extracts that disagree about almost everything — how a date
is stored, what unit a duration is in, whether a missing value is blank or `1970-01-01`,
even whether the same warehouse code means the same warehouse.

Do that by hand in a spreadsheet and you get a number. It just isn't the right number, and
nothing tells you that.

---

## Why "it looks fine" is the actual problem

This is the part that makes data work harder than it sounds.

If a data pipeline breaks loudly — a crash, an error, a job that won't start — that is a
good day. Somebody notices within minutes and fixes it.

The expensive failures are silent. The job succeeds, the report renders, the totals look
like totals. Consider a single real example from this project:

> One subsidiary books labour in **seconds**. The unit is recorded in a separate column
> that is easy to overlook. Add the hours column up without checking it and total labour
> across the group comes out **192 times too high** for the affected companies.

Nothing errors. No test fails. The number is simply wrong, and it stays wrong until
somebody senior looks at a report and says "that can't be right" — typically a quarter
later, after decisions have been made on it.

![Trap 2](assets/trap_unit_conversion.png)

Nine more traps like this one are documented in [data-traps.md](data-traps.md). Each is
drawn from the same category of problem: **the data is wrong in a way that looks right.**

---

## What I set out to build

Three things, in order of how much they matter.

### 1. A warehouse that produces the right number

A dimensional model — six dimensions and four fact tables — that consolidates all six
subsidiaries onto one grain, with every unit, timezone and null convention explicitly
reconciled. Not "cleaned up" in the vague sense: each decision written down, with the
reason, in the model that implements it.

### 2. Tests that fail when the number is wrong

70 automated tests covering uniqueness, referential integrity, accepted values and
business plausibility. These run on every build. If someone changes a model and breaks the
grain, the build fails before the number reaches a report.

### 3. Monitoring for what tests cannot catch

This is the part most projects skip, and the reason the machine-learning layer exists.

Tests inspect rows that are **present**. They are structurally incapable of catching two
very common failures:

- **A price that is wrong only in context.** A unit cost of 850 is normal for a pump and
  absurd for a washer. Both are plausible numbers, so no threshold can separate them. You
  have to compare each row against that item's own history.
- **Rows that never arrived.** If a subsidiary's extract fails for three days, there is
  nothing to test. Every uniqueness check passes, every foreign key resolves, and the
  warehouse reconciles perfectly to a source that is itself short.

Both need a model of what *normal* looks like. That is what `ml/` builds.

---

## Why machine learning, and why only here

It would have been easy to bolt on a model, report a good-looking accuracy figure, and
call the project modern. I deliberately did the opposite: the ML layer is scoped to the
two problems rules genuinely cannot solve, and **the comparison against rules is
published even where the rules win.**

![Rules versus model](assets/ml_rule_vs_model.png)

The left panel is a defect where a one-line `WHERE` clause scores perfect precision and
perfect recall. The Isolation Forest is much worse. The honest engineering conclusion is
to keep the rule and not deploy a model.

The right panel is a defect where no threshold works at all, the crude rule misses a third
of the anomalies outright, and the model ranks them with average precision **0.831**.

That contrast is the actual finding. **Rules for absolute violations, models for
contextual ones.** Knowing which is which — and being willing to publish the case where
ML loses — is more useful than any single score.

Full write-up: [ml-anomaly-detection.md](ml-anomaly-detection.md).

---

## How it was built, step by step

### Step 1 — Invent data that fights back

Clean synthetic data proves nothing. Anyone can model tidy inputs. So the generator
(`ingest/generate.py`) deliberately injects ten specific defects, every one of which I have
hit on a real multi-company ERP consolidation, and every one silent by design: the load
succeeds and the numbers look plausible.

The generator also records **which rows it broke**, into a separate schema no dbt model can
reach. That gives honest ground truth for scoring detection later — the same role a
confirmed incident log plays on a real platform.

### Step 2 — Land the data without interpreting it

Raw tables are loaded exactly as they arrive: padded text stays padded, epoch dates stay
epoch, the table named `ENTITY` keeps its reserved-word name. Interpreting data during
loading destroys your ability to prove what the source actually said.

### Step 3 — Fix the defects once, in staging, and say why

Every trap is resolved in exactly one place, with a comment explaining the decision and
what breaks if you choose otherwise. Timezone conversion, unit conversion, epoch-to-null,
trimming — one model each, never repeated downstream.

### Step 4 — Model dimensionally, and get the grain right

The grain of the inventory fact is the single most consequential decision in the project.
Transaction timestamps are unique to the second, and the line sequence does not restart
daily — so truncating to a date collapses genuinely distinct transactions onto one key and
silently loses rows. The fact carries the full timestamp. There is a test that fails if
anyone changes that.

### Step 5 — Test the things that would be embarrassing to get wrong

Not just structural tests. Plausibility tests too: labour hours per booking within a sane
range, no negative lead times slipping through unflagged, recomputed amounts reconciling to
the source's own figure.

### Step 6 — Build monitoring for the blind spots

Features that describe each row *relative to its own item's history*, an Isolation Forest
over those features, and a separate time-series monitor for volume. Scored against the
ground truth from step 1.

### Step 7 — Write down the decisions and the mistakes

[decision-log.md](decision-log.md) records what was chosen, what was rejected, and what I
got wrong on the first attempt — including a bug where my own label join lost 46% of the
labels because it fell into trap 1, the timezone trap the project is about.

---

## What this demonstrates

| Claim | Where to verify it |
|---|---|
| I can model awkward multi-source data dimensionally | `models/marts/`, [architecture.md](architecture.md) |
| I know which data problems are dangerous and why | [data-traps.md](data-traps.md) |
| I test for correctness, not just for green ticks | `models/**/*.yml`, `tests/` |
| I can build ML that earns its place | [ml-anomaly-detection.md](ml-anomaly-detection.md) |
| I report results honestly, including negative ones | `ml/detect_anomalies.py` output |
| I document decisions so others can maintain the work | [decision-log.md](decision-log.md) |

---

## What this is not

Being straight about the limits, because overclaiming is worse than a modest scope:

- **The data is synthetic.** It is deliberately adversarial and modelled on real patterns,
  but no real company's data is in this repository.
- **Nothing here has run in production.** No live traffic, no business rollout, no
  on-call rotation. The engineering is real; the deployment is not claimed.
- **The ML numbers come from one seeded run.** They are reproducible — `make all` will
  give you the same figures — but they are not a cross-validated study.
- **The oracle-tuned F1 in the output is an upper bound, not a result.** It uses the labels
  to pick its threshold, which you cannot do in production. It is labelled as such
  everywhere it appears.

---

## Where to go next

| If you want to | Read |
|---|---|
| Understand the layers and why they exist | [architecture.md](architecture.md) |
| See the ten data traps and how each is fixed | [data-traps.md](data-traps.md) |
| Dig into the detection work | [ml-anomaly-detection.md](ml-anomaly-detection.md) |
| See what was decided and rejected | [decision-log.md](decision-log.md) |
| Look up a column | [data-dictionary.md](data-dictionary.md) |
| Run it yourself | [runbook.md](runbook.md) |
