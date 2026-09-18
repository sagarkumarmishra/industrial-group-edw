{{ config(materialized = "view") }}

-- Warehouses per subsidiary.

select
    cast(company_code as integer)               as company_key,
    {{ clean("warehouse_code") }}               as warehouse_code,
    {{ clean("description") }}                  as warehouse_description,
    cast(is_wip as integer)                     as is_wip
from {{ source("raw", "warehouse_master") }}
