{{ config(materialized = "view") }}

/*
    Time entries from the LEGACY_ACCT and WORKSHOP_SYS subsidiaries -- staging.

    TRAP 8, COLUMN DRIFT. This is the part of multi-source consolidation that consumes the
    most time and gets the least credit. This extract is not a variant of the modern one,
    it is a different shape entirely:

      - columns are named differently          co, emp_id, hours_worked, rate, job_ref
      - there is NO hours_unit column          hours are always hours here
      - there is NO work_centre_code           these subsidiaries do not manufacture
      - there is NO is_deleted column          so it is defaulted, and that default is a
                                               documented assumption, not a fact
      - it carries a DATE, not a timestamp     so there is no time-of-day at all
      - the module is a text job_type          not the numeric key the modern ERP uses

    TRAP 1, TIMEZONE. These systems store LOCAL time already, so no conversion is applied.
    This is exactly the case that breaks if you apply the conversion globally.

    The job of this model is to make this source look like the modern one WITHOUT
    pretending it has information it does not have. Where a column genuinely does not
    exist, it is null and the reason is stated -- not silently defaulted to something
    convenient.
*/

with source as (

    select * from {{ source("raw", "time_entries_legacy") }}

),

normalised as (

    select
        cast(co as integer)                                             as company_key,
        {{ clean("emp_id") }}                                           as employee_code,

        -- A date, not a timestamp. Cast so the union has one consistent type; the
        -- absence of time-of-day is a real limitation of this source, not a defect.
        cast(cast(work_date as date) as timestamp)                      as booked_at,

        -- Map the text job type onto the same service module keys the modern ERP uses.
        -- This mapping is the whole point of the common-schema layer: without it,
        -- 'WORKSHOP' and DEPOT_REPAIR would never be recognised as the same thing.
        case upper({{ clean("job_type") }})
            when 'SERVICE'  then 1      -- FIELD_SERVICE
            when 'WORKSHOP' then 2      -- DEPOT_REPAIR
            when 'LEAVE'    then 4      -- GENERAL_LEAVE
            else {{ var("unknown_key") }}
        end                                                             as service_module_key,

        {{ clean("job_ref") }}                                          as order_no,

        -- No operation sequence in this source: these subsidiaries do not manufacture,
        -- so there are no production operations to book against.
        cast(null as integer)                                           as operation_seq,

        -- Genuinely absent from this source. Null rather than a fabricated value.
        cast(null as varchar)                                           as work_centre_code,

        cast(hours_worked as double)                                    as work_hours,

        -- No unit column exists. Recorded as an explicit assumption so the difference
        -- between "known to be hours" and "assumed to be hours" stays visible.
        'HR (assumed, no unit column in source)'                        as source_hours_unit,
        cast(hours_worked as double)                                    as work_hours_as_sourced,

        cast(rate as double)                                            as hourly_rate,

        -- No delete flag in this extract. Defaulted to not-deleted, which is the
        -- documented assumption -- this source only ever sends live rows.
        0                                                               as is_deleted,
        'LEGACY'                                                        as source_system

    from source

)

select *
from normalised
where work_hours is not null
  and work_hours > 0
