{#
    Portable helpers shared by every model.

    These exist for two reasons. First, the timezone and date-key rules are the two
    easiest things in the whole project to get subtly wrong, so they are defined once
    rather than repeated in a dozen models. Second, DuckDB and Snowflake disagree on
    some syntax, and confining that difference to macros keeps the models identical
    across both targets.
#}


{#
    Converts a source timestamp to the group's reporting timezone -- but ONLY for
    subsidiaries whose source system stores UTC.

    This is the single most consequential expression in the project. The modern ERP
    stores UTC; the legacy systems store local time. Convert everything and the legacy
    companies shift by an hour or two. Convert nothing and the ERP companies do. Either
    way the error is invisible: dates still look like dates, and only a reconciliation
    against source reveals it -- and only if the reconciliation applies the same rule,
    which is a trap in itself.
#}
{% macro to_reporting_time(ts_column, stores_utc_column) %}
    case
        when {{ stores_utc_column }} = 1
        then
            {%- if target.type == 'snowflake' %}
            convert_timezone('UTC', '{{ var("reporting_timezone") }}', {{ ts_column }})
            {%- else %}
            (({{ ts_column }} at time zone 'UTC') at time zone '{{ var("reporting_timezone") }}')
            {%- endif %}
        else {{ ts_column }}
    end
{% endmacro %}


{#
    Builds a YYYYMMDD integer date key, guarding the epoch.

    ERPs routinely write 1970-01-01 to mean "no date". Left alone it becomes a real
    looking date key of 19700101 that joins to the date dimension and quietly pulls
    fifty years of nothing into every trend chart.
#}
{% macro date_key(ts_column) %}
    case
        when {{ ts_column }} is null then null
        when cast({{ ts_column }} as date) <= date '{{ var("epoch_cutoff") }}' then null
        else cast(
            {%- if target.type == 'snowflake' %}
            to_char({{ ts_column }}, 'YYYYMMDD')
            {%- else %}
            strftime({{ ts_column }}, '%Y%m%d')
            {%- endif %}
            as integer)
    end
{% endmacro %}


{#
    Nulls out an epoch timestamp entirely, for cases where the timestamp itself is
    carried rather than just a date key.
#}
{% macro null_if_epoch(ts_column) %}
    case
        when cast({{ ts_column }} as date) <= date '{{ var("epoch_cutoff") }}' then null
        else {{ ts_column }}
    end
{% endmacro %}


{#
    Deterministic integer surrogate key from any number of parts.

    Two deliberate choices. Parts are trimmed, because source text arrives fixed-width
    padded and 'MAIN' must not become a different key from 'MAIN      '. And a separator
    is used, so ('AB','C') cannot collide with ('A','BC').
#}
{% macro surrogate_key(parts) %}
    abs(
        {%- if target.type == 'snowflake' %}
        hash(
        {%- else %}
        hash(
        {%- endif %}
            {%- for part in parts %}
            coalesce(cast(trim(cast({{ part }} as varchar)) as varchar), '~')
            {%- if not loop.last %} || '|' || {% endif %}
            {%- endfor %}
        )
    )
{% endmacro %}


{#
    Trim helper. Source codes arrive padded to a fixed width; every join and group-by
    must trim or the same business value splits into two distinct values.
#}
{% macro clean(column) %}
    nullif(trim(cast({{ column }} as varchar)), '')
{% endmacro %}


{#
    Resolves a dimension key to the unknown member rather than leaving it null.
    A fact row with an unresolved dimension is still a real transaction and must not be
    dropped or made invisible by an inner join downstream.
#}
{% macro resolve_key(key_column) %}
    coalesce({{ key_column }}, {{ var('unknown_key') }})
{% endmacro %}


{#
    Safe division. Returns null rather than erroring or returning zero, so a null
    percentage is visibly "not calculable" instead of a misleading 0%.
#}
{% macro safe_divide(numerator, denominator) %}
    case
        when {{ denominator }} is null or {{ denominator }} = 0 then null
        else cast({{ numerator }} as double) / cast({{ denominator }} as double)
    end
{% endmacro %}
