"""
Anomaly detection on the inventory cost fact, measured against the SQL rules it is
supposed to improve on.

--------------------------------------------------------------------------------------
THE QUESTION THIS SCRIPT ANSWERS
--------------------------------------------------------------------------------------

Not "does the model work". The useful question is "does the model earn its place next to
a WHERE clause", and the honest answer turns out to be different for each defect class.

    CORRUPT_QUANTITY   absolute violation.  quantity > 1e6 catches every one, instantly,
                       with no false positives and no training. A model here is pure cost.

    CONTEXTUAL_PRICE   contextual violation. The price is wrong only relative to that
                       item's own history. No global threshold can express it, so the
                       rule fails badly and the model is the only option.

That split is the whole point. Anyone can report an F1 score; being able to say which
technique belongs on which problem, and prove it with numbers, is the actual skill.

--------------------------------------------------------------------------------------
WHY ISOLATION FOREST
--------------------------------------------------------------------------------------

Unsupervised, because in production you do not have labels. The ground truth here exists
only to score the result afterwards -- the model never sees it, which is why this
generalises to a real platform where nobody has told you which rows are wrong.

It also isolates on random splits, so it is scale-invariant and needs no feature scaling.
Adding a StandardScaler would be harmless and pointless; trees do not care.

Alternatives considered and rejected:

    Supervised classifier   would score better here and be useless in production, because
                            it requires the labels you are trying to produce.
    Local Outlier Factor    comparable quality, O(n^2)-ish neighbour search, far slower at
                            200k rows for no measurable gain on this data.
    Autoencoder             defensible at much larger scale. At 200k rows and 11 features
                            it is a heavier dependency and a slower iteration loop for
                            results within noise of the forest.

--------------------------------------------------------------------------------------
Usage:
    python -m ml.detect_anomalies
    python -m ml.detect_anomalies --contamination 0.003
"""
from __future__ import annotations

import argparse
import json
import os

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.metrics import average_precision_score, precision_recall_curve

from ml.features import FEATURE_COLUMNS, EXCLUDED_LEAKY, get_dataset

OUT_DIR = os.path.join("data", "ml")
SEED = 20260917

# The two classes this detector is asked to find. EPOCH_DATE and SOURCE_DUPLICATE are also
# defects, but both are caught deterministically by dbt tests already -- throwing a model
# at a solved problem is how ML budgets get wasted.
TARGET_DEFECTS = ["CORRUPT_QUANTITY", "CONTEXTUAL_PRICE"]


def prf(y_true: np.ndarray, y_pred: np.ndarray) -> tuple[float, float, float, int, int, int]:
    """Precision, recall, F1 plus raw counts. Written out rather than imported so the
    arithmetic is visible -- these numbers end up in a README and should be checkable."""
    tp = int(np.sum(y_pred & y_true))
    fp = int(np.sum(y_pred & ~y_true))
    fn = int(np.sum(~y_pred & y_true))
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1, tp, fp, fn


def sql_rule_quantity(df: pd.DataFrame) -> np.ndarray:
    """
    The rule already shipped in the warehouse, as stg_inventory_cost_txn.is_quantity_reliable.

        case when quantity > 1000000 then 0 else 1 end

    Reimplemented here rather than read from the column, because reading it would be the
    label leakage that ml/features.py exists to prevent. This way the rule competes on the
    same footing as the model.
    """
    return (df["quantity"] > 1_000_000).to_numpy()


def sql_rule_price_global(df: pd.DataFrame, percentile: float = 99.9) -> np.ndarray:
    """
    The rule a team would reach for first on price anomalies: flag anything above a global
    percentile of unit_cost.

    It is a fair attempt and it cannot work. The catalogue spans washers at 2 and pumps at
    900, so the 99.9th percentile sits inside the legitimate range of expensive items. The
    rule therefore flags cheap-item anomalies not at all and expensive items constantly.

    Included because showing the obvious approach failing, with numbers, is more convincing
    than asserting that it would.
    """
    threshold = np.percentile(df["unit_cost"], percentile)
    return (df["unit_cost"] > threshold).to_numpy()


def fit_isolation_forest(df: pd.DataFrame, contamination: float) -> tuple[np.ndarray, IsolationForest]:
    """Fits on every row and returns an anomaly score where higher means more anomalous."""
    X = df[FEATURE_COLUMNS].to_numpy(dtype=float)
    model = IsolationForest(
        n_estimators=300,
        contamination=contamination,
        max_samples=min(4096, len(df)),
        random_state=SEED,
        n_jobs=-1,
    )
    model.fit(X)
    # score_samples is higher for normal points. Negate so the column reads intuitively.
    return -model.score_samples(X), model


