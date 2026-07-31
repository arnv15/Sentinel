# Project Handoff — Browser Research Agent

> **For the next Claude / developer picking this up.** This document is fully
> self-contained: it captures what this project is, every decision made and why,
> exactly what state it's in, and what to do next. You do not need the original
> chat history.

---

## 1. What this project is

A standalone AI agent that drives a **real Chromium browser** to research the web.
It lives in the `browser-agent/` subfolder of the `Sentinel` repo but is
**completely unrelated to Sentinel** (Sentinel is a Discord→Telegram restock
alert bot; this folder just happens to share the repo).

- **Claude** decides what to do (navigate, click, read, extract).
- **[Playwright MCP](https://github.com/microsoft/playwright-mcp)** is the "hands"
  — it exposes browser actions as tools and executes them in Chromium.
- The connective tissue is a **raw Anthropic Messages API tool-use loop** in
  `agent.py` (we deliberately did NOT use a higher-level framework — see decisions).

## 2. Architecture (three layers)

```
┌─────────────┐   MCP/JSON-RPC   ┌──────────────────┐   CDP over    ┌──────────────┐
│  agent.py   │   over stdio     │ @playwright/mcp  │   WebSocket   │  Chromium    │
│  + Claude   │ ───────────────► │ (Node subprocess)│ ────pipe────► │  (bundled)   │
│  (the loop) │ ◄─────────────── │ = Playwright lib │ ◄──────────── │  headed win  │
└─────────────┘  tool results    └──────────────────┘   DOM events  └──────────────┘
     Python                          Node.js                         separate process
```

1. **agent.py ↔ MCP server** — JSON-RPC over the subprocess's stdin/stdout
   (set up by `stdio_client`). Operations: `list_tools`, `call_tool`.
2. **MCP server ↔ Chromium** — `@playwright/mcp` is a thin Node wrapper around the
   Playwright library. Each tool call becomes a Playwright call (`page.goto`,
   `locator.click`, …), which talks to Chromium over the **Chrome DevTools
   Protocol (CDP)**.
3. **Chromium** — a separate OS process that Playwright launches. It is **NOT** the
   user's Google Chrome; Playwright ships its own pinned Chromium build cached in
   `~/Library/Caches/ms-playwright/` (macOS). ~150 MB, downloaded separately from
   the npm package.

**Key runtime fact:** the browser binary is downloaded/launched only on the first
`browser_navigate`, NOT when tools are listed. Page state is returned to Claude as
an **accessibility tree** (structured text with element `ref`s), not screenshots —
cheaper and more reliable than vision-based clicking.

## 3. Decisions made (and why)

| Decision | Choice | Why |
|---|---|---|
| Overall approach | Playwright MCP + own agent loop (vs. `browser-use` library) | Full control over the loop, prompting, guardrails, model choice. `browser-use` bundles its own loop/prompt and is harder to customize. Same architecture Claude Code itself uses. |
| Agent loop | **Raw Anthropic Messages API** (vs. Claude Agent SDK) | User explicitly chose maximum control / to see the mechanics, accepting more boilerplate. |
| Browser mode | **Headed** (visible window) | User wants to watch it work; best for learning/debugging. Toggle to headless by removing `"--headed"` from args in `run_agent()`. |
| Page representation | Accessibility tree (Playwright MCP default) | Faster/cheaper/more reliable than screenshots. |
| Model default | `claude-sonnet-5` | Good balance for tool-use loops. `MODEL` constant at top of agent.py. |

## 4. Current state — what's DONE vs PENDING

### Done ✅
- `agent.py` — full working loop + guardrails (details below).
- `requirements.txt` — `anthropic`, `mcp`, `python-dotenv`.
- `.env.example`, `README.md`.
- `browser-agent/.venv/` created and the 3 deps pip-installed into it.
- **Verified:** Playwright MCP subprocess launches and exposes all 24 browser
  tools; the specific names the code references (`browser_click`,
  `browser_snapshot`, `browser_navigate`, `browser_type`, `browser_press_key`)
  all exist.

### Pending ⏳ (nothing blocks these except the two marked)
1. **ANTHROPIC_API_KEY not set** — no `.env` yet. BLOCKS any live run.
   Create `.env` from `.env.example`, paste a key from
   https://console.anthropic.com/settings/keys.
2. **Chromium not downloaded** — `~/Library/Caches/ms-playwright/` is empty.
   Run `npx playwright install chromium` (~150 MB), or let the first navigate
   auto-download it (stalls ~1 min with no message).
3. **No live end-to-end run yet** — never executed a real task (blocked on #1/#2).
4. **Not committed to git** — all files are untracked new files.

## 5. Environment (verified on the original machine)

- macOS (Darwin). Repo: `/Users/gupta/Documents/GitHub/Sentinel`, branch `main`.
- Node v22.13.1, npx 10.9.2 (Playwright MCP needs Node 18+).
- Python 3.14 (repo `.venv`); a dedicated `browser-agent/.venv` was created for this project.
- `@playwright/mcp` already cached under `~/.npm/_npx/`.
- **If moving to a new machine:** re-run the setup in §6 from scratch (venv,
  pip install, playwright install, .env). None of the caches transfer.

## 6. How to set up & run (from scratch)

```bash
cd browser-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # paste ANTHROPIC_API_KEY into .env

npx playwright install chromium   # one-time browser download (~150 MB)

python agent.py "Find the top 3 papers on retrieval-augmented generation and summarize each"
```

A Chromium window opens so you can watch. With no CLI arg it prompts for a task.

## 7. agent.py internals (so you can modify confidently)

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
  - `RISKY_KEYWORDS` — clicks/types on controls labeled buy/checkout/log in/submit/
    delete/etc. pause for y/N confirmation, so it won't purchase/log in/submit
    unattended.
  - System prompt also instructs Claude not to log in, buy, or post.

## 8. Suggested next steps (discussed, not yet built)

1. **Persistent browser profile** — Playwright MCP supports `--user-data-dir=...`
   (persistent context) so cookies/logins survive across runs, letting the agent
   research behind sites you've logged into once. Default is a fresh throwaway
   profile each run.
2. **Headless toggle** — expose `--headed`/headless as a CLI flag or env var.
3. **Save output to a report file** (e.g. Markdown) instead of just stdout.
4. **Commit the project** to git once a key is confirmed working.

## 9. Safety notes for whoever runs this

- The agent drives a real browser with real network access. The guardrails in §7
  are the main protection; review them before pointing it at sensitive sites.
- Never hardcode the API key in `agent.py` — keep it in `.env` (already gitignore-
  worthy; add a `.gitignore` if committing).
- Don't let it authenticate, purchase, or submit forms unattended; that's what the
  `RISKY_KEYWORDS` confirmation is for.
