/*
    FACT: inventory transaction cost.

    ====================================================================================
    GRAIN
    One row per company, item, warehouse, cost component, TRANSACTION TIMESTAMP and line
    sequence.
    ====================================================================================

    The timestamp being IN THE GRAIN is the whole point of this model, and it is the
    single most instructive thing in this repository.

    THE MISTAKE THIS MODEL EXISTS TO DEMONSTRATE
    The obvious design is to key on the transaction DATE. Nobody reports inventory at
    second granularity, a date key is what joins to the date dimension, and carrying a
    timestamp in a fact key feels wrong. So you truncate.

    That is wrong here, because of two source characteristics that only show up when you
    test for them:
      1. txn_timestamp is unique to the SECOND, not the day.
      2. line_seq does NOT restart per day -- it is overwhelmingly 1.

    Together those mean that two genuine receipts of the same item, into the same
    warehouse, against the same cost component, on the same day, are INDISTINGUISHABLE
    once the timestamp is truncated. They collapse onto one key.

    On a real project this produced 325,318 duplicate rows in a 3.6 million row fact, and
    it was found by QA rather than by me. The fix was not to deduplicate -- the rows were
    not duplicates, they were distinct transactions -- but to correct the grain.

    HOW TO PROVE THE GRAIN IS RIGHT, BEFORE WRITING THE FACT
    Count distinct on the candidate key in the SOURCE, with and without the timestamp,
    and compare both to the row count. It is one query and it takes a minute:

        select
            count(*)                                             as source_rows,
            count(distinct (company, item, warehouse, comp,
                            date(ts), seq))                      as grain_date_only,
            count(distinct (company, item, warehouse, comp,
                            ts, seq))                            as grain_with_timestamp
        from source

    If grain_with_timestamp matches source_rows and grain_date_only does not, the
    timestamp belongs in the key. See analyses/prove_the_grain.sql to run it here.

    ------------------------------------------------------------------------------------
    OTHER DECISIONS WORTH NOTING

    COST comes from total_amount as recorded by the source, NOT from quantity * unit_cost.
    A small number of source rows carry corrupt quantities, and recomputing inherits the
    defect. On a real project that exact mistake reported $28.87bn of estimated labour cost
    against $3.06m of actual, because a handful of rows had quantities in the hundreds of
    millions. The ERP already held the correct amount.

    DIMENSION JOINS are all LEFT joins resolving to the unknown member. An unresolved
    dimension entry must never cause a real transaction to disappear from the fact.

    DELETED ROWS are retained, carrying is_deleted, so history stays auditable. Filtering
    them here would make the fact disagree with source for a reason nobody can later find.
*/

{{ config(
    materialized = 'table',
    unique_key = ['company_key', 'item_key', 'warehouse_key',
                  'cost_component_key', 'txn_timestamp', 'line_seq']
) }}

with txn as (

    select * from {{ ref('stg_inventory_cost_txn') }}

),

dim_item as (
    select item_key, item_code from {{ ref('dim_item') }}
),

dim_warehouse as (
    select warehouse_key, company_key, warehouse_code from {{ ref('dim_warehouse') }}
),

dim_cost_component as (
    select cost_component_key, cost_component_code from {{ ref('dim_cost_component') }}
),

resolved as (

    select
        -- Grain columns -------------------------------------------------------------
        t.company_key,
        {{ resolve_key('i.item_key') }}                     as item_key,
        {{ resolve_key('w.warehouse_key') }}                as warehouse_key,
        {{ resolve_key('c.cost_component_key') }}           as cost_component_key,

        -- PART OF THE GRAIN. Not decorative. See the header comment.
        t.txn_timestamp,
        t.line_seq,

        -- Date dimension foreign key. Derived from the same converted timestamp as the
        -- grain column, so the two can never disagree.
        {{ date_key('t.txn_timestamp') }}                   as txn_date_key,

        -- Degenerate dimensions: kept on the fact for traceability back to source.
        t.item_code                                         as source_item_code,
        t.warehouse_code                                    as source_warehouse_code,
        t.cost_component_code                               as source_cost_component_code,

        -- Measures ------------------------------------------------------------------
        t.quantity,
        t.unit_cost,

        -- Taken from source, NOT recomputed. See header.
        t.total_amount                                      as transaction_amount,

        -- Exposed so a consumer can see what recomputation would have produced, and why
        -- it is not used. This is the $28.87bn column.
        round(t.quantity * t.unit_cost, 2)                  as amount_if_recomputed,

        -- Flags ---------------------------------------------------------------------
        t.is_quantity_reliable,
        t.is_wip,
        t.is_deleted,

        -- Explicit, so a consumer never has to guess whether a null key is a real
        -- unresolved reference or a join that silently failed.
        case when i.item_key is null then 1 else 0 end       as is_item_unresolved,
        case when w.warehouse_key is null then 1 else 0 end  as is_warehouse_unresolved

    from txn t
    left join dim_item i
        on t.item_code = i.item_code
    left join dim_warehouse w
        on t.company_key = w.company_key
       and t.warehouse_code = w.warehouse_code
    left join dim_cost_component c
        on t.cost_component_code = c.cost_component_code

)

select * from resolved
