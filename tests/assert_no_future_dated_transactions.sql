-- Timezone conversion must be applied to UTC-storing subsidiaries and NOT to the others.
--
-- This is the test that catches the mistake I actually made on a real project: running a
-- reconciliation WITHOUT the same timezone rule as the load, and reporting roughly 480,000
-- false mismatches per company. The data was fine; the test was wrong.
--
-- The assertion here is deliberately indirect, because a converted timestamp cannot be
-- compared to its own source without repeating the conversion. Instead it checks a property
-- that must hold either way: no transaction may be dated in the future, which is what a
-- badly applied or doubly applied conversion tends to produce.

select
    f.company_key,
    c.company_name,
    c.stores_utc,
    count(*)                    as future_dated_rows,
    max(f.txn_timestamp)        as latest_timestamp
from {{ ref("fct_inventory_transaction_cost") }} f
join {{ ref("dim_company") }} c
    on f.company_key = c.company_key
where f.txn_timestamp > current_localtimestamp() + interval 1 day
group by 1, 2, 3
