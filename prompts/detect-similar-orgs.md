You are given a JSON array of organization folder slugs that are string-similar to each other.
Determine if they refer to the same real-world organization.

If yes, return the single canonical slug (the most complete, correct form).
If no, return null.

Output ONLY valid JSON — no explanation, no markdown fences. The first character must be `{`.
Output format: {"canonical": "slug"} or {"canonical": null}

=== CANDIDATE SLUGS ===
{{orgs}}
