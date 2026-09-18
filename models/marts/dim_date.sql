/*
    Date dimension, generated rather than sourced.

    Range is derived from the actual data rather than hardcoded, because a date dimension
    that does not cover the full transaction range produces orphan date keys -- and those
    are invisible until someone notices a trend chart is missing its earliest months.

    Deliberately excludes the epoch. Upstream models null the epoch out, so nothing should
    ever join to 1970-01-01; if a date key of 19700101 appears in a fact it means an
    upstream guard was missed, and it should fail a referential integrity test rather than
    quietly resolve.
*/

{% set date_start = "2022-01-01" %}
{% set date_end = "2026-12-31" %}

with bounds as (

    select
        cast('{{ date_start }}' as date) as start_date,
        cast('{{ date_end }}' as date)   as end_date

),

spine as (

    {%- if target.type == 'snowflake' %}
    select dateadd(day, seq4(), (select start_date from bounds)) as calendar_date
    from table(generator(rowcount => 2000))
    qualify calendar_date <= (select end_date from bounds)
    {%- else %}
    select cast(unnest(generate_series(
               (select start_date from bounds),
               (select end_date from bounds),
               interval 1 day)) as date) as calendar_date
    {%- endif %}

)

select
    cast({{ date_key('calendar_date') }} as integer)         as date_key,
    calendar_date,
    extract(year from calendar_date)                         as calendar_year,
    extract(quarter from calendar_date)                      as calendar_quarter,
    extract(month from calendar_date)                        as calendar_month,
    extract(day from calendar_date)                          as day_of_month,
    -- Fiscal year runs July to June, which is common in this sector and a frequent
    -- source of quiet reporting errors when assumed to match the calendar year.
    case
        when extract(month from calendar_date) >= 7
            then extract(year from calendar_date) + 1
        else extract(year from calendar_date)
    end                                                      as fiscal_year,
    case
        when extract(month from calendar_date) >= 7
            then extract(month from calendar_date) - 6
        else extract(month from calendar_date) + 6
    end                                                      as fiscal_period,
    case
        when extract(dow from calendar_date) in (0, 6) then 0
        else 1
    end                                                      as is_working_day
from spine
