Generate a short, descriptive filename (no extension) for a request document.

Rules:
- Use lowercase kebab-case (e.g. `zaka-emergency-rescue-equipment`)
- Include the organization name and a brief descriptor of the request
- Maximum 80 characters
- Use only [a-z0-9-] characters
- Return ONLY the filename, nothing else — no explanation, no quotes, no extension

Input: the summary and organization name of the request.
