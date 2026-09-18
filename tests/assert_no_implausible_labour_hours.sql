-- No labour booking should exceed 24 hours in a day, in any subsidiary or source system.
--
-- A blunt plausibility check, and exactly the kind that catches a unit-of-measure bug that
-- every structural test passes straight over. Row counts reconcile, keys are unique,
-- nothing is null -- and the hours are still 3,600x out.

select
    labour_hours_key,
    company_key,
    source_system,
    source_hours_unit,
    work_hours
from {{ ref("fct_labour_hours") }}
where work_hours > 24
