-- Proves the seconds-to-hours conversion actually fired.
--
-- A comment claiming a conversion happened is worth nothing. This asserts it: any row the
-- source booked in SEC must now hold a value strictly smaller than what was sourced, and
-- must land in a plausible range for a single booking.
--
-- Without the conversion these rows would be up to 3,600x too large, and no other test in
-- the suite would notice -- the values are still positive numbers.

select
    labour_hours_key,
    source_hours_unit,
    work_hours_as_sourced,
    work_hours,
    was_unit_converted
from {{ ref("fct_labour_hours") }}
where source_hours_unit = 'SEC'
  and (
        -- conversion did not reduce the value
        work_hours >= work_hours_as_sourced
        -- or produced something impossible for one booking
     or work_hours > 24
     or was_unit_converted <> 1
  )
