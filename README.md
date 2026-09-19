# PROXIMA // 1024

An open-source, Jev-forward command simulation about guiding a fictional crew aboard a generation ship. Players balance survival systems, governance, morale, and uncertainty while AI expedition partners help interpret the ship's state. Manifest size can range up to 50,000.

This is an exploratory work of fiction, not a medical, engineering, demographic, or scientific forecast.

## What it includes

- A deterministic Python simulation with explicit mechanical effects
- A local browser interface and interactive 3D ship scene
- Astra as a bounded conversational expedition partner
- VIGIL, a read-only Claude ship-intelligence agent that consults simulation evidence and prepares layered technical briefs
- Jev population-response modeling for fictional crew decisions
- Multiple incident domains, including outbreaks, political conflict, systems failures, crime, and AI governance
- Local campaign saves, with provider request/response audits kept out of Git

## Run locally

Requirements: Python 3.11+, Node.js 20+, an OpenAI API key, a Typesafe/Jev API key, and an installed and authenticated Claude Code CLI for VIGIL. The Claude Agent SDK is installed with the Python requirements. Set `PROXIMA_BRIEF_PROVIDER=astra` if you want Astra, rather than Claude, to generate technical briefs; Astra and Jev still require their respective keys.

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

Campaign state is stored locally in the ignored `state/` directory. When live model features are used, fictional campaign context and crew profiles are sent to the configured OpenAI and Typesafe APIs, and VIGIL sends a read-only fictional campaign snapshot through the locally authenticated Claude Code CLI. Do not enter real personal or sensitive information into a campaign.

API credentials are read only from the `OPENAI_API_KEY` and `TYPESAFE_API_KEY` environment variables. VIGIL uses the Claude Code CLI's local authentication; no Anthropic API key is required by this project. The browser never receives credentials.

## Structure

- `server.py` — loopback HTTP server and job coordination
- `engine.py` — campaign state and explicit simulation actions
- `providers.py` — Astra and Jev adapters
- `claude_agent.py` — VIGIL's bounded, read-only tools and technical-brief contract
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
