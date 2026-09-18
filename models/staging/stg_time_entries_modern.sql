{{ config(materialized = "view") }}

/*
    Time entries from the MODERN_ERP subsidiaries -- staging.

    TRAP 2, MIXED UNITS. This is the most dangerous defect in the project, because
    nothing about the data reveals it. work_hours is booked in SECONDS on a minority of
    manufacturing rows, and the unit lives in a separate hours_unit column. A SUM() over
    the raw column still returns a number that looks like a plausible total of hours.
    Miss the conversion and affected manufacturing hours inflate by a factor of 3,600 --
    and every utilisation percentage built on it is confidently, invisibly wrong.

    The lesson generalises: whenever a source carries a unit-of-measure column, the
    values in the measure column cannot be trusted on their own.

    TRAP 1, TIMEZONE. These subsidiaries store UTC, so the conversion applies.
*/

with source as (

    select * from {{ source("raw", "time_entries_modern") }}

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

normalised as (

    select
        cast(company_code as integer)                                   as company_key,
        {{ clean("employee_code") }}                                    as employee_code,

        {{ null_if_epoch(to_reporting_time("booking_timestamp", "stores_utc")) }}
                                                                        as booked_at,

        cast(service_module_key as integer)                             as service_module_key,
        {{ clean("order_no") }}                                         as order_no,
        cast(operation_seq as integer)                                  as operation_seq,
        {{ clean("work_centre_code") }}                                 as work_centre_code,

        -- TRAP 2 handled. Read the unit, then convert.
        case
            when upper({{ clean("hours_unit") }}) = 'SEC'
                then cast(work_hours as double) / 3600.0
            else cast(work_hours as double)
        end                                                             as work_hours,

        -- Kept so the conversion is auditable rather than invisible. A reviewer can
        -- confirm the rule fired on exactly the rows it should have.
        upper({{ clean("hours_unit") }})                                as source_hours_unit,
        cast(work_hours as double)                                      as work_hours_as_sourced,

        cast(hourly_rate as double)                                     as hourly_rate,
        cast(is_deleted as integer)                                     as is_deleted,
        'MODERN_ERP'                                                    as source_system

    from joined

)

select *
from normalised
-- Zero and null bookings carry no information and would distort any average.
where work_hours is not null
  and work_hours > 0
