{{ config(materialized = "view") }}

/*
    COMMON SCHEMA LAYER -- unified time entries across every subsidiary.

    This is the single most important model in the project, and the reason the whole
    architecture works.

    THE PROBLEM IT SOLVES
    Six subsidiaries run four different source systems. Their time-entry extracts have
    different column names, different granularity, different units, different timezone
    behaviour, and in one case a text job type where the other has a numeric key. If the
    fact models read those sources directly, then every fact has to know about every
    subsidiary -- and onboarding an acquisition means editing every model in the project.

    WHAT THIS LAYER GUARANTEES
    Everything downstream sees ONE shape. Adding a seventh subsidiary means adding one
    staging model and one branch here. No fact model changes. That is the difference
    between onboarding an acquisition in days and re-testing the entire warehouse.

    WHY UNION ALL, NOT UNION
    UNION deduplicates across the whole result set, which is both expensive at volume and
    actively wrong here: two subsidiaries can legitimately book identical hours for the
    same employee code on the same day. Deduplication, where it is needed, belongs in the
    fact model at a defined grain -- not as a side effect of a set operator.
*/

with modern as (

    select
        company_key,
        employee_code,
        booked_at,
        service_module_key,
        order_no,
        operation_seq,
        work_centre_code,
        work_hours,
        source_hours_unit,
        work_hours_as_sourced,
        hourly_rate,
        is_deleted,
        source_system,
        -- Modern ERP records a full timestamp, so time-of-day analysis is possible.
        1 as has_time_of_day
    from {{ ref("stg_time_entries_modern") }}

),

legacy as (

    select
        company_key,
        employee_code,
        booked_at,
        service_module_key,
        order_no,
        operation_seq,
        work_centre_code,
        work_hours,
        source_hours_unit,
        work_hours_as_sourced,
        hourly_rate,
        is_deleted,
        source_system,
        -- Legacy sources carry a date only. Flagged rather than hidden, because a
        -- consumer analysing hours by shift needs to know these rows cannot support it.
        0 as has_time_of_day
    from {{ ref("stg_time_entries_legacy") }}

),

unioned as (

    select * from modern
    union all
    select * from legacy

)

select
    *,
    -- Derived here so it is defined once for every consumer rather than reimplemented,
    -- slightly differently, in each downstream model.
    round(work_hours * hourly_rate, 2) as labour_cost
from unioned

