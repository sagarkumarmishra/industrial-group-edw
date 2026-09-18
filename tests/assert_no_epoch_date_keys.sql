-- The epoch must never reach a date key.
--
-- Source systems write 1970-01-01 to mean "no date". If a date key of 19700101 appears in
-- any fact, an upstream guard was missed -- and the symptom is subtle: fifty years of
-- empty history quietly attached to every trend chart.

select 'fct_inventory_transaction_cost' as model, txn_date_key as bad_date_key
from {{ ref("fct_inventory_transaction_cost") }}
where txn_date_key = 19700101

union all
select 'fct_labour_hours', booked_date_key
from {{ ref("fct_labour_hours") }}
where booked_date_key = 19700101

union all
select 'fct_work_order_lead_time (ordered)', ordered_date_key
from {{ ref("fct_work_order_lead_time") }}
where ordered_date_key = 19700101

union all
select 'fct_work_order_lead_time (completed)', completed_date_key
from {{ ref("fct_work_order_lead_time") }}
where completed_date_key = 19700101
