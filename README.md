# LLM Relay Manager

LLM Relay Manager is a local web console for managing AI relay endpoints, API keys, protocol detection, model discovery, and availability checks.

It is designed for operators who need to validate whether a relay endpoint can actually serve OpenAI-compatible, Anthropic-compatible, or Gemini-compatible traffic before handing it to users or downstream systems.

## Features

- Manage relay stations and multiple API keys under each station
- Auto-detect supported protocols after adding a key
- Discover model lists per detected protocol
- Run availability checks for individual protocol bindings
- Search models across stations, keys, and bindings
- Review background jobs, scheduling state, and check history
- Use a lightweight single-file Python backend with SQLite storage

## Supported Protocol Probes

- `OpenAI Chat`
- `OpenAI Responses`
- `Claude / Anthropic Messages`
- `Gemini GenerateContent`

## Screens and Routes

- `/` dashboard overview
- `/stations` station-centric workspace
- `/keys` key-centric operations page
- `/models` model search
- `/history` jobs, scheduler state, and history

## Tech Stack

- Python 3.10+
- SQLite
- Built-in `http.server`
- `curl` for upstream probing and checks
- Plain HTML, CSS, and JavaScript

## Quick Start

```bash
git clone https://github.com/qihaze123/llm-relay-manager.git
cd llm-relay-manager
python3 app.py
```

Default address:

```text
http://127.0.0.1:8787
```

Custom host or port:

```bash
python3 app.py --host 0.0.0.0 --port 8791
```

## Requirements

- Python `3.10` or newer
- `curl` available in `PATH`

No third-party Python package is required for the current version.

## API Overview

- `GET /api/summary`
- `GET /api/stations`
- `POST /api/stations`
- `PUT /api/stations/:id`
- `DELETE /api/stations/:id`
- `GET /api/keys`
- `POST /api/keys`
- `PUT /api/keys/:id`
- `DELETE /api/keys/:id`
- `GET /api/bindings`
- `POST /api/keys/:id/detect`
- `POST /api/bindings/:id/discover`
- `POST /api/bindings/:id/check`
- `GET /api/models/search?q=...&available_only=1`
- `GET /api/history?limit=100`
- `GET /api/jobs?limit=100`
- `GET /api/jobs/:id`
- `GET /api/settings/scheduler`
- `PUT /api/settings/scheduler`
- `POST /api/run-cycle`

## Data Storage

The application stores its local data in `data/relay_manager.db`.

Main tables:

- `stations`
- `api_keys`
- `protocol_bindings`
- `binding_models`
- `binding_checks`
- `binding_check_history`
- `jobs`
- `app_settings`

## Security Notice

- API keys are currently stored in plaintext in SQLite.
- This project is intended for local or tightly controlled internal environments.
- Do not expose the web UI directly to the public internet.
- Do not use production-grade secrets in an untrusted host without adding your own encryption and access controls.

More details are documented in `SECURITY.md`.

## Limitations

- Single-node tool, not a production control plane
- In-process scheduler and background jobs
- In-progress jobs are interrupted on process restart
- Protocol detection is best-effort and may be fooled by some relay implementations
- Current version prioritizes operator workflows over scale optimization

## Roadmap

- Encrypted API key storage
- More protocol adapters and probe strategies
- Import and export support
- Batch operations
- Better success-rate, latency, and error analytics
- Stronger task execution architecture

## Development

```bash
python3 app.py
```

The current UI is Chinese-first, while the project documentation is written for public GitHub distribution.

## License

MIT
