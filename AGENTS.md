# AGENTS.md

Guidance for AI coding agents (Codex, Claude, etc.) working in this repo.
Human-oriented docs are in [`README.md`](README.md) and
[`browser-agent.md`](browser-agent.md); read those for full context.

## What this repo is

One project, two phases — independent today, connected later (the scraper will
trigger the browser agent to research a restock hit).

1. **Scraper** (`src/scraper.py`) — a Discord→Telegram restock-alert
   monitor. Python, async, built on `discord.py-self` + `aiohttp`.
2. **Browser Research Agent** (`src/browser_agent.py`) — an AI agent that
   drives Chromium via Playwright MCP through a raw, provider-agnostic tool-use
   loop. Python, async, `openai` + `anthropic` + `mcp`. Defaults to Gemini's
   free tier; `LLM_PROVIDER` switches providers without code changes.

## Layout

```
src/
  scraper.py       # Phase 1 entrypoint
  browser_agent.py # Phase 2 entrypoint
mock-store/        # static fake storefront — test fixture for Phase 2 (no Python)
browser-agent.md   # Phase 2 deep context
requirements.txt   # ALL deps — one file, one virtualenv at the repo root
.env.example       # ALL secrets — one file, both phases
```

Both files are **standalone scripts**, not a package — run them by path
(`python src/scraper.py`), not with `-m`. When Phase 1 eventually calls Phase 2,
a plain `from browser_agent import run_agent` works, since both sit in `src/`.

There is a **single** `.venv/`, `.env`, `requirements.txt`, and `.gitignore` at
the repo root. Don't reintroduce per-project copies.

## Golden rules

- **Never hardcode or commit secrets.** All tokens/keys load from the git-ignored
  root `.env`. Only `.env.example` is tracked. If you touch config, keep it
  env-based. Before committing, grep the diff for anything token-shaped.
- **`.env`, `.venv/`, and `__pycache__/` are git-ignored** — do not add them.
- The browser agent has **safety guardrails** in `src/browser_agent.py`
  (`MAX_STEPS`, `ALLOWED_DOMAINS`, `RISKY_KEYWORDS`, and the system prompt).
  Preserve what remains and don't weaken them further without the user asking.
  **Note:** the purchase/login keywords and the system-prompt line forbidding
  logins and purchases were deliberately removed by the user for testing — that
  is a known, intentional deviation, not a bug to silently "fix". Flag it rather
  than reverting it unasked.

## Environment

- Python 3.10+ (developed on 3.14). **One** virtualenv at the repo root.
- Node 18+ is required for the browser agent (it spawns `npx @playwright/mcp`).
- Playwright's Chromium is downloaded once via `npx playwright install chromium`
  and cached outside the repo (`~/Library/Caches/ms-playwright` on macOS).

## Setup & run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # fill in only the vars for the phase you're running

python src/scraper.py                  # Phase 1
python src/browser_agent.py "a task"   # Phase 2
```

## Testing / verification

There is **no test suite yet**. Minimum bar before committing:

- `python -m py_compile src/scraper.py src/browser_agent.py` must pass.
- The scraper's parsing engine (`parse_alert`, `format_telegram`, and the regexes)
  is written as **pure functions with no Discord objects**, so it's the natural
  place to add `pytest` unit tests if you introduce tests.
- The browser agent can be smoke-tested without an API key by listing MCP tools
  (spawn `npx @playwright/mcp@latest --headless`, `initialize`, `list_tools`);
  a live run needs a key for the configured provider (default `GEMINI_API_KEY`).
- **Test agent runs against `mock-store/`, never a real retailer** — serve it with
  `cd mock-store && python3 -m http.server 8000`. It is a static fake storefront
  with no backend; its checkout deliberately submits nowhere. If you extend it,
  keep it that way (no `action`, no `fetch`) and keep the markup accessible —
  real `<button>`/`<a>`, `<label for>` on inputs, `aria-label`s naming the
  product — because the agent targets elements from the accessibility tree.

## Conventions

- **Style:** standard library + the deps already in `requirements.txt`. Match the
  existing style — module docstrings, section-banner comments (`# ===== ... =====`),
  type hints on new functions. No formatter is enforced; keep diffs minimal.
- **Async:** both entrypoints are `asyncio`-based. Don't block the event loop.
- **Secrets/config:** read via `os.environ.get(...)` with `python-dotenv`
  `load_dotenv()` already wired in. Add new config the same way and document it
  in `.env.example`.
- **Models (browser agent):** provider + model come from `LLM_PROVIDER` /
  `LLM_MODEL` in `.env`, with defaults in the `PROVIDERS` table at the top of
  `src/browser_agent.py` (default: `gemini` / `gemini-3.7-flash`). The loop is
  provider-agnostic — all non-Anthropic providers go through the OpenAI
  Chat Completions adapter. Don't invent model ids.
- **MCP attribute names:** the `mcp` package exposes **snake_case** attributes
  (`input_schema`, `is_error`, `mime_type`); the camelCase spellings are wire
  aliases only and reading them silently misbehaves. Go through the `_attr()` /
  `tool_schema()` helpers.

## Key files

| File                       | Purpose                                                                      |
| -------------------------- | ---------------------------------------------------------------------------- |
| `src/scraper.py`      | Phase 1: config → parsing engine (pure fns) → Discord/Telegram I/O.          |
| `src/browser_agent.py`| Phase 2: agent loop + MCP glue + guardrails.                                 |
| `browser-agent.md`    | Deep context: architecture, decisions, current state, next steps.            |
| `.env.example`             | Secret template for both phases. Update whenever you add config.             |
