-- Resolved order per key.
select
    order_id,
    customer_id,
    amount,
    currency,
    status,
    ordered_at,
    updated_at,
    n_sources,
    conflict_fields,
    conflict_fields is not null as has_conflict
from {{ ref('reconciled_orders') }}
