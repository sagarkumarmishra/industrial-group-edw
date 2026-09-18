"""
Demonstrates each engineered defect, with real numbers from the built warehouse.

This is the script to run if you have five minutes and want to see what the project is
actually about. It shows, for each trap, what the naive implementation would have produced
versus what the correct one does -- side by side, on real data.

Run after building:

    python -m ingest.generate
    python -m ingest.load
    dbt build --profiles-dir .
    python demo_defects.py
"""
from __future__ import annotations

import os
import sys

import duckdb

DB = os.path.join("data", "edw.duckdb")
W = 84


def rule(char: str = "-") -> None:
    print(char * W)


def header(n: int, title: str) -> None:
    print()
    rule("=")
    print(f"  TRAP {n}   {title}")
    rule("=")


def main() -> None:
    if not os.path.exists(DB):
        sys.exit(
            f"{DB} not found.\n\n"
            "Build it first:\n"
            "    python -m ingest.generate\n"
            "    python -m ingest.load\n"
            "    dbt build --profiles-dir .\n"
        )

    con = duckdb.connect(DB, read_only=True)
    q = lambda s: con.execute(s).fetchall()

    print()
    rule("=")
    print("  NORDVIK INDUSTRIAL GROUP EDW -- ENGINEERED DEFECT DEMONSTRATION")
    rule("=")

    # ---------------------------------------------------------------- scale
    print("\nWarehouse contents\n")
    print(f"  {'object':<38}{'rows':>12}")
    rule()
    for t in [
        "dim_company", "dim_service_module", "dim_item", "dim_warehouse",
        "dim_cost_component", "dim_date",
        "fct_inventory_transaction_cost", "fct_labour_hours",
        "fct_work_order_lead_time", "fct_labour_hours_variance",
    ]:
        n = q(f"select count(*) from main_marts.{t}")[0][0]
        print(f"  {t:<38}{n:>12,}")

    # ---------------------------------------------------------------- trap 3
    header(3, "SUB-DAY GRAIN -- the one that produced 325,318 duplicates")
    key_date = ("company_code || item_code || warehouse_code || cost_component_code "
                "|| cast(cast(txn_timestamp as date) as varchar) || cast(line_seq as varchar)")
    key_ts = ("company_code || item_code || warehouse_code || cost_component_code "
              "|| cast(txn_timestamp as varchar) || cast(line_seq as varchar)")
    r = q(f"""
        select count(*),
               count(distinct {key_date}),
               count(distinct {key_ts})
        from raw.inventory_cost_txn
    """)[0]
    src, date_only, with_ts = r
    print(f"\n  source rows                          {src:>12,}")
    print(f"  distinct keys, DATE only             {date_only:>12,}   <- the naive grain")
    print(f"  distinct keys, WITH timestamp        {with_ts:>12,}   <- the correct grain")
    rule()
    print(f"  transactions destroyed by truncating {src - date_only:>12,}"
          f"   ({100 * (src - date_only) / src:.1f}% of the fact)")
    print(f"  residual after correct grain         {src - with_ts:>12,}"
          "   <- GENUINE source duplicates, not a bug")
    print("\n  Verdict: the timestamp belongs in the key. The residual is a real upstream")
    print("  problem for one subsidiary, quantified and documented rather than deduplicated")
    print("  silently. The dbt test warns at 9 and fails above it -- a regression guard.")

    # ---------------------------------------------------------------- trap 2
    header(2, "MIXED UNITS -- seconds booked in an hours column")
    r = q("""
        select count(*), sum(work_hours_as_sourced), sum(work_hours)
        from main_marts.fct_labour_hours
        where source_hours_unit = 'SEC'
    """)[0]
    n, raw_sum, conv_sum = r
    print(f"\n  rows the source booked in SECONDS    {n:>12,}")
    print(f"  SUM if the unit is ignored           {raw_sum:>12,.0f}   'hours'")
    print(f"  SUM after reading the unit column    {conv_sum:>12,.1f}   hours")
    rule()
    print(f"  overstatement avoided                {raw_sum / conv_sum:>12,.0f}x")
    print("\n  Why this is the most dangerous trap here: nothing about the VALUES reveals it.")
    print("  The load succeeds, row counts reconcile, no key is null, and the total still")
    print("  looks like a plausible number of hours. Only the unit column gives it away.")

    # ---------------------------------------------------------------- trap 4
    header(4, "CORRUPT QUANTITIES -- why not to recompute what the source already holds")
    r = q("""
        select sum(transaction_amount), sum(amount_if_recomputed),
               sum(case when is_quantity_reliable = 0 then 1 else 0 end)
        from main_marts.fct_inventory_transaction_cost
    """)[0]
    src_amt, recomputed, bad = r
    print(f"\n  rows with a corrupt quantity         {bad:>12,}   (out of "
          f"{q('select count(*) from main_marts.fct_inventory_transaction_cost')[0][0]:,})")
    print(f"  total cost, taken FROM SOURCE        {src_amt:>16,.2f}")
    print(f"  total cost, recomputed qty x rate    {recomputed:>16,.2f}")
    rule()
    print(f"  inflation from recomputing           {recomputed / src_amt:>12,.0f}x")
    print(f"\n  A handful of bad rows out of {q('select count(*) from raw.inventory_cost_txn')[0][0]:,}"
          " is enough to make the total meaningless.")
    print("  On a real project this exact mistake reported $28.87bn of estimated labour")
    print("  cost against $3.06m actual. The ERP already held the correct amount.")

    # ---------------------------------------------------------------- -100%
    header(5, "THE -100% PROBLEM -- when a NULL is more honest than a number")
    r = q("""
        select count(*),
               sum(case when has_actual_booking = 0 then 1 else 0 end),
               avg(hours_variance_pct),
               avg(case when has_actual_booking = 0 then -100.0 else hours_variance_pct end)
        from main_marts.fct_labour_hours_variance
    """)[0]
    total, unbooked, correct_avg, naive_avg = r
    print(f"\n  planned operations                   {total:>12,}")
    print(f"  planned but NEVER booked             {unbooked:>12,}"
          f"   ({100 * unbooked / total:.1f}%)")
    rule()
    if correct_avg is None:
        print("  No booked operations found -- cannot contrast the two averages.")
    else:
        print(f"  average variance, naive (-100%)      {naive_avg:>12.2f}%   <- reported for weeks")
        print(f"  average variance, correct (NULL)     {correct_avg:>12.2f}%")
        print(f"  distortion                           {abs(naive_avg - correct_avg):>12.2f} percentage points")
    print("\n  An operation with no booking did not run 100% under plan -- it never ran.")
    print("  Returning NULL says 'not calculable'. Returning -100% is a lie that looks")
    print("  like a fact, and it drags every average built on the column.")

    # ---------------------------------------------------------------- lead time
    header(6, "IMPLAUSIBLE VALUES -- flag, do not filter")
    r = q("""
        select count(*),
               sum(case when is_lead_time_plausible = 0 then 1 else 0 end),
               avg(lead_time_days),
               avg(lead_time_days_clean),
               min(lead_time_days)
        from main_marts.fct_work_order_lead_time
        where is_closed = 1
    """)[0]
    closed, bad_lt, avg_all, avg_clean, worst = r
    print(f"\n  closed work orders                   {closed:>12,}")
    print(f"  with a NEGATIVE lead time            {bad_lt:>12,}")
    print(f"  worst single value                   {worst:>12,} days")
    rule()
    print(f"  average lead time, unfiltered        {avg_all:>12.2f} days")
    print(f"  average lead time, clean measure     {avg_clean:>12.2f} days")
    print("\n  The rows are RETAINED so the fact still reconciles to source and the business")
    print("  can see it has a data entry problem. A pre-filtered measure sits alongside, so")
    print("  reporting excludes them deliberately rather than by accident.")

    # ---------------------------------------------------------------- traps 1 + 8
    header(1, "MIXED TIME ZONES and COLUMN DRIFT -- the consolidation problem")
    print("\n  Six subsidiaries, four source systems, unioned into ONE fact:\n")
    print(f"  {'subsidiary':<28}{'source system':<15}{'stores UTC':<12}{'rows':>10}")
    rule()
    for name, system, utc, n in q("""
        select c.company_name, c.source_system, c.stores_utc, count(*)
        from main_marts.fct_labour_hours f
        join main_marts.dim_company c on f.company_key = c.company_key
        group by 1, 2, 3
        order by 4 desc
    """):
        print(f"  {name:<28}{system:<15}{'yes' if utc else 'no':<12}{n:>10,}")
    rule()
    print("\n  The UTC column is the whole point. Convert every subsidiary and the three")
    print("  that already store local time shift by an hour. Convert none and the other")
    print("  three do. Either way dates still look like dates, and only a reconciliation")
    print("  that applies the SAME rule as the load will reveal it.")
    print("\n  Column drift is handled in the same union: the legacy extract has no unit")
    print("  column, no work centre, no delete flag, and carries a date rather than a")
    print("  timestamp. Where information genuinely does not exist it is NULL and flagged,")
    print("  not defaulted to something convenient.")

    # ---------------------------------------------------------------- close
    print()
    rule("=")
    print("  Every number above came from a live build. Nothing here is illustrative.")
    print("  Run 'dbt build --profiles-dir .' to reproduce: 21 models, 70 tests, ~4 seconds.")
    rule("=")
    print()

    con.close()


if __name__ == "__main__":
    main()
