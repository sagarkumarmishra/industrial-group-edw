-- Reconciliation: the fact must not lose or invent rows against its source.
--
-- This is the first test to write on any fact and the one most often skipped. A row count
-- that silently drifts from source is how a warehouse loses trust, and once trust is gone
-- no amount of correct modelling wins it back.
--
-- Returns a row (and therefore fails) if the counts differ by even one.

with fact_count as (
    select count(*) as n from {{ ref("fct_inventory_transaction_cost") }}
),
source_count as (
    select count(*) as n from {{ source("raw", "inventory_cost_txn") }}
)
select
    f.n as fact_rows,
    s.n as source_rows,
    f.n - s.n as difference
from fact_count f
cross join source_count s
where f.n <> s.n
