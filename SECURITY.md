# Security Policy

## Current Security Model

LLM Relay Manager is currently intended for local or restricted internal use.

Important limitations:

- API keys are stored in plaintext in the local SQLite database
- The application does not include authentication or role-based access control
- The built-in web server is meant for lightweight local deployment
- Background jobs and scheduler state are process-local

## Safe Usage Recommendations

- Run the service only on trusted machines
- Put it behind your own access control if remote access is required
- Do not expose it directly to the public internet
- Use non-production or scoped credentials whenever possible
- Regularly rotate any keys managed by the tool

## Reporting Issues

If you discover a security issue, please avoid opening a public issue with live secrets or exploit details.

Open a private channel with the maintainer first, or remove sensitive details before reporting publicly.