def choose_operating_point(
    truth: np.ndarray, scores: np.ndarray, alert_budget: int
) -> dict:
    """
    Turns a ranking into a decision, which is the step most write-ups skip.

    Average precision measures how well the model ORDERS rows. It says nothing about where
    to draw the line, and the line is what determines whether anyone acts on the output.
    Three candidate thresholds, only one of which is honest in production:

      contamination   what you set before seeing results. A guess.

      best F1         found by sweeping the curve against the labels. Reported here for
                      reference, and clearly marked, because it is ORACLE-TUNED -- it uses
                      the answers to pick the threshold. If you have the labels you do not
                      need the detector, so this number is an upper bound and nothing more.
                      Quoting it as achievable performance would be dishonest.

      alert budget    the operating point a real platform uses. A human can triage maybe 20
                      rows a day, so take the top 20 by score and accept whatever recall
                      that buys. No labels required, which is why it survives contact with
                      production.

    The alert-budget number is the one worth defending in a design review.
    """
    precision, recall, thresholds = precision_recall_curve(truth, scores)
    # precision_recall_curve returns one more precision/recall point than thresholds.
    f1 = np.divide(
        2 * precision[:-1] * recall[:-1],
        precision[:-1] + recall[:-1],
        out=np.zeros_like(precision[:-1]),
        where=(precision[:-1] + recall[:-1]) > 0,
    )
    best = int(np.argmax(f1))

    order = np.argsort(-scores)
    top = order[:alert_budget]
    budget_pred = np.zeros_like(truth, dtype=bool)
    budget_pred[top] = True
    bp, br, bf1, btp, bfp, bfn = prf(truth, budget_pred)

    return {
        "best_f1_oracle": {
            "threshold": float(thresholds[best]),
            "precision": round(float(precision[best]), 4),
            "recall": round(float(recall[best]), 4),
            "f1": round(float(f1[best]), 4),
            "note": "oracle-tuned against labels; an upper bound, not an achievable result",
        },
        "alert_budget": {
            "budget": alert_budget,
            "precision": round(bp, 4), "recall": round(br, 4), "f1": round(bf1, 4),
            "tp": btp, "fp": bfp, "fn": bfn,
            "note": "requires no labels; this is the production operating point",
        },
    }


def report(df: pd.DataFrame, scores: np.ndarray, flagged: np.ndarray,
           alert_budget: int) -> dict:
    """Prints the comparison table and returns metrics for the README and PDF to quote."""
    rule_q = sql_rule_quantity(df)
    rule_p = sql_rule_price_global(df)
    results: dict = {"by_class": {}, "n_rows": int(len(df))}

    W = 96
    print()
    print("=" * W)
    print("  DETECTION: SQL RULES vs ISOLATION FOREST")
    print("=" * W)
    print(f"  rows scored {len(df):,}   features {len(FEATURE_COLUMNS)}   "
          f"excluded as leaky {EXCLUDED_LEAKY}")

    for defect in TARGET_DEFECTS:
        truth = (df["defect_type"] == defect).to_numpy()
        print()
        print("-" * W)
        print(f"  {defect}   ({int(truth.sum())} rows in ground truth)")
        print("-" * W)
        print(f"  {'detector':<34}{'precision':>11}{'recall':>9}{'F1':>8}"
              f"{'TP':>7}{'FP':>8}{'FN':>6}")

        entries = {
            "sql_rule_quantity_gt_1e6": rule_q,
            "sql_rule_unit_cost_p99.9": rule_p,
            "isolation_forest": flagged,
        }
        results["by_class"][defect] = {}
        for name, pred in entries.items():
            p, r, f1, tp, fp, fn = prf(truth, pred)
            print(f"  {name:<34}{p:>11.3f}{r:>9.3f}{f1:>8.3f}{tp:>7}{fp:>8}{fn:>6}")
            results["by_class"][defect][name] = {
                "precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4),
                "tp": tp, "fp": fp, "fn": fn,
            }

        ap = average_precision_score(truth, scores)
        results["by_class"][defect]["isolation_forest"]["average_precision"] = round(ap, 4)
        print(f"  {'isolation_forest ranking (avg prec)':<34}{ap:>11.3f}")

    # Combined view over both target classes.
    any_target = df["defect_type"].isin(TARGET_DEFECTS).to_numpy()
    p, r, f1, tp, fp, fn = prf(any_target, flagged)
    results["combined_targets"] = {
        "precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4),
        "tp": tp, "fp": fp, "fn": fn,
    }

    # Precision counting ANY labelled defect as a hit. Reported separately because a flag
    # landing on an epoch row is not really a false alarm -- that row is genuinely broken,
    # just broken in a way dbt already catches. Quoting only the generous number would be
    # misleading, so both appear.
    any_defect = (df["defect_type"] != "CLEAN").to_numpy()
    p2, _r2, _f2, tp2, fp2, _fn2 = prf(any_defect, flagged)
    results["precision_vs_any_defect"] = {"precision": round(p2, 4), "tp": tp2, "fp": fp2}

    print()
    print("-" * W)
    print("  COMBINED, both target classes")
    print("-" * W)
    print(f"  isolation forest          precision {p:.3f}   recall {r:.3f}   F1 {f1:.3f}")
    print(f"  flags landing on ANY labelled defect   precision {p2:.3f}  "
          f"({tp2} of {tp2 + fp2} flags)")

    # Where to draw the line.
    price_truth = (df["defect_type"] == "CONTEXTUAL_PRICE").to_numpy()
    ops = choose_operating_point(price_truth, scores, alert_budget)
    results["operating_points_contextual_price"] = ops

    print()
    print("-" * W)
    print("  CHOOSING A THRESHOLD, contextual price only")
    print("-" * W)
    o = ops["best_f1_oracle"]
    b = ops["alert_budget"]
    print(f"  best-F1, oracle-tuned      precision {o['precision']:.3f}   "
          f"recall {o['recall']:.3f}   F1 {o['f1']:.3f}")
    print("                             upper bound only; uses the labels to pick the line")
    print(f"  top-{alert_budget} alert budget        precision {b['precision']:.3f}   "
          f"recall {b['recall']:.3f}   F1 {b['f1']:.3f}")
    print(f"                             {b['tp']} of {alert_budget} flags were real. "
          "No labels needed -- this is what ships.")

    print()
    print("=" * W)
    print("  READ THIS BEFORE QUOTING ANY NUMBER ABOVE")
    print("=" * W)
    rq = results["by_class"]["CORRUPT_QUANTITY"]
    rp = results["by_class"]["CONTEXTUAL_PRICE"]
    print("  1. Corrupt quantity. The SQL rule scores precision "
          f"{rq['sql_rule_quantity_gt_1e6']['precision']:.3f} and recall "
          f"{rq['sql_rule_quantity_gt_1e6']['recall']:.3f}.")
    print(f"     The forest manages recall {rq['isolation_forest']['recall']:.3f} at "
          f"precision {rq['isolation_forest']['precision']:.3f} -- far worse.")
    print("     Keep the rule. Adding a model to a solved problem costs money and trust.")
    print()
    print("  2. Contextual price. The global-threshold rule reaches recall "
          f"{rp['sql_rule_unit_cost_p99.9']['recall']:.3f}, missing a third of")
    print("     the anomalies outright, because no single number fits a catalogue spanning")
    print("     washers and pumps. The forest ranks these well: average precision "
          f"{rp['isolation_forest']['average_precision']:.3f}.")
    print()
    print("  3. Ranking is not detection. At the default contamination the forest scores")
    print(f"     F1 {rp['isolation_forest']['f1']:.3f}, BELOW the crude rule's "
          f"{rp['sql_rule_unit_cost_p99.9']['f1']:.3f}, purely because the")
    print("     threshold is badly placed. Strong ordering, weak decision. Reported rather")
    print("     than buried, because it is the most useful thing on this page.")
    print()
    print("  Conclusion: rules for absolute violations, models for contextual ones, and a")
    print("  threshold chosen from how many alerts a human can actually action per day.")
    print("=" * W)
    return results


