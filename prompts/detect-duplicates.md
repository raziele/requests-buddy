You are analyzing incoming requests to detect duplicates.

Below are NEW requests (just arrived) and EXISTING requests (already in the system).
Identify which NEW requests are semantically duplicate or near-duplicate of EXISTING ones.
Two requests are duplicates if they are about the same topic/ask from the same or different senders.

Output ONLY a raw JSON array — no explanation, no commentary, no markdown fences. The first character of your response must be `[` and the last must be `]`.

Each element of the array is a group (an array of file path strings) that should be merged together. Each group must contain at least one NEW file and at least one EXISTING file. If no duplicates are found, output exactly: []

=== NEW REQUESTS ===
{{new_requests}}

=== EXISTING REQUESTS ===
{{existing_requests}}
