{{ config(materialized = "view") }}

-- Item master. Nothing clever here beyond trimming: source codes arrive fixed-width
-- padded, and an untrimmed join turns 'SEAL-00001' and 'SEAL-00001      ' into two
-- different items.

select
    {{ clean("item_code") }}                    as item_code,
    {{ clean("description") }}                  as item_description,
    {{ clean("item_family") }}                  as item_family,
    {{ clean("unit_of_measure") }}              as unit_of_measure,
    cast(standard_cost as double)               as standard_cost
from {{ source("raw", "item_master") }}
