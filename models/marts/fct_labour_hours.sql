/*
    FACT: labour hours.

    ====================================================================================
    GRAIN
    One row per time-entry booking, across every subsidiary and every source system.
    ====================================================================================

    Built on the common-schema union rather than on the source extracts, which is what
    keeps this model readable. It has no idea that six subsidiaries and four source systems
    exist -- that complexity is resolved one layer down, on purpose.

    WHAT THIS FACT DEMONSTRATES

    1. THE UNIT-OF-MEASURE TRAP, already handled upstream. work_hours here is always
       genuinely hours. source_hours_unit is carried through so the conversion stays
       auditable: a reviewer can confirm the rule fired on exactly the rows it should.
       Had it been missed, affected manufacturing hours would be 3,600x too large, and
       every utilisation percentage built on this fact would be wrong while looking fine.

    2. SERVICE MODULE AS A REAL FOREIGN KEY, not a text column. Six operationally
       different activities share these tables. Any query that does not group by or filter
       on service_module_key is at risk of mixing field service with workshop repair and
       producing a plausible, meaningless number.

    3. HONEST HANDLING OF SOURCE LIMITATIONS. has_time_of_day is 0 for the legacy
       subsidiaries because those extracts carry a date only. That is a real limitation,
       flagged rather than papered over, so nobody attempts shift-level analysis on rows
       that cannot support it.
*/

{{ config(materialized = 'table') }}

with entries as (

    select * from {{ ref('int_time_entries_unioned') }}

),

dim_service_module as (
    select service_module_key from {{ ref('dim_service_module') }}
),

resolved as (

    select
        -- Surrogate key for the fact. Includes every grain component so it is genuinely
        -- unique, and is deterministic so a rebuild produces identical keys.
        {{ surrogate_key([
            'e.company_key', 'e.employee_code', 'e.booked_at',
            'e.order_no', 'e.service_module_key'
        ]) }}                                                   as labour_hours_key,

        -- Dimension keys -----------------------------------------------------------
        e.company_key,
        {{ resolve_key('m.service_module_key') }}               as service_module_key,
        {{ date_key('e.booked_at') }}                           as booked_date_key,

        -- Degenerate dimensions ----------------------------------------------------
        e.employee_code,
        e.order_no,
        e.operation_seq,
        e.work_centre_code,
        e.booked_at,

        -- Measures -----------------------------------------------------------------
        -- Always hours. Converted upstream where the source booked seconds.
        e.work_hours,
        e.hourly_rate,
        e.labour_cost,

        -- Productive versus non-productive, which is the whole basis of utilisation.
        case when e.service_module_key = 4 then 0 else 1 end     as is_productive,

        -- Audit and provenance -----------------------------------------------------
        e.source_system,
        e.source_hours_unit,
        e.work_hours_as_sourced,
        -- Makes the seconds conversion provable rather than a claim in a comment.
        case
            when upper(coalesce(e.source_hours_unit, '')) = 'SEC' then 1
            else 0
        end                                                     as was_unit_converted,

        e.has_time_of_day,
        e.is_deleted

    from entries e
    left join dim_service_module m
        on e.service_module_key = m.service_module_key

)

select * from resolved
