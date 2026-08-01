# Sentinel

This repository holds **two independent projects** that happen to share a repo.
They do not import or depend on each other — pick whichever you need.

| Project | Folder | What it does |
|---|---|---|
| **Sentinel** (restock alerts) | repo root (`scraper.py`) | Watches a Discord channel and forwards matching restock posts to Telegram. |
| **Browser Research Agent** | [`browser-agent/`](browser-agent/) | An AI agent that drives a real Chromium browser to research the web for you. |

---

## Repository layout

```
Sentinel/
├── scraper.py            # Sentinel: Discord -> Telegram monitor
├── requirements.txt      # Sentinel deps
├── .env.example          # Sentinel secrets template  (copy to .env)
├── .gitignore
├── README.md             # you are here
├── AGENTS.md             # guidance for AI coding agents (Codex etc.)
└── browser-agent/        # Browser Research Agent (self-contained sub-project)
    ├── agent.py
    ├── requirements.txt
    ├── .env.example
    ├── README.md
    └── HANDOFF.md        # full handoff / architecture notes
```

Both projects read their secrets from a local **`.env`** file (git-ignored). No
real tokens or keys live in source, so the repo is safe to publish.

---

# Project 1 — Sentinel (Discord → Telegram restock alerts)

Watches one Discord channel and, for every matching message, sends a formatted
Telegram alert — **item, price, store, purchase limit**, plus link, SKU, and any
extra fields it finds — to as many recipients as you list. It only makes
outbound calls (to Discord and Telegram): no server to host, no port to open.

### ⚠️ Before you run or share it

This uses `discord.py-self`, which automates a normal **user account**. That
**violates Discord's Terms of Service** and can get the account permanently
banned. The ToS-compliant alternative is a real **bot account** (plain
`discord.py`), but a bot can only read channels in servers where you can invite
it. Know the risk before running or sharing.

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

### Setup

Requires Python 3.10+.

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt    # if you have plain discord.py installed, pip uninstall it first — it conflicts

cp .env.example .env               # then fill in the values (see below)
```

Fill in `.env`:

| Variable | How to get it |
|---|---|
| `DISCORD_TOKEN` | Your Discord **user** token. |
| `TARGET_CHANNEL_ID` | Enable Developer Mode in Discord → right-click the channel → *Copy ID*. |
| `TELEGRAM_BOT_TOKEN` | Create a bot with [@BotFather](https://t.me/BotFather). |
| `TELEGRAM_CHAT_IDS` | Comma-separated. Have each recipient message the bot once, then read `chat.id` from `https://api.telegram.org/bot<TOKEN>/getUpdates`. |

Non-secret behavior knobs stay at the top of [`scraper.py`](scraper.py):
`TITLE_KEYWORDS` (only alert on these pack names; `[]` = all) and
`REQUIRE_EMBED` (skip ping-only text lines).

### Run

```bash
python scraper.py
```

You'll see `--- Sentinel Monitor Online ---`. If any required variable is
missing, it exits immediately telling you which. A failed send to one recipient
is logged and skipped — it won't block the others.

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
ExecStart=/home/pi/sentinel/.venv/bin/python scraper.py
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

# Project 2 — Browser Research Agent

An AI agent that drives a **real Chromium browser** to research the web. Claude
decides what to do; [Playwright MCP](https://github.com/microsoft/playwright-mcp)
is the "hands" that navigate, click, and read pages. Built as a **raw Anthropic
Messages API tool-use loop** (no higher-level framework) for full control over
the loop, prompting, and guardrails.

```
agent.py + Claude  ──tool call──►  Playwright MCP  ──CDP──►  Chromium (headed)
     (the loop)    ◄──a11y tree──   (Node subproc)  ◄──────   separate process
```

Full setup, run instructions, architecture, and a complete handoff doc live in
the sub-project:

- **[browser-agent/README.md](browser-agent/README.md)** — setup & run
- **[browser-agent/HANDOFF.md](browser-agent/HANDOFF.md)** — architecture, decisions, current state, next steps

Quick start:

```bash
cd browser-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env               # paste your ANTHROPIC_API_KEY
npx playwright install chromium    # one-time browser download
python agent.py "Find the top 3 papers on retrieval-augmented generation and summarize each"
```

---

## Security notes

- **Never commit `.env`.** It's git-ignored in both the repo root and
  `browser-agent/`. Only `.env.example` templates are tracked.
- If a token is ever exposed, rotate it: Discord (reset password),
  Telegram (`/revoke` via @BotFather), Anthropic (revoke the key in the console).
- The browser agent drives a real browser with network access — review the
  guardrails in `browser-agent/agent.py` before pointing it at sensitive sites.
