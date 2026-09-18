{{ config(materialized = "view") }}

-- Cost components. Trimming matters here specifically: this source emits the same code
-- as both '100' and '100   ' depending on the extract run.

select
    {{ clean("cost_component_code") }}          as cost_component_code,
    {{ clean("description") }}                  as cost_component_name
from {{ source("raw", "cost_component_master") }}
