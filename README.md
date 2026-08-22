# Sentinel

Two phases of one project. They run independently today and will connect later:
the scraper spots a restock, then hands the browser agent a research task.

| Phase                          | Module                    | What it does                                                             |
| ------------------------------ | ------------------------- | ------------------------------------------------------------------------ |
| **1 — Scraper**                | `src/scraper.py`      | Watches a Discord channel, forwards matching restock posts to Telegram.  |
| **2 — Browser Research Agent** | `src/browser_agent.py`| Drives a real Chromium browser to research the web for you.              |

---

## Repository layout

```
Sentinel/
├── README.md            # you are here
├── AGENTS.md            # guidance for AI coding agents
├── browser-agent.md     # Phase 2 architecture, decisions, current state
├── requirements.txt     # all deps, one virtualenv
├── .env.example         # secrets template — copy to .env
├── .gitignore
├── src/
│   ├── scraper.py       # Phase 1
│   └── browser_agent.py # Phase 2
└── mock-store/          # static fake storefront for testing the agent
```

---

## Setup (once, covers both phases)

Requires **Python 3.10+**, plus **Node 18+** if you're running Phase 2.

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt    # if plain discord.py is installed, pip uninstall it first — it conflicts

cp .env.example .env               # then fill in the values you need
```

| Variable             | Phase | How to get it                                                                                                                         |
| -------------------- | ----- | ------------------------------------------------------------------------------------------------------------------------------------- |
| `DISCORD_TOKEN`      | 1     | Your Discord **user** token.                                                                                                          |
| `TARGET_CHANNEL_ID`  | 1     | Enable Developer Mode in Discord → right-click the channel → _Copy ID_.                                                               |
| `TELEGRAM_BOT_TOKEN` | 1     | Create a bot with [@BotFather](https://t.me/BotFather).                                                                               |
| `TELEGRAM_CHAT_IDS`  | 1     | Comma-separated. Have each recipient message the bot once, then read `chat.id` from `https://api.telegram.org/bot<TOKEN>/getUpdates`. |
| `LLM_PROVIDER`       | 2     | Which model drives the agent. Defaults to `gemini`.                                                                                   |
| `GEMINI_API_KEY`     | 2     | **Free**, no credit card: [aistudio.google.com/apikey](https://aistudio.google.com/apikey)                                             |

One `.env` serves both phases — leave a section blank if you only run one.

---

# Phase 1 — Scraper (Discord → Telegram restock alerts)

Watches one Discord channel and, for every matching message, sends a formatted
Telegram alert — **item, price, store, purchase limit**, plus link, SKU, and any
extra fields it finds — to as many recipients as you list. It only makes
outbound calls (to Discord and Telegram): no server to host, no port to open.

### ⚠️ Before you run or share it

This uses `discord.py-self`, which automates a normal **user account**. That
**violates Discord's Terms of Service** and can get the account permanently
banned. The ToS-compliant alternative is a real **bot account** (plain
`discord.py`), but a bot can only read channels in servers where you can invite
it. The user has acknowledged this risk when asking you to build it, and wants to use it for testing purposes.

### What an alert looks like

```
🔔 Restock Alert

🛍 Item:  Pokemon TCG: Mega Evolution — Phantasmal Flames Elite Trainer Box
💰 Price: $59.99
📦 Stock: 10
🏬 Store: Target
🔢 Limit: 2
🏷 SKU:   94860231
🔗 Link:  https://www.target.com/p/-/A-94860231
```

The parser is tuned to embed-style monitor posts (Rattle Pokemon Target/Walmart
ads): **store** from the embed author, **item** from the linked title, and
**price / stock / limit / SKU** from the embed fields. It falls back to scraping
message text for free-form posts. Only the main product link is included; the
affiliate row is dropped.

### Run

```bash
python src/scraper.py
```

You'll see `--- Sentinel Monitor Online ---`. If any required variable is
missing, it exits immediately telling you which. A failed send to one recipient
is logged and skipped — it won't block the others.

Non-secret behavior knobs stay at the top of `src/scraper.py`:
`TITLE_KEYWORDS` (only alert on these pack names; `[]` = all) and
`REQUIRE_EMBED` (skip ping-only text lines).

### Running on a Raspberry Pi Zero 2 W

Runs fine on a Zero 2 W (~80–150 MB RAM). Flash the **64-bit** OS (arm64) for
prebuilt wheels, and run it as a systemd service so it restarts on reboot:

```ini
# /etc/systemd/system/sentinel.service  (adjust User and paths)
[Unit]
Description=Sentinel Discord->Telegram monitor
After=network-online.target
Wants=network-online.target

[Service]
User=pi
WorkingDirectory=/home/pi/sentinel
ExecStart=/home/pi/sentinel/.venv/bin/python src/scraper.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now sentinel
journalctl -u sentinel -f
```

To add recipients, append chat IDs to `TELEGRAM_CHAT_IDS` in `.env`. The Discord
token belongs to one account — for a shared setup, one person runs the monitor
and everyone else is just a Telegram recipient.

---

# Phase 2 — Browser Research Agent

An AI agent that drives a **real Chromium browser** to research the web. The
model decides what to do; [Playwright MCP](https://github.com/microsoft/playwright-mcp)
is the "hands" that navigate, click, and read pages. Built as a **raw tool-use
loop** (no higher-level framework) for full control over the loop, prompting,
and guardrails.

### Choosing a model provider

The loop is provider-agnostic. Everything except Anthropic speaks the OpenAI
Chat Completions format, so switching is a `.env` change, not a code change:

| `LLM_PROVIDER` | Cost | Key |
| --- | --- | --- |
| `gemini` *(default)* | **Free tier** | `GEMINI_API_KEY` — [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| `groq` | Free tier, fastest | `GROQ_API_KEY` |
| `cerebras` | Free tier, high throughput | `CEREBRAS_API_KEY` |
| `openrouter` | Some free models | `OPENROUTER_API_KEY` |
| `github` | Free w/ GitHub account | `GITHUB_TOKEN` |
| `ollama` | Fully local | none |
| `anthropic` | Paid | `ANTHROPIC_API_KEY` |

> **Note:** a Google AI Pro / Gemini Advanced subscription does **not** include
> API access — that's a consumer chat plan. The API key is separate and its free
> tier is open to any Google account. On the free tier Google uses submitted
> content to improve its products, so keep free-tier runs pointed at test
> fixtures like MockMart rather than anything sensitive.

```
┌──────────────────┐  tool call   ┌──────────────┐  Playwright   ┌──────────┐
│ browser_agent.py │ ───────────► │ Playwright   │ ────────────► │ Chromium │
│   + your model   │              │ MCP server   │               │ (headed) │
│     (loop)       │ ◄─────────── │ (npx subproc)│ ◄──────────── │          │
└──────────────────┘  a11y tree   └──────────────┘   DOM/a11y     └──────────┘
```

1. `browser_agent.py` spawns `npx @playwright/mcp` as a subprocess (MCP over stdio).
2. That server exposes browser actions as tools — `browser_navigate`,
   `browser_click`, `browser_type`, `browser_snapshot`, etc.
3. We hand those tools to the configured model. It reads the page as an
   **accessibility tree** (structured text, not screenshots), picks an action,
   and we execute it.
4. Repeat until the model has the answer. There is no AI in the MCP server — all
   the reasoning is the single model call inside `run_agent()`.

### One-time browser download

```bash
npx playwright install chromium    # ~180 MB, cached in ~/Library/Caches/ms-playwright
```

### Run

```bash
python src/browser_agent.py "Find the top 3 papers on retrieval-augmented generation and summarize each"
```

Launch with no argument and it'll prompt you for a task. A Chromium window opens
so you can watch it work.

### Guardrails (in `src/browser_agent.py`)

- **`MAX_STEPS`** — hard cap on tool calls (default 40), so it can't loop forever.
- **`ALLOWED_DOMAINS`** — empty by default (any site). Add hostnames to restrict;
  navigating off-list pauses for your y/N confirmation.
- **`RISKY_KEYWORDS`** — before any click/type on a control whose label matches,
  it stops and asks you on the terminal.

> **Note:** the purchase/login terms (`buy`, `checkout`, `pay`, `order`,
> `sign in`, `log in`) and the system-prompt line forbidding logins and purchases
> were **intentionally removed for testing**. As it stands the agent can proceed
> through checkout and sign-in flows without pausing. Keep task prompts narrowly
> scoped, and restore those entries before any unattended run.

### Tuning

- **Model** — set `LLM_PROVIDER` and optionally `LLM_MODEL` in `.env`; defaults
  per provider live in the `PROVIDERS` table at the top of `browser_agent.py`.
  `LLM_BASE_URL` points at any other OpenAI-compatible endpoint.
- **Headless** — remove `"--headed"` from the `args` list in `run_agent()` to run
  with no visible window (for servers / unattended use).
- **More tools** — Playwright MCP has flags for tabs, file uploads, PDF, etc.
  See its docs; any tool it exposes is automatically offered to Claude.

### Testing it safely — MockMart

Pointing the agent at a real retailer's checkout risks placing real orders, so
the repo ships **[mock-store/](mock-store/)** — a static fake storefront with the
full browse → cart → checkout → confirmation flow, 40 products, no backend, and a
checkout form that submits nowhere.

```bash
cd mock-store && python3 -m http.server 8000
```

```bash
python src/browser_agent.py "Go to http://localhost:8000, add a Prismatic Evolutions Elite Trainer Box to the cart, and report the cart total"
```

It's also **live at https://arnv15.github.io/Sentinel/mock-store/**, so the agent
can hit a real URL with no local server running. See
[mock-store/README.md](mock-store/README.md) for the built-in test scenarios —
purchase limits, out-of-stock dead ends, empty cart, search misses.

Deeper context — architecture, every decision and why, current state, next steps
— lives in **[browser-agent.md](browser-agent.md)**.

---

## Security notes

- **Never commit `.env`.** It's git-ignored; only `.env.example` is tracked.
- If a token is ever exposed, rotate it: Discord (reset password),
  Telegram (`/revoke` via @BotFather), Anthropic (revoke the key in the console).
- The browser agent drives a real browser with network access — review the
  guardrails above before pointing it at sensitive sites.
