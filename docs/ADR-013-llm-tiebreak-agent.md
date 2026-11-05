# ADR-013: LangGraph tiebreak agent with Instructor output and MCP evidence

- Context: pairs in the matcher's uncertain band need a model verdict that is typed, cheap, offline-testable and reads native records with drift intact.
- Decision: a LangGraph StateGraph (fetch_evidence -> decide -> validate) fetches records through an MCP server ("xstore-evidence", get_evidence tool, in-memory client), asks Claude Haiku 5.5 by default through Instructor with a Pydantic TiebreakDecision response_model, and sends the static system prompt as a cache_control block (prompt caching); confidence < 0.6 becomes "different, needs human".
- Alternative rejected: free-text completions parsed by regex; agent reading stores directly; live API calls in tests.
- Why: Instructor retries until the output validates; MCP lets external clients reuse the same evidence tool; a mock provider replays recorded fixtures keyed by sha256(model, system, prompt) so CI runs with no keys.
- Consequence: fixture misses fall back to a labelled simulated verdict that is not model output; prompt edits invalidate fixtures until re-recorded with `python -m xstore.agent.record_fixtures`; per-model minimum cacheable prompt length lives in config.yaml and must be checked against current Anthropic docs before real runs.
