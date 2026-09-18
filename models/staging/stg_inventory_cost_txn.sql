{{ config(materialized = "view") }}

/*
    Inventory transaction cost -- staging.

    Three traps are handled here, and the comments matter more than the SQL.

    TRAP 1, TIMEZONE. The modern ERP subsidiaries store UTC; the legacy ones store local
    time. The conversion is applied conditionally on stores_utc. Applying it to all
    companies, or none, silently shifts dates for whichever group you got wrong.

    TRAP 3, GRAIN. txn_timestamp is unique to the second, and line_seq does NOT restart
    per day. That means the full timestamp is part of the natural key. Carrying only the
    date -- which is the obvious thing to do, since nobody reports at second granularity
    -- collapses distinct same-day transactions onto one key. The fact model keeps both:
    the timestamp for the grain, the date key for the date dimension join.

    TRAP 4, CORRUPT QUANTITIES. quantity is unreliable on a small number of rows. Cost is
    therefore taken from total_amount, which the source records correctly, rather than
    recomputed as quantity * unit_cost. Recomputing is how you end up reporting billions.
    A reliability flag is exposed so consumers can exclude the bad rows explicitly rather
    than having them silently filtered out here.
*/

with source as (

    select * from {{ source("raw", "inventory_cost_txn") }}

),

companies as (

    select company_key, stores_utc from {{ ref("stg_companies") }}

),

joined as (

    select
        s.*,
        c.stores_utc
    from source s
    -- Left join: a transaction for a company missing from the master is still a real
    -- transaction and must not disappear.
    left join companies c
        on cast(s.company_code as integer) = c.company_key

),

converted as (

    select
        cast(company_code as integer)                                   as company_key,
        {{ clean("item_code") }}                                        as item_code,
        {{ clean("warehouse_code") }}                                   as warehouse_code,
        {{ clean("cost_component_code") }}                              as cost_component_code,

        -- One expression, used for both the grain column and the date key, so they can
        -- never disagree with each other.
        {{ null_if_epoch(to_reporting_time("txn_timestamp", "stores_utc")) }}
                                                                        as txn_timestamp,
        cast(line_seq as integer)                                       as line_seq,

        cast(quantity as double)                                        as quantity,
        cast(unit_cost as double)                                       as unit_cost,
        cast(total_amount as double)                                    as total_amount,
        cast(is_wip as integer)                                         as is_wip,
        cast(is_deleted as integer)                                     as is_deleted,

        -- Trap 4 made explicit. Anything beyond a plausible transaction quantity is
        -- flagged rather than silently dropped, so the defect stays visible and
        -- auditable instead of becoming a mystery gap in the row count.
        case when quantity > 1000000 then 0 else 1 end                  as is_quantity_reliable

    from joined

)

select * from converted
