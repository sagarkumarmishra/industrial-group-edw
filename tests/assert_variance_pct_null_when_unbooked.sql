-- Variance percentage must be NULL, never a number, when nothing was booked.
--
-- Guards against the -100% defect described in the fct_labour_hours_variance header. A
-- planned operation with no booking is not 100% under plan, it simply never ran, and
-- reporting it as a number drags every average it touches.

select
    labour_variance_key,
    company_key,
    production_order,
    operation_seq,
    planned_hours,
    actual_hours,
    has_actual_booking,
    hours_variance_pct
from {{ ref("fct_labour_hours_variance") }}
where has_actual_booking = 0
  and hours_variance_pct is not null
