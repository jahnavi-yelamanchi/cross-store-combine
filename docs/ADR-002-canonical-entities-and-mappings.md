# ADR-002: Canonical entities and declarative store mappings

- Context: 8 stores hold Customer/Order/Batch under different names, units (cents, lb) and enum values.
- Decision: frozen Pydantic models as the canonical schema; one YAML per store maps field names, units and enum values; mapping code is generic.
- Alternative rejected: hand-written per-adapter converters.
- Why: one place to see every store's shape; unit and enum drift become config, not code; mappings double as input for data contracts.
- Consequence: transforms beyond rename/unit/enum need a code change in `EntityMapping`.
