"""
Feature engineering for anomaly detection on the inventory cost fact.

Reads from the MARTS layer, not from raw. That is deliberate: monitoring should watch the
tables the business actually reports from, so that a defect introduced by a transformation
is as visible as one that arrived in the source. Watching raw only tells you the source
was fine.

--------------------------------------------------------------------------------------
THE LEAKAGE PROBLEM, AND WHY THIS FILE IS MOSTLY ABOUT WHAT IT EXCLUDES
--------------------------------------------------------------------------------------

Two columns on fct_inventory_transaction_cost encode the answer:

    is_quantity_reliable    is literally the output of the rule (quantity > 1e6)
    amount_if_recomputed    becomes astronomic exactly when quantity is corrupt

Include either and the model scores near-perfectly on corrupt quantities while having
learned nothing. You would ship it, the dashboard would look excellent, and the first
genuinely novel defect would sail straight through.

This is the single most common way ML pipelines produce impressive and worthless numbers.
Both columns are excluded below at EXCLUDED_LEAKY, and the exclusion is asserted in
build_features() so nobody can quietly add them back.

--------------------------------------------------------------------------------------
WHY THE FEATURES ARE MOSTLY RELATIVE, NOT ABSOLUTE
--------------------------------------------------------------------------------------

An absolute unit cost carries almost no signal. 850 is normal for a pump and absurd for a
washer, so a model given raw price alone can only learn a global threshold, which is
exactly what the SQL rule already does better and faster.

The features that matter are therefore CONTEXTUAL -- how far this row sits from what is
normal for THIS item:

    unit_cost_z_by_item     robust z-score of price within the item
    unit_cost_ratio_to_item this row's price divided by the item's median price
    quantity_z_by_item      same idea for quantity

Robust statistics (median and MAD) rather than mean and standard deviation, because the
anomalies are IN the data being summarised. A mean price is dragged upward by the very
rows we are hunting; a median barely moves. Using a mean here would quietly hide the
defects behind their own influence on the baseline.
"""
from __future__ import annotations

import os

import duckdb
import numpy as np
import pandas as pd

DB = os.path.join("data", "edw.duckdb")

# Columns that must never become features. See the module docstring.
EXCLUDED_LEAKY = ["is_quantity_reliable", "amount_if_recomputed"]

FEATURE_COLUMNS = [
    "quantity",
    "unit_cost",
    "transaction_amount",
    "unit_cost_z_by_item",
    "unit_cost_ratio_to_item",
    "quantity_z_by_item",
    "amount_per_unit",
    "amount_z_by_item",
    "hour_of_day",
    "is_wip",
    "item_txn_count",
]

# Join key for ground truth.
#
# Note what is absent: txn_timestamp. Staging converts timestamps to the reporting
# timezone for the three subsidiaries storing UTC (trap 1), so the raw timestamp the
# generator recorded no longer equals the value in the fact table for half the group.
# Joining on it drops roughly 46% of labels and understates the model badly. Using the
# measures instead sidesteps anything the transformation rewrites.
LABEL_JOIN = ["company_key", "item_code", "warehouse_code",
              "cost_component_code", "line_seq", "quantity", "unit_cost"]


def _robust_z(values: pd.Series) -> pd.Series:
    """
    Median-absolute-deviation z-score, computed within a group.

    The 1.4826 constant rescales MAD so that for normally distributed data this is
    comparable to a standard z-score. Where MAD is zero -- an item whose price never
    moves -- the result is 0 rather than infinity, because a constant price with one
    outlier is handled by the ratio feature instead.
    """
    med = values.median()
    mad = (values - med).abs().median()
    if mad == 0 or np.isnan(mad):
        return pd.Series(np.zeros(len(values)), index=values.index)
    return (values - med) / (1.4826 * mad)


def load_fact(db_path: str = DB) -> pd.DataFrame:
    """Reads the fact table. Leaky columns are not even selected."""
    if not os.path.exists(db_path):
        raise FileNotFoundError(
            f"{db_path} not found. Run 'python -m ingest.generate' then "
            "'python -m ingest.load' then 'dbt build --profiles-dir .' first."
        )
    con = duckdb.connect(db_path, read_only=True)
    df = con.execute(
        """
        select
            company_key,
            source_item_code        as item_code,
            source_warehouse_code   as warehouse_code,
            source_cost_component_code as cost_component_code,
            txn_timestamp,
            line_seq,
            quantity,
            unit_cost,
            transaction_amount,
            is_wip
        from main_marts.fct_inventory_transaction_cost
        where is_deleted = 0
        """
    ).df()
    con.close()
    return df


