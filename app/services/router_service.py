"""
app/services/router_service.py

Deterministic routing for Smart search (the 5th, agent-routed mode) — pure
logic, no I/O, no AI provider awareness. Mirrors the separation already
established between vector_service.py (embed+search) and
matching_service.py (score): matching_service.classify_query() talks to an
AI provider to decompose the buyer's text into requirement clauses;
decide_route() below turns that decomposition into a routing decision.

The decision itself is deterministic code, not something the LLM decides —
same philosophy matching_service._compute_deterministic_scores already
uses (the model extracts/observes, code decides), so the rule here is
inspectable and testable rather than a black box.
"""

ROUTE_TRADITIONAL = "traditional"
ROUTE_VECTOR = "vector"
ROUTE_MATCH = "match"


def decide_route(preferences: str, requirements: list[dict]) -> tuple[str, str]:
    """Returns (route, reason).

    - No preferences text at all -> Traditional: filters only, nothing to
      reason about.
    - Exactly one requirement and it's not negated -> Vector search: cheap,
      no LLM scoring call, and this is exactly the case vector search is
      good at (this project's own measured findings: negation and
      multi-clause compounds are its known weak spots — see
      scripts/vector_eval_dashboard.py and docs/architecture.md).
    - Multiple requirements and/or any negated -> AI-scored matching: the
      more expensive but accurate path, used specifically when vector
      search's known weaknesses would apply.
    """
    if not preferences.strip():
        return ROUTE_TRADITIONAL, "No description provided — using structured filters only."

    if not requirements:
        return ROUTE_VECTOR, "No distinct requirement detected in the text — treating as a simple semantic query."

    negated = [r["text"] for r in requirements if r.get("negated")]
    if len(requirements) > 1 or negated:
        if negated and len(requirements) > 1:
            reason = (
                f"Detected {len(requirements)} requirements, including a negation "
                f"(\"{negated[0]}\") — routing to AI-scored matching for accurate per-requirement judgment."
            )
        elif negated:
            reason = f"Detected a negation (\"{negated[0]}\") — routing to AI-scored matching, which vector search can't reliably distinguish from its positive form."
        else:
            reason = f"Detected {len(requirements)} distinct requirements — routing to AI-scored matching so each is judged individually."
        return ROUTE_MATCH, reason

    return ROUTE_VECTOR, f"Single, non-negated requirement detected (\"{requirements[0]['text']}\") — routing to vector search."
