/*
    Warehouse dimension. Keyed on company AND warehouse code, because warehouse codes are
    only unique within a subsidiary -- every company has a warehouse called MAIN.

    This is a common and expensive mistake in multi-company models: key on the code alone
    and six subsidiaries collapse into a single warehouse.
*/

select
    {{ surrogate_key(["company_key", "warehouse_code"]) }}  as warehouse_key,
    company_key,
    warehouse_code,
    warehouse_description,
    is_wip
from {{ ref("stg_warehouses") }}

union all

select
    {{ var("unknown_key") }}                as warehouse_key,
    {{ var("unknown_key") }}                as company_key,
    cast('UNKNOWN' as varchar)              as warehouse_code,
    cast('Unknown warehouse' as varchar)    as warehouse_description,
    0                                       as is_wip
