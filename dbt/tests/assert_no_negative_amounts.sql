-- Canonical amounts are major currency units and never negative (Order.amount ge=0).
select order_id, amount
from {{ ref('canonical_orders') }}
where amount < 0