def load_labels(db_path: str = DB) -> pd.DataFrame:
    """
    Reads ground truth from the ml schema.

    This is used ONLY to score detection after the fact. It is never joined before
    features are built, and no feature is derived from it.
    """
    con = duckdb.connect(db_path, read_only=True)
    try:
        df = con.execute(
            """
            select company_code as company_key, item_code, warehouse_code,
                   cost_component_code, line_seq, quantity, unit_cost, defect_type
            from ml.defect_labels
            """
        ).df()
    except duckdb.CatalogException:
        df = pd.DataFrame(columns=LABEL_JOIN + ["defect_type"])
    con.close()
    return df


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds contextual features. Returns the frame with FEATURE_COLUMNS present.

    Rows for items seen fewer than MIN_ITEM_ROWS times keep a z-score of 0. With only a
    handful of observations there is no reliable notion of "normal for this item", and
    inventing one produces false positives on rare parts -- which is how monitoring
    loses the trust of the people who have to action the alerts.
    """
    MIN_ITEM_ROWS = 20

    df = df.copy()
    df["amount_per_unit"] = np.where(
        df["quantity"] > 0, df["transaction_amount"] / df["quantity"], np.nan
    )
    df["hour_of_day"] = pd.to_datetime(df["txn_timestamp"]).dt.hour.fillna(0).astype(int)

    grp = df.groupby("item_code", sort=False)
    df["item_txn_count"] = grp["unit_cost"].transform("size")

    df["unit_cost_z_by_item"] = grp["unit_cost"].transform(_robust_z)
    df["quantity_z_by_item"] = grp["quantity"].transform(_robust_z)
    df["amount_z_by_item"] = grp["transaction_amount"].transform(_robust_z)

    item_median = grp["unit_cost"].transform("median")
    df["unit_cost_ratio_to_item"] = np.where(
        item_median > 0, df["unit_cost"] / item_median, 1.0
    )

    thin = df["item_txn_count"] < MIN_ITEM_ROWS
    for col in ["unit_cost_z_by_item", "quantity_z_by_item", "amount_z_by_item"]:
        df.loc[thin, col] = 0.0

    df[FEATURE_COLUMNS] = (
        df[FEATURE_COLUMNS]
        .replace([np.inf, -np.inf], np.nan)
        .fillna(0.0)
    )

    # Guard rail. If somebody adds a leaky column to FEATURE_COLUMNS, fail loudly here
    # rather than shipping a model with a flawless score and no value.
    leaked = [c for c in EXCLUDED_LEAKY if c in FEATURE_COLUMNS]
    if leaked:
        raise AssertionError(
            f"Label leakage: {leaked} must never be used as features. "
            "See the leakage note in ml/features.py."
        )
    return df


def attach_labels(features: pd.DataFrame, labels: pd.DataFrame) -> pd.DataFrame:
    """
    Left joins ground truth on for scoring. Unlabelled rows are CLEAN.

    Floats are rounded before joining. Parquet round-trips doubles exactly, but rounding
    to the precision the generator actually produced removes any dependence on that
    holding true across engines -- the Snowflake path stores these as FLOAT.

    The nine known source duplicates share every column by definition, so the join can
    match more than one row. Duplicates are dropped afterwards, which slightly understates
    duplicate recall. Stated rather than hidden, because it affects one class out of four.
    """
    if labels.empty:
        features = features.copy()
        features["defect_type"] = "CLEAN"
        return features

    features = features.copy()
    labels = labels.copy()
    for frame in (features, labels):
        for col in ["item_code", "warehouse_code", "cost_component_code"]:
            frame[col] = frame[col].astype(str).str.strip()
        frame["quantity"] = frame["quantity"].round(3)
        frame["unit_cost"] = frame["unit_cost"].round(4)
        frame["line_seq"] = frame["line_seq"].astype(int)
        frame["company_key"] = frame["company_key"].astype(int)

    merged = features.merge(labels, on=LABEL_JOIN, how="left")
    merged = merged.drop_duplicates(subset=LABEL_JOIN + ["txn_timestamp"])
    merged["defect_type"] = merged["defect_type"].fillna("CLEAN")
    return merged


def get_dataset(db_path: str = DB) -> pd.DataFrame:
    """One call to produce a scored-ready frame: fact + features + labels."""
    fact = load_fact(db_path)
    feats = build_features(fact)
    return attach_labels(feats, load_labels(db_path))


if __name__ == "__main__":
    data = get_dataset()
    print(f"rows            {len(data):,}")
    print(f"features        {len(FEATURE_COLUMNS)}")
    print(f"excluded (leaky){'':<1}{EXCLUDED_LEAKY}")
    print()
    print("label distribution")
    print("-" * 46)
    for defect, count in data["defect_type"].value_counts().items():
        print(f"  {defect:<24}{count:>10,}")
    print()
    print("contextual feature spread by class (unit_cost_ratio_to_item)")
    print("-" * 46)
    summary = data.groupby("defect_type")["unit_cost_ratio_to_item"].describe()[
        ["count", "mean", "50%", "max"]
    ]
    print(summary.round(2).to_string())
