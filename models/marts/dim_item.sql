/*
    Item dimension.

    Surrogate key rather than the natural code, for two reasons: the natural code is a
    padded string that is expensive to join on at volume, and a surrogate insulates the
    facts from any future change in source coding.
*/

select
    {{ surrogate_key(["item_code"]) }}      as item_key,
    item_code,
    item_description,
    item_family,
    unit_of_measure,
    standard_cost
from {{ ref("stg_items") }}

union all

select
    {{ var("unknown_key") }}                as item_key,
    cast('UNKNOWN' as varchar)              as item_code,
    cast('Unknown item' as varchar)         as item_description,
    cast('UNKNOWN' as varchar)              as item_family,
    cast('EA' as varchar)                   as unit_of_measure,
    cast(0 as double)                       as standard_cost
