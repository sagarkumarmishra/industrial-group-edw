{{ config(materialized = "view") }}

/*
    Production order operations -- staging.

    Planned hours and planned cost per operation, feeding the labour variance model.

    Note on planned_amount versus planned_hours * planned_rate: the source records the
    finished amount, and that is what is used. This is the same lesson as the corrupt
    quantity in the inventory source -- if the ERP already holds a computed amount,
    prefer it over recomputing from components, because recomputing inherits every
    defect in every component.

    Only operations at status 7 or 8 (released / complete) with non-zero planned hours
    are in scope. Status 3 is still being planned and has no meaningful variance.
*/

select
    cast(company_code as integer)                                       as company_key,
    {{ clean("production_order") }}                                     as production_order,
    cast(operation_seq as integer)                                      as operation_seq,
    {{ clean("work_centre_code") }}                                     as work_centre_code,
    cast(operation_status as integer)                                   as operation_status,

    cast(planned_hours as double)                                       as planned_hours,
    cast(planned_rate as double)                                        as planned_rate,

    -- Preferred over planned_hours * planned_rate. See note above.
    cast(planned_amount as double)                                      as planned_amount,

    {{ null_if_epoch("order_timestamp") }}                              as ordered_at

from {{ source("raw", "production_operations") }}
where cast(operation_status as integer) in (7, 8)
  and cast(planned_hours as double) > 0
