/*
    FACT: work order lead time.

    ====================================================================================
    GRAIN
    One row per work order.
    ====================================================================================

    Answers "how long do jobs actually take", per subsidiary and per service module.

    WHAT THIS FACT DEMONSTRATES: FLAG, DO NOT FILTER.

    Roughly 1% of source orders have a completion date BEFORE the order date, producing a
    negative lead time. Real ERP data does this -- back-dated closures, corrections keyed
    against the wrong order, migration artefacts.

    There are three ways to handle it and only one is right:

      WRONG    Ignore it. AVG(lead_time_days) is then dragged down by impossible values
               and nobody knows why the average looks low.

      WRONG    Filter the rows out. The fact then disagrees with source on row count, for
               a reason that is invisible six months later, and the business never learns
               it has a data entry problem.

      RIGHT    Calculate it, flag it, and expose a pre-filtered measure alongside. The
               business can see how much of it there is, reporting can exclude it
               deliberately, and the row count still reconciles to source.

    Open orders are also excluded from lead time but retained as rows. An order with no
    completion date has no lead time -- that is not missing data, it is an open job, and
    the backlog is a legitimate thing to want to count.
*/

{{ config(materialized = 'table') }}

with orders as (

    select * from {{ ref('stg_work_orders') }}

),

dim_service_module as (
    select service_module_key from {{ ref('dim_service_module') }}
),

resolved as (

    select
        {{ surrogate_key(['o.company_key', 'o.order_no']) }}     as work_order_key,

        -- Dimension keys -----------------------------------------------------------
        o.company_key,
        {{ resolve_key('m.service_module_key') }}               as service_module_key,
        {{ date_key('o.ordered_at') }}                          as ordered_date_key,
        {{ date_key('o.completed_at') }}                        as completed_date_key,

        -- Degenerate dimensions ----------------------------------------------------
        o.order_no,
        o.customer_code,
        o.order_status,
        o.ordered_at,
        o.completed_at,

        -- Measures -----------------------------------------------------------------
        o.estimated_value,

        -- As calculated, including implausible values. Kept so the fact reconciles and
        -- so the problem stays visible.
        o.lead_time_days,

        -- Pre-filtered measure for reporting. Null where not plausible, so an AVG over
        -- this column ignores the bad rows automatically rather than silently including
        -- them or requiring every consumer to remember the filter.
        case
            when o.is_lead_time_plausible = 1 then o.lead_time_days
            else null
        end                                                     as lead_time_days_clean,

        -- Flags ---------------------------------------------------------------------
        o.is_closed,
        o.is_lead_time_plausible,
        o.is_deleted,

        -- Open orders are backlog, not missing data.
        case when o.completed_at is null then 1 else 0 end       as is_open

    from orders o
    left join dim_service_module m
        on o.service_module_key = m.service_module_key

)

select * from resolved