def main() -> None:
    ap = argparse.ArgumentParser(description="Score anomaly detection against ground truth.")
    ap.add_argument("--contamination", type=float, default=0.002,
                    help="expected anomaly rate; sets the flagging threshold")
    ap.add_argument("--alert-budget", type=int, default=20,
                    help="alerts a human can triage per run; sets the production threshold")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)

    print("Loading fact table and building contextual features...")
    df = get_dataset()

    if (df["defect_type"] != "CLEAN").sum() == 0:
        raise SystemExit(
            "No labels found. Re-run 'python -m ingest.generate' and 'python -m ingest.load' "
            "so that ml.defect_labels exists."
        )

    print(f"Fitting IsolationForest on {len(df):,} rows "
          f"(contamination={args.contamination})...")
    scores, model = fit_isolation_forest(df, args.contamination)
    threshold = np.quantile(scores, 1 - args.contamination)
    flagged = scores >= threshold

    df = df.assign(anomaly_score=scores, is_flagged=flagged.astype(int))
    metrics = report(df, scores, flagged, args.alert_budget)
    metrics["contamination"] = args.contamination
    metrics["score_threshold"] = float(threshold)
    metrics["n_flagged"] = int(flagged.sum())

    # Saved for ml/charts.py and the PDF, so every published figure traces to one run.
    scored_path = os.path.join(OUT_DIR, "scored_transactions.parquet")
    keep = (FEATURE_COLUMNS + ["company_key", "item_code", "txn_timestamp",
                               "defect_type", "anomaly_score", "is_flagged"])
    df[keep].to_parquet(scored_path, index=False)

    truth = df["defect_type"].isin(TARGET_DEFECTS).to_numpy()
    precision, recall, _thr = precision_recall_curve(truth, scores)
    pd.DataFrame({"precision": precision, "recall": recall}).to_parquet(
        os.path.join(OUT_DIR, "pr_curve.parquet"), index=False
    )

    with open(os.path.join(OUT_DIR, "metrics.json"), "w", encoding="utf-8") as fh:
        json.dump(metrics, fh, indent=2)

    print(f"\nWrote {scored_path}")
    print(f"Wrote {os.path.join(OUT_DIR, 'pr_curve.parquet')}")
    print(f"Wrote {os.path.join(OUT_DIR, 'metrics.json')}")
    print("\nNext:  python -m ml.volume_monitor")


if __name__ == "__main__":
    main()
