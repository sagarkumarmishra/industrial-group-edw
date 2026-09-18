{{ config(materialized = "view") }}

/*
    Work orders -- staging.

    TRAP 5, EPOCH AS NULL. Open orders carry 1970-01-01 in completion_timestamp rather
    than NULL. Left alone it becomes a date key of 19700101 that joins happily to the
    date dimension and drags fifty years of empty history into every trend.

    DELIBERATE DATA QUALITY PROBLEM. A small share of orders have a completion date
    BEFORE the order date, which produces a negative lead time. Real ERP data does this
    -- back-dated closures, corrections keyed against the wrong order. A naive DATEDIFF
    reports it without complaint.

    The choice made here is to CALCULATE the lead time but also FLAG it, rather than
    filtering the rows out. Filtering hides a real data quality problem from the business
    and makes the row count disagree with source for reasons nobody can later explain.
*/

with source as (

    select * from {{ source("raw", "work_orders") }}

),

companies as (

    select company_key, stores_utc from {{ ref("stg_companies") }}

),

joined as (

    select s.*, c.stores_utc
    from source s
    left join companies c
        on cast(s.company_code as integer) = c.company_key

),

converted as (

    select
        cast(company_code as integer)                                   as company_key,
        {{ clean("order_no") }}                                         as order_no,

        {{ null_if_epoch(to_reporting_time("order_timestamp", "stores_utc")) }}
                                                                        as ordered_at,
        {{ null_if_epoch(to_reporting_time("completion_timestamp", "stores_utc")) }}
                                                                        as completed_at,

        cast(order_status as integer)                                   as order_status,
        cast(service_module_key as integer)                             as service_module_key,
        {{ clean("customer_code") }}                                    as customer_code,
        cast(estimated_value as double)                                 as estimated_value,
        cast(is_deleted as integer)                                     as is_deleted

    from joined

),

flagged as (

    select
        *,
        -- Both dates present, so a duration can be calculated at all.
        case when ordered_at is not null and completed_at is not null then 1 else 0 end
                                                                        as is_closed,

        case
            when ordered_at is null or completed_at is null then null
            else date_diff('day', cast(ordered_at as date), cast(completed_at as date))
        end                                                             as lead_time_days
    from converted

)

select
    *,
    -- Negative lead time is not meaningful. Flagged, not filtered, so the business can
    -- see how much of it there is and decide what to do about it.
    case
        when lead_time_days is null then 0
        when lead_time_days < 0 then 0
        else 1
    end                                                                 as is_lead_time_plausible
from flagged
