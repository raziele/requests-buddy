You are analyzing incoming requests to detect duplicates.

Below are NEW requests (just arrived) and EXISTING requests (already in the system).
Identify which NEW requests are semantically duplicate or near-duplicate of EXISTING ones.
Two requests are duplicates if they are about the same topic/ask from the same or different senders.

Return ONLY valid JSON — an array of groups. Each group is an array of file paths that should be merged.
Each group must contain at least one NEW file and at least one EXISTING file.
If no duplicates are found, return an empty array: []

=== NEW REQUESTS ===
{{new_requests}}

=== EXISTING REQUESTS ===
{{existing_requests}}
