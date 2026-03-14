You are analyzing requests from the same organization to detect duplicates.

Below is a list of requests from a single org. Identify groups of requests that are semantically the same ask — same topic, same need, same funding request — even if worded differently or sent at different times.

Return ONLY valid JSON — an array of groups. Each group is an array of file paths that should be merged.
If no duplicates are found, return an empty array: []

Rules:
- A group must contain 2 or more paths.
- Only group requests that are clearly about the same specific ask. Similar-topic requests from the same org are NOT duplicates unless they are asking for the same thing.
- Do not group requests that are merely related (e.g. two different funding requests from the same org).

=== REQUESTS ===
{{requests}}
