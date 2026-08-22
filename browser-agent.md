# Browser Research Agent — architecture & handoff

> **For the next Claude / developer picking this up.** This document is fully
> self-contained: it captures what this phase is, every decision made and why,
> exactly what state it's in, and what to do next. You do not need the original
> chat history.

---

## 1. What this is

Phase 2 of Sentinel: an AI agent that drives a **real Chromium browser** to
research the web. It lives in `src/browser_agent.py`.

Today it runs standalone from the CLI. The plan is for Phase 1
(`src/scraper.py`) to eventually drive it — a restock hit triggers a
research task — which is why both phases share one package, one venv, and one
`.env`.

- **Claude** decides what to do (navigate, click, read, extract).
- **[Playwright MCP](https://github.com/microsoft/playwright-mcp)** is the "hands"
  — it exposes browser actions as tools and executes them in Chromium.
- The connective tissue is a **raw Anthropic Messages API tool-use loop** (we
  deliberately did NOT use a higher-level framework — see decisions).

## 2. Architecture (three layers)

```
┌──────────────────┐   MCP/JSON-RPC   ┌──────────────────┐   CDP over    ┌──────────────┐
│ browser_agent.py │   over stdio     │ @playwright/mcp  │   WebSocket   │  Chromium    │
│    + Claude      │ ───────────────► │ (Node subprocess)│ ────pipe────► │  (bundled)   │
│     (the loop)   │ ◄─────────────── │ = Playwright lib │ ◄──────────── │  headed win  │
└──────────────────┘  tool results    └──────────────────┘   DOM events  └──────────────┘
     Python                                Node.js                        separate process
```

1. **browser_agent.py ↔ MCP server** — JSON-RPC over the subprocess's stdin/stdout
   (set up by `stdio_client`). Operations: `list_tools`, `call_tool`.
2. **MCP server ↔ Chromium** — `@playwright/mcp` is a thin Node wrapper around the
   Playwright library. Each tool call becomes a Playwright call (`page.goto`,
   `locator.click`, …), which talks to Chromium over the **Chrome DevTools
   Protocol (CDP)**.
3. **Chromium** — a separate OS process that Playwright launches. It is **NOT** the
   user's Google Chrome; Playwright ships its own pinned Chromium build cached in
   `~/Library/Caches/ms-playwright/` (macOS), downloaded separately from the npm
   package.

**Key runtime fact:** the browser binary launches only on the first
`browser_navigate`, NOT when tools are listed. Page state is returned to Claude as
an **accessibility tree** (structured text with element `ref`s), not screenshots —
cheaper and more reliable than vision-based clicking.

## 3. Decisions made (and why)

| Decision            | Choice                                                      | Why                                                                                                                                                                               |
| ------------------- | ----------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Overall approach    | Playwright MCP + own agent loop (vs. `browser-use` library) | Full control over the loop, prompting, guardrails, model choice. `browser-use` bundles its own loop/prompt and is harder to customize. Same architecture Claude Code itself uses. |
| Agent loop          | **Raw Anthropic Messages API** (vs. Claude Agent SDK)       | User explicitly chose maximum control / to see the mechanics, accepting more boilerplate.                                                                                         |
| Browser mode        | **Headed** (visible window)                                 | User wants to watch it work; best for learning/debugging. Toggle to headless by removing `"--headed"` from args in `run_agent()`.                                                 |
| Page representation | Accessibility tree (Playwright MCP default)                 | Faster/cheaper/more reliable than screenshots.                                                                                                                                    |
| Model default       | `claude-sonnet-5`                                           | Good balance for tool-use loops. `MODEL` constant at the top of the module.                                                                                                       |
| Repo layout         | Flat `src/`, one venv, one `.env` (was two sub-projects)    | The two phases are meant to connect; both scripts sitting in `src/` makes `from browser_agent import run_agent` trivial and removes duplicate config.                             |

## 4. Current state — what's DONE vs PENDING

### Done ✅

- `src/browser_agent.py` — full working loop + guardrails (details below).
- Deps merged into the root `requirements.txt` and installed into the root `.venv`.
- **Verified:** Playwright MCP subprocess launches and exposes all 24 browser
  tools; the specific names the code references (`browser_click`,
  `browser_snapshot`, `browser_navigate`, `browser_type`, `browser_press_key`)
  all exist.
- **Chromium downloaded** — Chrome for Testing 151.0.7922.34 cached in
  `~/Library/Caches/ms-playwright/chromium-1234` (plus headless shell + ffmpeg).
- Committed to git and restructured into the unified layout.

### Pending ⏳

1. **ANTHROPIC_API_KEY not set** — no `.env` yet. BLOCKS any live run.
   `cp .env.example .env` and paste a key from
   https://console.anthropic.com/settings/keys.
2. **No live end-to-end run yet** — never executed a real task (blocked on #1).

### Known intentional deviation ⚠️

The user **deliberately removed** the purchase/login guardrails for testing:

- `RISKY_KEYWORDS` no longer contains `buy`, `purchase`, `checkout`, `pay`,
  `order`, `place order`, `sign in`, `log in`.
- The system-prompt line *"Do NOT attempt to log in, purchase, submit forms, or
  post content"* was deleted.

The agent will therefore **not** pause before checkout or sign-in flows. This is
a known choice, not a bug — don't silently revert it, but do restore it before
any unattended run.

## 5. Environment (verified on the original machine)

- macOS (Darwin, arm64). Repo: `/Users/gupta/Documents/GitHub/Sentinel`, branch `main`.
- Node v22.13.1, npx 10.9.2 (Playwright MCP needs Node 18+).
- Python 3.14.6 in the single root `.venv`.
- `@playwright/mcp` cached under `~/.npm/_npx/`.
- **If moving to a new machine:** re-run the setup in §6 from scratch (venv,
  pip install, playwright install, .env). None of the caches transfer.

## 6. How to set up & run (from scratch)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env               # paste ANTHROPIC_API_KEY into .env

npx playwright install chromium    # one-time browser download (~180 MB)

python src/browser_agent.py "Find the top 3 papers on retrieval-augmented generation and summarize each"
```

A Chromium window opens so you can watch. With no CLI arg it prompts for a task.

## 7. Internals (so you can modify confidently)

- `run_agent(goal)` — async entrypoint. Spawns `npx -y @playwright/mcp@latest
  --headed` via `StdioServerParameters` + `stdio_client`, opens a `ClientSession`,
  `initialize()`, `list_tools()`.
- `mcp_tools_to_anthropic()` — maps MCP tool schema → Messages API `tools` format.
- **The loop** (`for step in range(1, MAX_STEPS+1)`): call `client.messages.create`
  with `tools` + `messages`; append assistant content; if `stop_reason != "tool_use"`
  print the answer and return; else execute each `tool_use` block via
  `session.call_tool`, wrap results with `mcp_result_to_content`, append as a
  `user` turn, repeat.
- `mcp_result_to_content()` — converts MCP results to tool_result blocks; text→text,
  images (screenshots)→base64 image blocks.
- **Guardrails:**
  - `MAX_STEPS` (default 40) — hard cap against runaway loops.
  - `ALLOWED_DOMAINS` (default `[]` = allow all) — non-allowlisted `browser_navigate`
    prompts y/N on the terminal.
  - `RISKY_KEYWORDS` — clicks/types on matching controls pause for y/N
    confirmation. See the deviation note in §4 for what was removed.

## 8. Suggested next steps (discussed, not yet built)

1. **Persistent browser profile** — Playwright MCP supports `--user-data-dir=...`
   (persistent context) so cookies/logins survive across runs, letting the agent
   research behind sites you've logged into once. Default is a fresh throwaway
   profile each run.
2. **Headless toggle** — expose `--headed`/headless as a CLI flag or env var.
3. **Save output to a report file** (e.g. Markdown) instead of just stdout.
4. **Wire Phase 1 → Phase 2** — have `scraper.py` call `run_agent()` on a restock
   hit. This is the reason for the shared package layout.

## 9. Safety notes for whoever runs this

- The agent drives a real browser with real network access. The guardrails in §7
  are the main protection — and per §4 the purchase/login ones are currently off.
  Keep task prompts narrowly scoped.
- Never hardcode the API key — keep it in the git-ignored root `.env`.
- Sites with bot detection (Target, ticketing, most checkout flows) may challenge
  or block a bare Playwright Chromium; that's a separate hurdle from the guardrails.
