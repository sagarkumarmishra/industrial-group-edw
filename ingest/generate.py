"""
Synthetic source-system generator for the Industrial Group EDW demo.

Generates data for a fictional industrial group, NORDVIK INDUSTRIAL GROUP, made up of
six subsidiaries acquired over time and running four different source systems.

The point of this module is that the data is deliberately AWKWARD. Clean synthetic data
proves nothing. Every quirk below is one I have hit on a real multi-company ERP
consolidation, and each one is silent -- the load succeeds and the numbers look plausible.

Traps injected (see docs/data-traps.md for the full write-up):

  1. MIXED TIME ZONES      the modern ERP stores UTC; the legacy systems store local time.
                           Converting both, or neither, corrupts every date key.
  2. MIXED UNITS           work_hours is sometimes booked in SECONDS. The unit lives in a
                           separate column. Miss it and hours inflate 3,600x.
  3. SUB-DAY GRAIN         txn_timestamp is unique to the second and line_seq does NOT
                           restart per day. Truncate to a date and distinct transactions
                           collapse onto one key.
  4. CORRUPT QUANTITIES    a handful of rows carry absurd quantities. Cost computed as
                           qty * rate explodes; the ERP's own amount column is correct.
  5. EPOCH AS NULL         1970-01-01 means "no date", not 1 January 1970.
  6. PADDED TEXT           codes arrive fixed-width padded. Untrimmed, one value becomes two.
  7. RESERVED WORDS        one source table is called ENTITY, which is reserved in several
                           engines. Unquoted DDL fails on it.
  8. COLUMN DRIFT          not every subsidiary's extract has the same columns.
  9. SOURCE DUPLICATES     one subsidiary genuinely double-extracted a few rows.
 10. CONTEXTUAL PRICES     a few rows carry a unit_cost 5-10x that ITEM's normal price,
                           while every value stays inside a plausible absolute range.
                           No fixed threshold can catch these -- see the note below.

WHY TRAP 10 EXISTS, AND WHY IT IS DIFFERENT FROM TRAP 4

Trap 4 is an ABSOLUTE violation. A quantity of 256,000,000 is wrong on sight, and a
one-line rule (quantity > 1e6) catches every instance with perfect precision. No model
is needed and none would be justified.

Trap 10 is a CONTEXTUAL violation. A unit cost of 850 is entirely normal for a pump and
absurd for a washer. Both values sit in the same plausible range, so there is no global
number that separates them -- the only way to judge the row is against that item's own
price history. Rules cannot express that without one threshold per item, which nobody
maintains. A model holding per-item context separates them easily.

That contrast is the whole argument for the ML layer in ml/: rules for absolute
violations, models for contextual ones. See docs/ml-anomaly-detection.md.

GROUND TRUTH

Because this generator decides which rows to break, it also knows the answer. Every
defective row carries a defect_type, which is stripped out of the source extracts and
written separately to data/labels/defect_labels.parquet. That file loads into an 'ml'
schema and NO dbt model reads it -- the warehouse never sees the answers. It exists only
so detection can be scored, which is the same role a confirmed incident log plays on a
real platform.

VOLUME ANOMALIES

Two further events are injected for the freshness monitor in ml/volume_monitor.py: a
three-day extract OUTAGE for one subsidiary, and a one-day volume SPIKE for another.
Neither is a data defect. Both are the kind of operational incident that is far more
common in production than a corrupt value, and that no row-level test can detect.

Usage:
    python -m ingest.generate            # writes parquet into data/raw/ and data/labels/
    python -m ingest.generate --rows 50000
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import random

import numpy as np
import pandas as pd

RAW_DIR = os.path.join("data", "raw")
LABEL_DIR = os.path.join("data", "labels")
SEED = 20260917

# Defect type labels. Used only for scoring detection, never exposed to the warehouse.
CLEAN = "CLEAN"
D_CORRUPT_QTY = "CORRUPT_QUANTITY"        # trap 4  -- absolute, rules win
D_PRICE_ANOMALY = "CONTEXTUAL_PRICE"      # trap 10 -- contextual, model wins
D_EPOCH = "EPOCH_DATE"                    # trap 5
D_DUPLICATE = "SOURCE_DUPLICATE"          # trap 9

# Volume incidents for the freshness monitor. Not data defects.
OUTAGE_COMPANY = 400                      # Aalborg stops extracting for three days
OUTAGE_START_DAY = 500                     # days after 2023-01-01
OUTAGE_DAYS = 3
SPIKE_COMPANY = 500                       # Gotland double-runs an extract one day
SPIKE_DAY = 620
SPIKE_MULTIPLIER = 6

# --------------------------------------------------------------------------- group
# Six subsidiaries. is_utc drives the timezone decision and is the single most
# consequential flag in the whole model.
COMPANIES = [
    # code, name, short, source_system,   is_utc, tz
    (100, "Nordvik Hydraulics",       "NHY", "MODERN_ERP",  1, "Europe/Oslo"),
    (200, "Baltic Pump Works",        "BPW", "MODERN_ERP",  1, "Europe/Oslo"),
    (300, "Vantaa Precision Tooling", "VPT", "MODERN_ERP",  1, "Europe/Helsinki"),
    (400, "Aalborg Fluid Systems",    "AFS", "LEGACY_ACCT", 0, "Europe/Copenhagen"),
    (500, "Gotland Valve Company",    "GVC", "LEGACY_ACCT", 0, "Europe/Stockholm"),
    (600, "Rauma Service Partners",   "RSP", "WORKSHOP_SYS", 0, "Europe/Helsinki"),
]

# Manufacturing only happens at these two.
MANUFACTURING = [300, 200]

SERVICE_MODULES = [
    (1, "FIELD_SERVICE",  "On-site service performed at the customer site"),
    (2, "DEPOT_REPAIR",   "Repair performed in one of our own workshops"),
    (3, "MANUFACTURING",  "Production against a works order"),
    (4, "GENERAL_LEAVE",  "Leave and other non-productive booked time"),
]

COST_COMPONENTS = [("100", "LABOUR"), ("200", "MATERIAL"), ("300", "TOOL"), ("400", "OVERHEAD")]
WAREHOUSES = ["MAIN", "WIP", "GOODS-IN", "QUARANTINE", "CONSIGN"]

EPOCH = dt.datetime(1970, 1, 1)


def pad(value: str, width: int = 10) -> str:
    """Trap 6. Source systems export fixed-width padded text."""
    return str(value).ljust(width)


# --------------------------------------------------------------------------- reference
def gen_entity() -> pd.DataFrame:
    """
    Trap 7. This table is deliberately called ENTITY, which is a reserved word in
    several SQL engines. Loaders that do not quote identifiers fail on it -- see
    ingest/load.py for how that is handled rather than worked around by renaming.
    """
    return pd.DataFrame(
        [
            {
                "company_code": c,
                "company_name": name,
                "short_code": pad(short, 6),
                "source_system": system,
                "stores_utc": utc,
                "local_timezone": tz,
                "is_manufacturing": 1 if c in MANUFACTURING else 0,
            }
            for c, name, short, system, utc, tz in COMPANIES
        ]
    )


def gen_item_master(rng: random.Random, n: int = 900) -> pd.DataFrame:
    families = ["SEAL", "PISTON", "CYLNDR", "VALVE", "HOSE", "PUMP", "FITTING", "BEARING"]
    rows = []
    for i in range(n):
        fam = rng.choice(families)
        rows.append(
            {
                "item_code": pad(f"{fam}-{i:05d}", 16),
                "description": f"{fam.title()} assembly, type {rng.randint(1, 40)}",
                "item_family": pad(fam, 10),
                "unit_of_measure": rng.choice(["EA", "EA", "EA", "M", "KG"]),
                "standard_cost": round(rng.uniform(2.0, 900.0), 2),
            }
        )
    return pd.DataFrame(rows)


def gen_warehouse_master() -> pd.DataFrame:
    rows = []
    for c, *_ in [(x[0],) for x in COMPANIES]:
        for w in WAREHOUSES:
            rows.append(
                {
                    "company_code": c,
                    "warehouse_code": pad(w, 12),
                    "description": f"{w.title()} store",
                    "is_wip": 1 if w == "WIP" else 0,
                }
            )
    return pd.DataFrame(rows)


def gen_cost_component_master() -> pd.DataFrame:
    return pd.DataFrame(
        [{"cost_component_code": pad(c, 6), "description": d} for c, d in COST_COMPONENTS]
    )


def gen_work_centre_master(rng: random.Random) -> pd.DataFrame:
    rows = []
    for c in MANUFACTURING:
        for i in range(1, 19):
            rows.append(
                {
                    "company_code": c,
                    "work_centre_code": pad(f"WC{i:03d}", 10),
                    "description": f"Work centre {i}",
                    "capacity_hours_per_week": float(rng.choice([37.5, 40.0, 75.0, 80.0])),
                    "labour_rate": round(rng.uniform(38.0, 96.0), 2),
                }
            )
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- facts
def gen_inventory_cost(rng: random.Random, np_rng, items: pd.DataFrame, n: int) -> pd.DataFrame:
    """
    Inventory transaction cost. Traps 1, 3, 4, 5, 9 and 10 all live here.

    Trap 3 is the structural one. txn_timestamp is unique to the second, and line_seq
    does NOT restart per day. A model that keys on (company, item, warehouse,
    cost_component, DATE(txn_timestamp), line_seq) will collapse many genuine
    same-day transactions onto a single key. The grain must carry the full timestamp.

    PER-ITEM PRICE BANDS AND VOLATILITY

    unit_cost is drawn around each item's standard_cost rather than from one global range.
    That is both more realistic and a prerequisite for trap 10: if every item could
    legitimately cost anything, a contextual price anomaly would not exist as a concept.

    Each item also gets its own VOLATILITY, between 3% and 35%. This matters more than it
    looks. If every item held a tight band, the injected anomalies would separate perfectly
    and any detector would score 100% -- which would prove nothing except that the test was
    rigged. Real catalogues contain both stable commodity parts and volatile ones subject
    to metal prices and exchange rates, so a 2.5x move is a genuine anomaly on a stable
    part and unremarkable on a volatile one.

    The result is deliberate overlap between the top of the normal distribution and the
    bottom of the anomalous one. Detection is therefore a real precision-recall tradeoff
    rather than a threshold anyone could eyeball.
    """
    # Per-item price anchor and volatility. Dict lookups rather than a merge, because this
    # loop runs 200k times. Volatility is not written to the item master -- it is a property
    # of the real world, not a column any ERP would hold.
    price_of = dict(zip(items["item_code"], items["standard_cost"]))
    volatility_of = {code: rng.uniform(0.03, 0.35) for code in items["item_code"]}
    item_codes = items["item_code"].tolist()
    start = dt.datetime(2023, 1, 1)
    rows = []

    for _ in range(n):
        code, _name, _short, _system, is_utc, _tz = rng.choice(COMPANIES)

        # Deliberately coarse: many transactions land on the same DAY for the same
        # item / warehouse / component, differing only by time of day.
        day_offset = rng.randint(0, 900)
        ts = start + dt.timedelta(
            days=day_offset,
            hours=rng.randint(0, 23),
            minutes=rng.randint(0, 59),
            seconds=rng.randint(0, 59),
        )

        item = rng.choice(item_codes)
        base_price = price_of[item]
        vol = volatility_of[item]

        qty = round(rng.uniform(1, 25), 3)
        # Log-normal-ish variation around this item's own price, scaled by its volatility.
        # Clamped at 0.25x so a price never goes negative or absurdly low.
        factor = max(0.25, rng.gauss(1.0, vol))
        unit_cost = round(base_price * factor, 4)
        defect = CLEAN

        roll = rng.random()

        if roll < 0.00025:
            # TRAP 4, absolute. Quantity is nonsense on sight. total_amount stays correct,
            # so anything deriving cost as qty * unit_cost explodes while the source's own
            # figure remains usable.
            qty = float(rng.choice([2_560_000_00, 8_912_000_00, 1_000_000_000]))
            total_amount = round(rng.uniform(500, 40_000), 2)
            defect = D_CORRUPT_QTY

        elif roll < 0.00105:
            # TRAP 10, contextual. 2x to 9x THIS item's normal price. The lower end of that
            # range overlaps the upper tail of a volatile item's legitimate variation, which
            # is intentional -- it is what stops this being a threshold problem.
            #
            # total_amount is computed from the inflated cost, so the row is internally
            # consistent: quantity x unit_cost genuinely equals the amount. Nothing about
            # the row contradicts itself, which is precisely why no single-row rule works.
            unit_cost = round(base_price * rng.uniform(2.0, 9.0), 4)
            total_amount = round(qty * unit_cost, 2)
            defect = D_PRICE_ANOMALY

        else:
            total_amount = round(qty * unit_cost, 2)

        rows.append(
            {
                "company_code": code,
                "item_code": item,
                "warehouse_code": pad(rng.choice(WAREHOUSES), 12),
                "cost_component_code": pad(rng.choice([c for c, _ in COST_COMPONENTS]), 6),
                # Trap 1. MODERN_ERP companies write UTC. Legacy systems write local time.
                "txn_timestamp": ts,
                # Trap 3. Does not restart per day. Mostly 1, which is what makes the
                # truncated-date grain look plausible right up until you count duplicates.
                "line_seq": rng.choices([1, 1, 1, 1, 2, 3], k=1)[0],
                "quantity": qty,
                "unit_cost": unit_cost,
                "total_amount": total_amount,
                "is_wip": rng.choices([0, 1], weights=[9, 1], k=1)[0],
                "is_deleted": 0,
                "defect_type": defect,
            }
        )

    df = pd.DataFrame(rows)

    # Trap 9. One subsidiary genuinely double-extracted a small number of rows. These are
    # real duplicates in the source, not a modelling error -- and the difference matters.
    dupes = df[df["company_code"] == 600].head(9).copy()
    dupes["defect_type"] = D_DUPLICATE
    df.loc[dupes.index, "defect_type"] = D_DUPLICATE
    df = pd.concat([df, dupes], ignore_index=True)

    # Trap 5. Epoch means "no date". A handful of rows never got a real timestamp.
    # Only relabel rows that were otherwise clean, so a corrupt quantity is not masked.
    epoch_idx = df.sample(frac=0.0015, random_state=SEED).index
    df.loc[epoch_idx, "txn_timestamp"] = EPOCH
    clean_epoch = df.index.isin(epoch_idx) & (df["defect_type"] == CLEAN)
    df.loc[clean_epoch, "defect_type"] = D_EPOCH

    return df


def inject_volume_incidents(df: pd.DataFrame, rng: random.Random) -> pd.DataFrame:
    """
    Injects two operational incidents that no row-level test can detect.

    1. OUTAGE. One subsidiary's extract fails for three consecutive days. Every row it
       should have sent is simply absent. Nothing is malformed, so every uniqueness,
       not-null and referential test still passes. The fact table reconciles perfectly
       to the source, because the source itself is short.

       This is the single most common data incident in production and the hardest to
       catch with row-level assertions, because you cannot test rows that are not there.
       It needs a model of expected volume. See ml/volume_monitor.py.

    2. SPIKE. Another subsidiary double-runs an extract, producing roughly six times the
       normal volume for one day. The rows are legitimate, so this is not a defect --
       but it is worth alerting on, because it usually means a job ran twice.
    """
    start = dt.datetime(2023, 1, 1)

    # --- outage: drop the rows entirely
    o_from = start + dt.timedelta(days=OUTAGE_START_DAY)
    o_to = o_from + dt.timedelta(days=OUTAGE_DAYS)
    outage_mask = (
        (df["company_code"] == OUTAGE_COMPANY)
        & (df["txn_timestamp"] >= o_from)
        & (df["txn_timestamp"] < o_to)
    )
    dropped = int(outage_mask.sum())
    df = df.loc[~outage_mask].reset_index(drop=True)

    # --- spike: duplicate one day's rows for another company, with distinct timestamps
    s_from = start + dt.timedelta(days=SPIKE_DAY)
    s_to = s_from + dt.timedelta(days=1)
    spike_src = df.loc[
        (df["company_code"] == SPIKE_COMPANY)
        & (df["txn_timestamp"] >= s_from)
        & (df["txn_timestamp"] < s_to)
    ]
    extra = []
    for _ in range(SPIKE_MULTIPLIER - 1):
        block = spike_src.copy()
        # Shift by whole seconds so these do not collide on the fact grain. They are
        # genuinely distinct transactions, just far too many of them.
        block["txn_timestamp"] = block["txn_timestamp"] + pd.to_timedelta(
            [rng.randint(1, 80_000) for _ in range(len(block))], unit="s"
        )
        extra.append(block)
    if extra:
        df = pd.concat([df] + extra, ignore_index=True)

    added = sum(len(b) for b in extra)
    print(f"  volume incidents: -{dropped:,} rows (outage, company {OUTAGE_COMPANY}), "
          f"+{added:,} rows (spike, company {SPIKE_COMPANY})")
    return df


def gen_time_entries_modern(rng: random.Random, n: int, n_ops: int) -> pd.DataFrame:
    """
    Time and attendance from MODERN_ERP. Traps 1 and 2 live here.

    Trap 2 is the nastiest defect in this repo because nothing about the VALUES reveals
    it: work_hours is booked in seconds on a minority of manufacturing rows, and the
    unit sits in a separate column. A SUM() still returns a number that looks like hours.

    Note on order numbering: manufacturing bookings (module 3) are booked against
    PRODUCTION orders in the same PO namespace as production_operations, and carry an
    operation_seq. Without that the variance model has nothing to join to. Service and
    depot bookings use their own WO namespace, as they would in a real ERP.
    """
    modern = [c for c in COMPANIES if c[3] == "MODERN_ERP"]
    start = dt.datetime(2023, 1, 1)
    rows = []
    for i in range(n):
        code, *_ = rng.choice(modern)
        module = rng.choices([1, 2, 3, 4], weights=[5, 3, 4, 2], k=1)[0]
        booked = start + dt.timedelta(days=rng.randint(0, 900), hours=rng.randint(5, 20))

        hours = round(rng.uniform(0.25, 11.0), 2)
        unit = "HR"
        # Manufacturing bookings from the shop-floor terminal arrive in seconds.
        if module == 3 and rng.random() < 0.18:
            hours = round(hours * 3600)
            unit = "SEC"

        if module == 3:
            # Same namespace as production_operations, so the variance join resolves.
            # n_ops is the operation count; operations are grouped 3 per order, so the
            # order pool is a third of that.
            order = pad(f"PO{20000 + rng.randint(0, max(n_ops // 3 - 1, 1))}", 12)
            op_seq = rng.choice([10, 20, 30])
            wc = pad(f"WC{rng.randint(1, 18):03d}", 10)
        else:
            order = pad(f"WO{rng.randint(10000, 99999)}", 12)
            op_seq = None
            wc = None

        rows.append(
            {
                "company_code": code,
                "employee_code": pad(f"E{rng.randint(1, 420):05d}", 10),
                "booking_timestamp": booked,
                "service_module_key": module,
                "order_no": order,
                "operation_seq": op_seq,
                "work_centre_code": wc,
                "work_hours": hours,
                "hours_unit": unit,           # <- the column that must be read
                "hourly_rate": round(rng.uniform(35, 92), 2),
                "is_deleted": 0,
            }
        )
    return pd.DataFrame(rows)


def gen_time_entries_legacy(rng: random.Random, n: int) -> pd.DataFrame:
    """
    Time entries from the LEGACY_ACCT and WORKSHOP_SYS subsidiaries.

    Trap 8, column drift: this extract has NO hours_unit, NO work_centre_code and NO
    is_deleted column, and it names things differently. The union layer has to reconcile
    that rather than assuming every source looks the same. It also stores LOCAL time,
    so it must NOT be timezone converted.
    """
    legacy = [c for c in COMPANIES if c[3] in ("LEGACY_ACCT", "WORKSHOP_SYS")]
    start = dt.datetime(2023, 1, 1)
    rows = []
    for _ in range(n):
        code, *_ = rng.choice(legacy)
        rows.append(
            {
                "co": code,                                    # different column name
                "emp_id": pad(f"E{rng.randint(1, 260):05d}", 10),
                "work_date": (start + dt.timedelta(days=rng.randint(0, 900))).date(),
                "job_ref": pad(f"JOB{rng.randint(1000, 9999)}", 12),
                "hours_worked": round(rng.uniform(0.5, 10.0), 2),   # always hours here
                "rate": round(rng.uniform(30, 78), 2),
                "job_type": rng.choice(["SERVICE", "SERVICE", "WORKSHOP", "LEAVE"]),
            }
        )
    return pd.DataFrame(rows)


def gen_work_orders(rng: random.Random, n: int) -> pd.DataFrame:
    """
    Work orders. Trap 5 again, plus a deliberate data quality problem: a small number of
    orders have a completion date BEFORE the order date, which yields a negative lead
    time. Real ERP data does this and a naive DATEDIFF happily reports it.
    """
    start = dt.datetime(2023, 1, 1)
    rows = []
    for i in range(n):
        code, _n, _s, _sys, _utc, _tz = rng.choice(COMPANIES)
        ordered = start + dt.timedelta(days=rng.randint(0, 880), hours=rng.randint(6, 18))

        status = rng.choices([20, 25, 30, 10, 5], weights=[4, 4, 3, 2, 1], k=1)[0]
        if status in (20, 25, 30):
            lead = rng.choices([rng.randint(1, 45), rng.randint(45, 240)], weights=[8, 2], k=1)[0]
            completed = ordered + dt.timedelta(days=lead, hours=rng.randint(0, 20))
            # ~0.9% have a completion date before the order date.
            if rng.random() < 0.009:
                completed = ordered - dt.timedelta(days=rng.randint(1, 400))
        else:
            completed = EPOCH        # open orders carry the epoch, not NULL

        rows.append(
            {
                "company_code": code,
                "order_no": pad(f"WO{10000 + i}", 12),
                "order_timestamp": ordered,
                "completion_timestamp": completed,
                "order_status": status,
                "service_module_key": rng.choices([1, 2, 3], weights=[5, 3, 2], k=1)[0],
                "customer_code": pad(f"C{rng.randint(1, 900):05d}", 10),
                "estimated_value": round(rng.uniform(200, 60_000), 2),
                "is_deleted": 0,
            }
        )
    return pd.DataFrame(rows)


def gen_operations(rng: random.Random, n: int, manufacturing_only: bool = True) -> pd.DataFrame:
    """
    Production order operations -- planned hours and cost, for the variance model.

    Deliberate characteristic: a meaningful share of operations are PLANNED but never
    BOOKED. A naive variance percentage reports those as exactly -100%, which is not a
    real -100% and drags any average down. The model must return NULL and flag them.
    """
    start = dt.datetime(2023, 1, 1)
    rows = []
    for i in range(n):
        code = rng.choice(MANUFACTURING)
        planned_h = round(rng.uniform(0.5, 60.0), 2)
        # Three operations per production order, sequences 10 / 20 / 30. Real production
        # orders have several routing steps, and it means the variance join has a
        # meaningful hit rate rather than matching almost nothing.
        rows.append(
            {
                "company_code": code,
                "production_order": pad(f"PO{20000 + i // 3}", 12),
                "operation_seq": 10 * (i % 3 + 1),
                "work_centre_code": pad(f"WC{rng.randint(1, 18):03d}", 10),
                "operation_status": rng.choices([7, 8, 3], weights=[5, 4, 2], k=1)[0],
                "planned_hours": planned_h,
                "planned_rate": round(rng.uniform(40, 95), 2),
                "planned_amount": round(planned_h * rng.uniform(40, 95), 2),
                "order_timestamp": start + dt.timedelta(days=rng.randint(0, 880)),
            }
        )
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- main
TABLES = {}


# Join key for the ground-truth labels.
#
# Deliberately EXCLUDES txn_timestamp, which is the obvious choice and the wrong one.
# Staging converts timestamps to the reporting timezone for the three subsidiaries whose
# systems store UTC (trap 1), so a raw timestamp recorded here no longer matches the value
# in the fact table for half the group. Joining on it silently drops about 46% of labels
# and makes the model look far worse than it is.
#
# I hit exactly that while building this, which is a neat demonstration that trap 1 bites
# your tooling as readily as your reports.
#
# quantity and unit_cost pass through staging unchanged apart from a cast, so together with
# the business keys they identify a row without relying on anything the transformation
# rewrites. Collisions across 200k rows are vanishingly unlikely at 3 and 4 decimal places.
LABEL_JOIN_COLUMNS = [
    "company_code", "item_code", "warehouse_code",
    "cost_component_code", "line_seq", "quantity", "unit_cost",
]


def build(rows: int) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """
    Builds every source extract, plus the ground-truth label frame.

    Returns (tables, labels). The labels are deliberately returned separately so that
    nothing in the caller can accidentally write defect_type into a source extract.
    """
    rng = random.Random(SEED)
    np_rng = np.random.default_rng(SEED)

    items = gen_item_master(rng)
    n_ops = max(rows // 10, 1_500)

    inventory = gen_inventory_cost(rng, np_rng, items, rows)
    inventory = inject_volume_incidents(inventory, rng)

    # Split the answers off before anything is written.
    labels = (
        inventory.loc[
            inventory["defect_type"] != CLEAN, LABEL_JOIN_COLUMNS + ["defect_type"]
        ]
        .drop_duplicates()
        .reset_index(drop=True)
    )
    inventory = inventory.drop(columns=["defect_type"])

    out = {
        # "entity" is the reserved-word table. Kept deliberately.
        "entity": gen_entity(),
        "item_master": items,
        "warehouse_master": gen_warehouse_master(),
        "cost_component_master": gen_cost_component_master(),
        "work_centre_master": gen_work_centre_master(rng),
        "inventory_cost_txn": inventory,
        "time_entries_modern": gen_time_entries_modern(rng, max(rows // 2, 5_000), n_ops),
        "time_entries_legacy": gen_time_entries_legacy(rng, max(rows // 4, 2_500)),
        "work_orders": gen_work_orders(rng, max(rows // 8, 2_000)),
        "production_operations": gen_operations(rng, n_ops),
    }
    return out, labels


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate synthetic multi-company ERP source data.")
    ap.add_argument("--rows", type=int, default=200_000,
                    help="approximate inventory transaction rows (default 200000)")
    ap.add_argument("--out", default=RAW_DIR)
    ap.add_argument("--labels", default=LABEL_DIR)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    os.makedirs(args.labels, exist_ok=True)

    print(f"Generating synthetic source data for Nordvik Industrial Group -> {args.out}\n")
    tables, labels = build(args.rows)

    print()
    print(f"{'table':<26}{'rows':>10}  notes")
    print("-" * 78)
    notes = {
        "entity": "reserved word table name (trap 7)",
        "inventory_cost_txn": "sub-day grain, bad qty, prices, dupes, epoch (3,4,5,9,10)",
        "time_entries_modern": "seconds/hours mixed, UTC (traps 1,2)",
        "time_entries_legacy": "column drift, local time (traps 1,8)",
        "work_orders": "negative lead times, epoch for open orders (trap 5)",
        "production_operations": "planned-but-never-booked operations",
    }
    for name, df in tables.items():
        path = os.path.join(args.out, f"{name}.parquet")
        df.to_parquet(path, index=False)
        print(f"{name:<26}{len(df):>10,}  {notes.get(name, '')}")

    total = sum(len(d) for d in tables.values())
    print("-" * 78)
    print(f"{'TOTAL':<26}{total:>10,}")

    # Ground truth, written well away from data/raw so it cannot be loaded as a source.
    label_path = os.path.join(args.labels, "defect_labels.parquet")
    labels.to_parquet(label_path, index=False)

    print(f"\nGround truth -> {label_path}  (loads into schema 'ml', no dbt model reads it)")
    print(f"{'defect type':<26}{'rows':>10}")
    print("-" * 78)
    for defect, count in labels["defect_type"].value_counts().items():
        print(f"{defect:<26}{count:>10,}")
    print("-" * 78)
    print(f"{'LABELLED TOTAL':<26}{len(labels):>10,}")

    print("\nNext:  python -m ingest.load          (load into DuckDB)")
    print("       dbt build --profiles-dir .     (transform and test)")


if __name__ == "__main__":
    main()
