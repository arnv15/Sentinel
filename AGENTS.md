# AGENTS.md

Guidance for AI coding agents (Codex, Claude, etc.) working in this repo.
Human-oriented docs are in [`README.md`](README.md) and
[`browser-agent/HANDOFF.md`](browser-agent/HANDOFF.md); read those for full context.

## What this repo is

Two **independent** projects sharing one repo. They share no code — treat them
separately and don't add cross-dependencies.

1. **Sentinel** (repo root, `scraper.py`) — a Discord→Telegram restock-alert
   monitor. Python, async, built on `discord.py-self` + `aiohttp`.
2. **Browser Research Agent** (`browser-agent/`) — an AI agent that drives
   Chromium via Playwright MCP through a raw Anthropic Messages API tool-use
   loop. Python, async, `anthropic` + `mcp`.

## Golden rules

- **Never hardcode or commit secrets.** All tokens/keys load from a git-ignored
  `.env` (there is one at the repo root for Sentinel and one in `browser-agent/`).
  Only `.env.example` templates are tracked. If you touch config, keep it
  env-based. Before committing, grep the diff for anything token-shaped.
- **`.env`, `.venv/`, and `__pycache__/` are git-ignored** — do not add them.
- **Don't couple the two projects.** No shared imports between `scraper.py` and
  `browser-agent/`.
- The browser agent has **safety guardrails** in `browser-agent/agent.py`
  (`MAX_STEPS`, `ALLOWED_DOMAINS`, `RISKY_KEYWORDS`, and a restrictive system
  prompt). Preserve them — don't remove confirmation prompts on
  buy/checkout/login/submit actions, and don't let the agent authenticate,
  purchase, or submit forms unattended.

## Environment

- Python 3.10+ (developed on 3.14). Each project has its **own** virtualenv.
- Node 18+ is required for the browser agent (it spawns `npx @playwright/mcp`).

## Setup & run

### Sentinel (repo root)
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # fill in DISCORD_TOKEN, TARGET_CHANNEL_ID, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_IDS
python scraper.py               # exits with a clear message if any var is missing
```

### Browser Research Agent (`browser-agent/`)
```bash
cd browser-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # fill in ANTHROPIC_API_KEY
npx playwright install chromium # one-time Chromium download (~150 MB)
python agent.py "your research task"
```

## Testing / verification

There is **no test suite yet**. Minimum bar before committing:

- `python -m py_compile scraper.py` and
  `python -m py_compile browser-agent/agent.py` must pass.
- Sentinel's parsing engine (`parse_alert`, `format_telegram`, and the regexes in
  `scraper.py`) is written as **pure functions with no Discord objects**, so it's
  the natural place to add `pytest` unit tests if you introduce tests.
- The browser agent can be smoke-tested without an API key by listing MCP tools
  (spawn `npx @playwright/mcp@latest --headless`, `initialize`, `list_tools`);
  a live run needs `ANTHROPIC_API_KEY`.

## Conventions

- **Style:** standard library + the deps already in `requirements.txt`. Match the
  existing style — module docstrings, section-banner comments (`# ===== ... =====`),
  type hints on new functions. No formatter is enforced; keep diffs minimal.
- **Async:** both entrypoints are `asyncio`-based. Don't block the event loop.
- **Secrets/config:** read via `os.environ.get(...)` with `python-dotenv`
  `load_dotenv()` already wired in. Add new config the same way and document it in
  the matching `.env.example`.
- **Models (browser agent):** the model id is the `MODEL` constant at the top of
  `browser-agent/agent.py` (currently `claude-sonnet-5`). Use current Anthropic
  model ids; don't invent them.

## Key files

| File | Purpose |
|---|---|
| `scraper.py` | Sentinel monitor: config → parsing engine (pure fns) → Discord/Telegram I/O. |
| `browser-agent/agent.py` | Agent loop + MCP glue + guardrails. Start here for that project. |
| `browser-agent/HANDOFF.md` | Deep context: architecture, decisions, current state, next steps. |
| `.env.example` (×2) | Secret templates. Update whenever you add config. |
