-- Resolved customer per key; the table Great Expectations validates.
select
    customer_id,
    name,
    email,
    phone,
    country,
    updated_at,
    n_sources,
    conflict_fields,
    conflict_fields is not null as has_conflict
from {{ ref('reconciled_customers') }}
