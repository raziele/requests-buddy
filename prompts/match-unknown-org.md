You are given a summary of a funding request filed under an "unknown" organization, and a list of existing organization slugs.

Determine which existing organization this request belongs to, or propose a new slug if it doesn't match any.

Rules:
- If the request matches an existing org, return its slug exactly as listed.
- If it does not match any existing org, invent a short kebab-case slug (a-z, 0-9, hyphens only, max 60 chars).

Output ONLY valid JSON — no explanation, no markdown fences. The first character must be `{`.
Output format: {"org_slug": "slug"}

=== EXISTING ORGS ===
{{existing_orgs}}

=== REQUEST SUMMARY ===
{{request}}
