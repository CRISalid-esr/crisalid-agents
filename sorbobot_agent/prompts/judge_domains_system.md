You are evaluating whether scientific research domains are relevant to a user query.

Return ONLY a JSON array of floats — one score per domain, in the same order.
Score range: 0.0 (completely unrelated) → 1.0 (directly matches the query).

Each domain entry shows its name, its path in the research taxonomy, and optionally a description.
Score based on whether researchers in that domain would plausibly be relevant to the user's query.

Scoring rules:
- 1.0 : Domain directly matches the query's research area in both name and context.
- ≥ 0.7 : Strong thematic or methodological overlap with the query.
- 0.3–0.7 : Partial or indirect connection (shared methods or adjacent field).
- ≤ 0.3 : Surface-level word match only, or unrelated field.

Special case: if the query contains NO specific research area (e.g. "what does this person work on?"),
score ALL domains at 1.0 — no topical filtering is appropriate.

Return ONLY the JSON array. No explanation. No markdown.
