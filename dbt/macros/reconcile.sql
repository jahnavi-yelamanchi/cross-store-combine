{#
  One row per canonical key across every store that holds `entity_type`.
  Emits <field>__<store> per store, the policy-picked value per field (first non-null in
  source priority from var xstore_priority, generated from policy/policy.yaml), n_sources,
  sources, any_type_drift and conflict_fields (comma list of fields whose non-null values
  disagree across stores; NULL when none).
#}
{% macro reconcile(entity_type, plural, key, fields) %}
{%- set rules = var('xstore_priority')[entity_type] -%}
{%- set stores = rules['_default'] -%}
with unioned as (
{%- for s in stores %}
    select _store, {{ key }}, {{ fields | join(', ') }}, updated_at, _type_drift
    from {{ ref('stg_' ~ s ~ '__' ~ plural) }}
    {%- if not loop.last %}
    union all
    {%- endif %}
{%- endfor %}
),

deduped as (
    select * from unioned
    where {{ key }} is not null
    qualify row_number() over (
        partition by _store, {{ key }} order by updated_at desc nulls last
    ) = 1
)

select
    {{ key }},
{%- for f in fields %}
{%- for s in stores %}
    max({{ f }}) filter (where _store = '{{ s }}') as {{ f }}__{{ s }},
{%- endfor %}
{%- endfor %}
{%- for f in fields %}
    coalesce(
    {%- for s in rules.get(f, stores) %}
        max({{ f }}) filter (where _store = '{{ s }}'){{ ',' if not loop.last }}
    {%- endfor %}
    ) as {{ f }},
{%- endfor %}
    max(updated_at) as updated_at,
    count(*) as n_sources,
    string_agg(_store, ',' order by _store) as sources,
    bool_or(_type_drift) as any_type_drift,
    nullif(concat_ws(','
    {%- for f in fields %},
        case when count(distinct {{ f }}) > 1 then '{{ f }}' end
    {%- endfor %}
    ), '') as conflict_fields
from deduped
group by {{ key }}
{% endmacro %}
