/*
    Company dimension. Conformed -- every fact in the model joins to this, which is what
    makes group-level reporting possible at all.

    It also carries stores_utc, the flag that drives every timezone decision upstream.
    Small table, outsized importance.
*/

select
    company_key,
    company_name,
    company_short_code,
    source_system,
    stores_utc,
    local_timezone,
    is_manufacturing
from {{ ref('stg_companies') }}

union all

-- Unknown member. Present so that an unresolved company key resolves to a real row
-- rather than producing a null that an inner join downstream would silently drop.
select
    {{ var('unknown_key') }}            as company_key,
    cast('Unknown' as varchar)          as company_name,
    cast('UNK' as varchar)              as company_short_code,
    cast('UNKNOWN' as varchar)          as source_system,
    0                                   as stores_utc,
    cast('UTC' as varchar)              as local_timezone,
    0                                   as is_manufacturing
