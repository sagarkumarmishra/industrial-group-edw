{{ config(materialized = "view") }}

-- Company master. Small, but it drives the two things most likely to go wrong in a
-- multi-company consolidation: which timezone rule applies, and which subsidiaries are
-- even in scope for manufacturing models.
--
-- Source table is named ENTITY, a reserved word. Kept as-is deliberately -- see
-- ingest/load.py for why renaming it would be the wrong fix.

select
    cast(company_code as integer)               as company_key,
    {{ clean("company_name") }}                 as company_name,
    {{ clean("short_code") }}                   as company_short_code,
    {{ clean("source_system") }}                as source_system,
    cast(stores_utc as integer)                 as stores_utc,
    {{ clean("local_timezone") }}               as local_timezone,
    cast(is_manufacturing as integer)           as is_manufacturing
from {{ source("raw", "entity") }}
