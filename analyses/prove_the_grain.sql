/*
    PROVE THE GRAIN -- run this BEFORE writing a fact table, not after QA finds duplicates.

    This is the single highest-value query in the repository. It takes about a minute to
    write and it prevents the most expensive class of dimensional modelling bug there is.

    On a real project, skipping this check produced 325,318 duplicate rows in a 3.6 million
    row fact. It was found by a QA analyst weeks later, after the fact had already been
    reported from. The fix was straightforward once diagnosed; the cost was the weeks of
    reporting nobody could trust, and having to explain it.

    HOW TO READ THE RESULT

      source_rows            total rows in the source extract
      grain_date_only        distinct keys if you truncate the timestamp to a date
      grain_with_timestamp   distinct keys if you keep the full timestamp

    If grain_with_timestamp is (near) equal to source_rows but grain_date_only is well
    below it, the timestamp is part of the natural key and truncating it collapses
    distinct transactions. That is the whole diagnosis.

    Run it with:   dbt show -s prove_the_grain --limit 5
*/

with source as (

    select * from {{ source('raw', 'inventory_cost_txn') }}

),

counted as (

    select
        count(*)                                                    as source_rows,

        -- The obvious design: key on the transaction DATE.
        count(distinct
            cast(company_code as varchar)               || '|' ||
            trim(cast(item_code as varchar))            || '|' ||
            trim(cast(warehouse_code as varchar))       || '|' ||
            trim(cast(cost_component_code as varchar))  || '|' ||
            cast(cast(txn_timestamp as date) as varchar) || '|' ||
            cast(line_seq as varchar)
        )                                                           as grain_date_only,

        -- The correct design: key on the full TIMESTAMP.
        count(distinct
            cast(company_code as varchar)               || '|' ||
            trim(cast(item_code as varchar))            || '|' ||
            trim(cast(warehouse_code as varchar))       || '|' ||
            trim(cast(cost_component_code as varchar))  || '|' ||
            cast(txn_timestamp as varchar)              || '|' ||
            cast(line_seq as varchar)
        )                                                           as grain_with_timestamp

    from source

)

select
    source_rows,
    grain_date_only,
    grain_with_timestamp,

    -- How many distinct transactions a date-only grain would silently destroy.
    source_rows - grain_date_only                                   as rows_lost_by_truncating,
    round(100.0 * (source_rows - grain_date_only) / source_rows, 2) as pct_lost_by_truncating,

    -- What remains with the correct grain. Any residual here is a genuine source
    -- duplicate, not a modelling error -- and the difference matters enormously.
    source_rows - grain_with_timestamp                              as genuine_source_duplicates,

    case
        when grain_with_timestamp = source_rows
            then 'Timestamp belongs in the grain. Source is unique with it.'
        when grain_with_timestamp > grain_date_only
            then 'Timestamp belongs in the grain. Residual rows are genuine source duplicates -- investigate upstream, do not deduplicate silently.'
        else 'Neither candidate key is unique. The grain needs another column.'
    end                                                             as verdict

from counted
