# PROXIMA // 1024

An open-source, Jev-forward command simulation about guiding 1,024 fictional people aboard a generation ship. Players balance survival systems, governance, morale, and uncertainty while an AI expedition partner helps interpret the ship's state.

This is an exploratory work of fiction, not a medical, engineering, demographic, or scientific forecast.

## What it includes

- A deterministic Python simulation with explicit mechanical effects
- A local browser interface and interactive 3D ship scene
- Astra as a bounded conversational expedition partner
- Jev population-response modeling for fictional crew decisions
- Local campaign saves, with provider request/response audits kept out of Git

## Run locally

Requirements: Python 3.11+, Node.js 20+, an OpenAI API key, and a Typesafe/Jev API key.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
npm install
npm run build

export OPENAI_API_KEY="your-key"
export TYPESAFE_API_KEY="your-key"
.venv/bin/python server.py --port 8924
```

Open `http://127.0.0.1:8924`. The server intentionally binds only to loopback.

## Privacy and provider use

Campaign state is stored locally in the ignored `state/` directory. When live model features are used, fictional campaign context and crew profiles are sent to the configured OpenAI and Typesafe APIs. Do not enter real personal or sensitive information into a campaign.

Credentials are read only from the `OPENAI_API_KEY` and `TYPESAFE_API_KEY` environment variables. The browser never receives them.

## Structure

- `server.py` — loopback HTTP server and job coordination
- `engine.py` — campaign state and explicit simulation actions
- `providers.py` — Astra and Jev adapters
- `sim_engine/` — deterministic simulation framework and systems
- `public/` — browser application and assets
- `data/` — scenario catalogs

## Development

```bash
.venv/bin/python -m unittest discover -p 'test_*.py' -v
npm run build
```

See [ASSET_PROVENANCE.md](ASSET_PROVENANCE.md) for source and generation notes. Contributions are welcome; please read [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT. See [LICENSE](LICENSE).

