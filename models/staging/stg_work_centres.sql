{{ config(materialized = "view") }}

-- Work centres. Only the two manufacturing subsidiaries have these, which is correct
-- rather than a gap -- the other four do not manufacture.

select
    cast(company_code as integer)                   as company_key,
    {{ clean("work_centre_code") }}                 as work_centre_code,
    {{ clean("description") }}                      as work_centre_description,
    cast(capacity_hours_per_week as double)         as capacity_hours_per_week,
    cast(labour_rate as double)                     as labour_rate
from {{ source("raw", "work_centre_master") }}
