# Browser Research Agent

An AI agent that drives a **real Chromium browser** to research the web for you.
Claude decides what to do; [Playwright MCP](https://github.com/microsoft/playwright-mcp)
is the "hands" that actually navigate, click, and read pages.

## How it works

```
┌──────────┐  tool call   ┌──────────────┐  Playwright   ┌──────────┐
│ agent.py │ ───────────► │ Playwright   │ ────────────► │ Chromium │
│ + Claude │              │ MCP server   │               │ (headed) │
│  (loop)  │ ◄─────────── │ (npx subproc)│ ◄──────────── │          │
└──────────┘  a11y tree   └──────────────┘   DOM/a11y     └──────────┘
```

1. `agent.py` spawns `npx @playwright/mcp` as a subprocess (MCP over stdio).
2. That server exposes browser actions as tools — `browser_navigate`,
   `browser_click`, `browser_type`, `browser_snapshot`, etc.
3. We hand those tools to the Claude Messages API. Claude reads the page as an
   **accessibility tree** (structured text, not screenshots), picks an action,
   and we execute it.
4. Repeat until Claude has the answer. There is no AI in the MCP server — all
   the reasoning is the Claude call inside `run_agent()`.

## Setup

Requires **Node 18+** (for `npx`) and **Python 3.10+**.

```bash
cd browser-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env        # then paste your ANTHROPIC_API_KEY into .env
```

The first run downloads the Playwright browser binaries automatically. If it
complains, run once: `npx playwright install chromium`.

## Run

```bash
python agent.py "Find the three most-cited papers on retrieval-augmented generation and summarize each"
```

or launch with no argument and it'll prompt you for a task. A Chromium window
opens so you can watch it work.

## Guardrails (in `agent.py`)

- **`MAX_STEPS`** — hard cap on tool calls, so it can't loop forever.
- **`ALLOWED_DOMAINS`** — empty by default (any site). Add hostnames to restrict;
  navigating off-list pauses for your y/N confirmation.
- **`RISKY_KEYWORDS`** — before any click/type on a control labeled *buy, checkout,
  submit, log in, delete,* etc., it stops and asks you. This keeps the agent from
  purchasing, logging in, or submitting forms unattended.
- The system prompt also tells Claude not to log in, buy, or post.

## Tuning

- **Model** — `MODEL` at the top of `agent.py`. `claude-sonnet-5` (default) is a
  good balance; `claude-opus-4-8` for hardest reasoning; `claude-haiku-4-5` for speed.
- **Headless** — remove `"--headed"` from the `args` list in `run_agent()` to run
  with no visible window (for servers / unattended use).
- **More tools** — Playwright MCP has flags for tabs, file uploads, PDF, etc.
  See its docs; any tool it exposes is automatically offered to Claude.
```
