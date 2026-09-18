/*
    Cost component dimension -- labour, material, tool, overhead.

    Matters more than its size suggests: inventory cost is held PER COMPONENT, so a whole
    unit cost is the SUM across components, not a single column. Missing that produces a
    cost figure that is quietly a fraction of the real one.
*/

select
    {{ surrogate_key(["cost_component_code"]) }}    as cost_component_key,
    cost_component_code,
    cost_component_name
from {{ ref("stg_cost_components") }}

union all

select
    {{ var("unknown_key") }}                as cost_component_key,
    cast('UNKNOWN' as varchar)              as cost_component_code,
    cast('Unknown component' as varchar)    as cost_component_name
