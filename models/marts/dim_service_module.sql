/*
    Service module dimension.

    This is the discriminator that keeps operationally different businesses apart once
    their data lands in the same fact table. Field service at a customer site, workshop
    repair, production against a works order and non-productive leave are four different
    activities that share the same time-entry tables.

    Getting this wrong produces the worst kind of error: mixing field service and workshop
    repair returns a number that looks entirely plausible and is meaningless. Any query
    against a fact carrying this key should group by it or filter on it.

    Hand-assigned keys rather than hashed, because the values are a fixed, small, known
    domain and stable keys keep the mapping in the legacy staging model readable.
*/

select
    cast(1 as integer)                                                      as service_module_key,
    cast('FIELD_SERVICE' as varchar)                                        as service_module_code,
    cast('On-site service performed at the customer site' as varchar)       as description

union all
select cast(2 as integer), cast('DEPOT_REPAIR' as varchar),
       cast('Repair performed in one of our own workshops' as varchar)

union all
select cast(3 as integer), cast('MANUFACTURING' as varchar),
       cast('Production against a works order' as varchar)

union all
select cast(4 as integer), cast('GENERAL_LEAVE' as varchar),
       cast('Leave and other non-productive booked time' as varchar)

union all
select cast({{ var('unknown_key') }} as integer), cast('UNKNOWN' as varchar),
       cast('Source value could not be mapped to a known module' as varchar)
