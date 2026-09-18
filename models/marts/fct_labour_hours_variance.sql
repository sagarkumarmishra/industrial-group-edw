/*
    FACT: labour hours variance -- planned versus actual, per production operation.

    ====================================================================================
    GRAIN
    One row per company, production order and operation sequence.
    ====================================================================================

    WHAT THIS FACT DEMONSTRATES: THE -100% PROBLEM.

    This is a subtle defect that is very easy to ship and very hard to spot afterwards.

    Planned operations are LEFT joined to actual bookings, because an operation can be
    planned and never booked. When that happens, actual_hours is 0 and the obvious
    variance formula:

        (actual - planned) / planned  =  (0 - 40) / 40  =  -100%

    returns exactly -100%. That is arithmetically correct and analytically meaningless. It
    does not mean the operation ran 100% under plan; it means it never ran at all, or the
    booking never made it into the system.

    On a real project 31,346 rows reported exactly -100%. Averaging that column made
    manufacturing performance look dramatically better than it was, and the number had
    been reported for weeks before anyone questioned it.

    THE FIX, and it is the general principle for all derived ratios:

      - return NULL, not a number, when the denominator case is not meaningful
      - expose an explicit has_actual_booking flag so the absence is visible as data
      - expose is_variance_reliable so a consumer can filter deliberately

    A NULL is honest: it says "not calculable". A -100% is a lie that looks like a fact.

    Note also that planned cost uses the source's planned_amount rather than
    planned_hours * planned_rate -- the same principle as the inventory fact. If the
    source already holds a computed amount, prefer it.
*/

{{ config(materialized = 'table') }}

with planned as (

    select * from {{ ref('stg_production_operations') }}

),

actual as (

    -- Actual bookings aggregated to the OPERATION grain BEFORE the join.
    --
    -- Two things matter here. First, aggregating before joining is what prevents the join
    -- fanning out and double counting planned hours -- a classic and expensive mistake.
    -- Second, the aggregation is at (company, order, operation), matching the planned
    -- grain exactly. Aggregating to (company, order) alone would attach the whole order's
    -- actual hours to EVERY operation on it, silently inflating actuals several times over
    -- while every row count and key test still passed.
    select
        company_key,
        order_no                        as production_order,
        operation_seq,
        sum(work_hours)                 as actual_hours,
        sum(labour_cost)                as actual_amount,
        count(*)                        as booking_count
    from {{ ref('fct_labour_hours') }}
    where service_module_key = 3            -- MANUFACTURING only
      and is_deleted = 0
      and operation_seq is not null
    group by 1, 2, 3

),

joined as (

    select
        {{ surrogate_key([
            'p.company_key', 'p.production_order', 'p.operation_seq'
        ]) }}                                               as labour_variance_key,

        p.company_key,
        {{ date_key('p.ordered_at') }}                       as ordered_date_key,

        p.production_order,
        p.operation_seq,
        p.work_centre_code,
        p.operation_status,

        -- Planned -------------------------------------------------------------------
        p.planned_hours,
        p.planned_amount,

        -- Actual. Coalesced to 0 for the absolute variance, which is legitimate: zero
        -- hours were genuinely booked. The percentage is where care is needed.
        coalesce(a.actual_hours, 0)                          as actual_hours,
        coalesce(a.actual_amount, 0)                         as actual_amount,
        coalesce(a.booking_count, 0)                         as booking_count,

        -- The flag that makes the whole thing interpretable.
        case when a.actual_hours is null then 0 else 1 end   as has_actual_booking

    from planned p
    left join actual a
        on p.company_key = a.company_key
       and p.production_order = a.production_order
       and p.operation_seq = a.operation_seq

),

variance as (

    select
        *,

        -- Absolute variance is meaningful even with no booking: the full planned hours
        -- were not consumed, and that is a real number.
        round(actual_hours - planned_hours, 4)               as hours_variance,
        round(actual_amount - planned_amount, 2)             as amount_variance,

        -- THE IMPORTANT PART. Null rather than -100% when nothing was booked.
        case
            when has_actual_booking = 0 then null
            else round(
                {{ safe_divide('actual_hours - planned_hours', 'planned_hours') }} * 100.0,
            4)
        end                                                  as hours_variance_pct,

        case
            when has_actual_booking = 0 then null
            else round(
                {{ safe_divide('actual_amount - planned_amount', 'planned_amount') }} * 100.0,
            4)
        end                                                  as amount_variance_pct

    from joined

)

select
    *,
    -- A single flag consumers can filter on, rather than each of them reimplementing
    -- the reliability rule slightly differently.
    case
        when has_actual_booking = 1 and planned_hours > 0 then 1
        else 0
    end                                                      as is_variance_reliable
from variance
