"""
Sentinel — Discord Channel Monitor -> Telegram Alerts
=====================================================

Watches a single Discord channel and, for each matching message, parses out the
item, price, store, stock, purchase limit (plus link / SKU) and sends a
formatted alert to a list of Telegram recipients.

Tuned for embed-style monitor posts (e.g. "Rattle Pokemon" Target/Walmart
restock ads), but falls back to scraping free-form text when needed.

It only makes OUTBOUND calls (to Discord and Telegram), so there is no server to
host and nothing to expose to the internet. Light enough to run on a Raspberry
Pi Zero 2 W (see the README for the systemd service).

------------------------------------------------------------------------------
NOTE ON ACCOUNTS
------------------------------------------------------------------------------
This uses `discord.py-self`, which automates a normal USER account. That
violates Discord's Terms of Service and can get the account banned. The
compliant alternative is a real bot account (plain `discord.py`), usable only
in servers where you can invite a bot. Know the risk before running or sharing.
"""

import asyncio
import html
import os
import re
import sys
from urllib.parse import urlparse

import aiohttp
import discord
from discord.ext import commands

# Load secrets from a local .env file if present (pip install python-dotenv).
# This keeps tokens out of the source so the file is safe to commit/push.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# ==============================================================================
# CONFIG
# ==============================================================================
# Secrets come from the environment (see .env.example). Never hardcode them here.
TOKEN = os.environ.get("DISCORD_TOKEN", "")
# `or 0` matters: a present-but-empty TARGET_CHANNEL_ID= in .env makes
# int("") raise at import, crashing before _check_config can explain why.
TARGET_CHANNEL_ID = int(os.environ.get("TARGET_CHANNEL_ID", "0") or 0)  # channel to monitor

# Only alert when the item TITLE contains one of these pack names
# (case-insensitive). These are the valuable sets worth buying; everything
# else in the channel is ignored. Leave the list EMPTY ([]) to alert on all.
TITLE_KEYWORDS = [
    "prismatic evolution",
    "destined rivals",
    "phantasmal flames",
]

# The monitor puts all the useful data in an embed. Ping-only text lines
# (e.g. "[ @walmart ] [QUEUE] ... @ $15.87") usually duplicate an embed, so we
# skip messages that have no embed by default. Set to False to alert on those too.
REQUIRE_EMBED = True

# Telegram bot + recipients. The bot token comes from the environment; the
# recipient chat IDs are a comma-separated list in TELEGRAM_CHAT_IDS.
# (Get a chat ID by messaging your bot and checking
# https://api.telegram.org/bot<TOKEN>/getUpdates)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_IDS = [
    cid.strip()
    for cid in os.environ.get("TELEGRAM_CHAT_IDS", "").split(",")
    if cid.strip()
]

# ----- Phase 2 hand-off: fire the browser agent on a match -----
# All off by default: with AGENT_ENABLED unset this file behaves exactly as it
# always has, and never imports the Phase 2 dependencies (openai / mcp).


def _agent_flag(name, default):
    raw = os.environ.get(name, "").strip().lower()
    if raw in ("1", "true", "yes"):
        return True
    if raw in ("0", "false", "no"):
        return False
    return default


def _agent_num(name, default, cast=float):
    try:
        raw = os.environ.get(name, "").strip()
        return cast(raw) if raw else default
    except ValueError:
        return default


# ----- Where alerts get delivered -----
# Set either (or both). A Discord webhook needs no bot and no account: in your
# server, Settings -> Integrations -> Webhooks -> New Webhook -> Copy URL.
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()


AGENT_ENABLED = _agent_flag("AGENT_ENABLED", False)

# Where the agent is pointed.
#   fixture — the MockMart test storefront. The real product link from the alert
#             is NOT put in the prompt, so the agent can't wander onto a real
#             retailer. This is the default.
#   live    — the actual product URL from the alert.
AGENT_TARGET_MODE = os.environ.get("AGENT_TARGET_MODE", "fixture").strip().lower()
AGENT_FIXTURE_URL = os.environ.get(
    "AGENT_FIXTURE_URL", "https://arnv15.github.io/Sentinel/mock-store/"
)

# Single-flight + cooldown. A restock burst should NOT queue up a backlog of
# runs about items that sold out twenty minutes ago — extra triggers are dropped.
AGENT_COOLDOWN_SECONDS = _agent_num("AGENT_COOLDOWN_SECONDS", 300.0)
AGENT_TIMEOUT_SECONDS = _agent_num("AGENT_TIMEOUT_SECONDS", 600.0)

# One step is roughly one API request. Gemini's free tier is ~1500/day, so the
# CLI default of 40 would cap you near 37 runs/day. 15 keeps some headroom.
AGENT_MAX_STEPS = _agent_num("AGENT_MAX_STEPS", 15, int)
AGENT_MAX_RUNS_PER_DAY = _agent_num("AGENT_MAX_RUNS_PER_DAY", 20, int)

AGENT_HEADLESS = _agent_flag("AGENT_HEADLESS", False)   # headed: watch it work

# Seconds to leave the browser window up after a bot-triggered run finishes, so
# you can see the end state. A TIMED hold, not the CLI's wait-for-Enter one:
# there is no terminal here, and blocking on stdin would pin the single-flight
# lock forever. Counts against AGENT_TIMEOUT_SECONDS.
AGENT_KEEP_OPEN_SECONDS = _agent_num("AGENT_KEEP_OPEN_SECONDS", 45.0)
AGENT_ANNOUNCE = _agent_flag("AGENT_ANNOUNCE", True)    # Telegram the prompt on start

# Optional prompt override. Placeholders: {item} {price} {store} {stock}
# {limit} {link} {sku} {target}. In fixture mode {link} is always empty.
AGENT_TASK_TEMPLATE = os.environ.get("AGENT_TASK_TEMPLATE", "").strip()

# ==============================================================================
# PARSING ENGINE  (pure functions — no Discord objects, so they're easy to test)
# ==============================================================================
_CURRENCY = r"(?:[$£€₹]|USD|GBP|EUR|CAD|AUD|INR|Rs\.?)"
_PRICE_RE = re.compile(rf"{_CURRENCY}\s?\d[\d,]*(?:\.\d{{1,2}})?", re.IGNORECASE)
_LIMIT_RE = re.compile(
    r"(?:order\s*limit|limit|max(?:imum)?|cap)\D{0,12}?(\d+)"
    r"|(\d+)\s*(?:per|/)\s*(?:customer|person|order|account|household)",
    re.IGNORECASE,
)
_SKU_RE = re.compile(
    r"\b(?:sku|upc|style|pid|product\s*id)\b[\s:#\-]*([A-Z0-9][A-Z0-9\-_/]{3,})",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://[^\s<>\"'\)\]]+", re.IGNORECASE)

# `[ @walmart ]`-style store tag at the start of a ping line (requires the @).
_CONTENT_STORE_RE = re.compile(r"\[\s*@([A-Za-z0-9._-]{2,30}?)\s*\]")
_PING_ID_RE = re.compile(r"\(?\s*ping\s*id[:\s]*\d+\s*\)?", re.IGNORECASE)

# Field-name buckets. Each field is assigned to the FIRST bucket it matches
# (checked in this order), so "Order Limit" -> limit, "Stock" -> stock, etc.
_FIELD_BUCKETS = [
    ("item", ("item", "product", "title")),
    ("price", ("price", "cost", "retail")),
    ("store", ("store", "site", "retailer", "shop", "merchant")),
    ("limit", ("order limit", "limit", "max", "per customer")),
    ("stock", ("stock", "quantity", "qty", "available")),
    ("sku", ("sku", "upc", "product id", "pid")),
]
# Field names we deliberately drop (affiliate links, task buttons, cart links).
_NOISE_KEYS = ("add to cart", "quick task", "links ad", "link ad", "links", "task", "atc")


def _store_from_url(url):
    """Turn a URL into a readable store name, e.g. shop.nike.com -> Nike."""
    try:
        host = urlparse(url).netloc.lower().split(":")[0]
    except ValueError:
        return None
    if not host:
        return None
    parts = [p for p in host.split(".") if p not in ("www", "shop", "store", "us", "uk")]
    if len(parts) >= 2:
        return parts[-2].capitalize()
    return parts[0].capitalize() if parts else None


def _store_from_author(author):
    """'Target - Item Restocked Ad:' -> 'Target'."""
    if not author:
        return None
    head = re.split(r"\s[-–|•]\s", author.strip(), maxsplit=1)[0].strip()
    return head or None


def _store_from_footer(footer):
    """'Rattle ... Monitors • Target • 05:34 AM EST' -> 'Target' (middle segment)."""
    if not footer:
        return None
    parts = [p.strip() for p in footer.split("•") if p.strip()]
    if len(parts) >= 3:
        return parts[1]
    return None


def _clean_content_item(content):
    """First non-empty content line, minus the '[ @store ]' tag and '(Ping ID: N)'."""
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        line = _CONTENT_STORE_RE.sub("", line, count=1)
        line = _PING_ID_RE.sub("", line)
        return line.strip(" ()").strip() or None
    return None


def _classify_fields(fields):
    """Sort embed fields into named buckets, dropping noise; return (buckets, extras)."""
    result = {name: None for name, _ in _FIELD_BUCKETS}
    extras = []
    for f in fields:
        name = (f.get("name") or "").strip()
        value = (f.get("value") or "").strip()
        if not name or not value:
            continue
        low = name.lower()
        if any(k in low for k in _NOISE_KEYS):
            continue
        placed = False
        for key, keys in _FIELD_BUCKETS:
            if result[key] is None and any(k in low for k in keys):
                result[key] = value
                placed = True
                break
        if not placed:
            extras.append((name, value))
    return result, extras


def parse_alert(content, embeds):
    """Extract item / price / store / stock / limit (+ extras) from a message.

    `embeds` is a list of plain dicts shaped like Discord embeds:
        {title, description, url, author, footer, fields:[{name, value}, ...]}
    Every key is optional. Structured embed fields win over text scraped from
    the free-form body, because they're reliable.
    """
    content = content or ""
    embeds = embeds or []

    text_parts = [content]
    all_fields, titles, authors, footers = [], [], [], []
    for e in embeds:
        if e.get("title"):
            titles.append(e["title"])
            text_parts.append(e["title"])
        if e.get("description"):
            text_parts.append(e["description"])
        if e.get("author"):
            authors.append(e["author"])
        if e.get("footer"):
            footers.append(e["footer"])
        for f in e.get("fields") or []:
            all_fields.append(f)
            text_parts.append(f"{f.get('name', '')} {f.get('value', '')}")
    text = "\n".join(p for p in text_parts if p)

    fld, extras = _classify_fields(all_fields)

    # Link: the embed title's URL (the product page) beats any loose URL in text.
    embed_urls = [e["url"] for e in embeds if e.get("url")]
    loose_urls = list(dict.fromkeys(_URL_RE.findall(text)))
    link = embed_urls[0] if embed_urls else (loose_urls[0] if loose_urls else None)

    # Item: field > embed title > cleaned content line.
    item = fld["item"] or (titles[0] if titles else None) or _clean_content_item(content)

    # Price: field > regex.
    price = fld["price"]
    if not price:
        m = _PRICE_RE.search(text)
        price = m.group(0) if m else None

    # Store: field > embed author > content '[ @x ]' tag > footer > URL domain.
    store = fld["store"]
    if not store:
        for a in authors:
            store = _store_from_author(a)
            if store:
                break
    if not store:
        m = _CONTENT_STORE_RE.search(content)
        if m:
            store = m.group(1).capitalize()
    if not store:
        for foot in footers:
            store = _store_from_footer(foot)
            if store:
                break
    if not store and link:
        store = _store_from_url(link)

    # Limit: field > regex ("Order Limit 2", "2 per customer", ...).
    limit = fld["limit"]
    if not limit:
        m = _LIMIT_RE.search(text)
        if m:
            limit = next((g for g in m.groups() if g), None)

    # Stock: field only (no reliable free-text pattern).
    stock = fld["stock"]

    # SKU: field > regex.
    sku = fld["sku"]
    if not sku:
        m = _SKU_RE.search(text)
        sku = m.group(1).upper() if m else None

    return {
        "item": item,
        "price": price,
        "store": store,
        "stock": stock,
        "limit": limit,
        "link": link,
        "sku": sku,
        "extras": extras,
    }


def format_telegram(parsed, jump_url):
    """Build the Telegram message body (HTML parse mode)."""

    def esc(v):
        return html.escape(str(v)) if v is not None else "—"

    # Headline = the item name, linked to the product page (the main link).
    item = parsed.get("item") or "Restock"
    link = parsed.get("link")
    if link:
        headline = f"🛒 <a href=\"{esc(link)}\"><b>{esc(item)}</b></a>"
    else:
        headline = f"🛒 <b>{esc(item)}</b>"

    lines = [headline, ""]
    lines.append(f"💰 <b>Price:</b> {esc(parsed.get('price'))}")
    if parsed.get("stock"):
        lines.append(f"📦 <b>Stock:</b> {esc(parsed['stock'])}")
    lines.append(f"🏬 <b>Store:</b> {esc(parsed.get('store'))}")
    lines.append(f"🔢 <b>Limit:</b> {esc(parsed.get('limit'))}")
    if parsed.get("sku"):
        lines.append(f"🏷 <b>SKU:</b> {esc(parsed['sku'])}")
    for name, value in parsed.get("extras", []):
        lines.append(f"• <b>{esc(name)}:</b> {esc(value)}")
    if jump_url:
        lines.append("")
        lines.append(f"💬 <a href=\"{esc(jump_url)}\">View in Discord</a>")
    return "\n".join(lines)


# ==============================================================================
# AGENT PROMPT BUILDING  (pure functions — no Discord objects, easy to test)
# ==============================================================================
_AGENT_FIELD_MAX = 120


def _clean_field(value, limit=_AGENT_FIELD_MAX):
    """
    Flatten one parsed field for safe inclusion in an LLM prompt.

    Embed text is written by whoever posts in the Discord channel, i.e. it is
    untrusted input heading straight into a prompt. Collapse to one line, strip
    URLs (so a planted link can't redirect the agent), and cap the length.
    """
    if not value:
        return ""
    text = " ".join(str(value).split())
    text = _URL_RE.sub("", text)
    text = text.strip()
    return text[:limit] + "…" if len(text) > limit else text


def build_agent_goal(parsed, *, mode=None, fixture_url=None, template=None):
    """
    Turn a parsed restock alert into the goal string handed to run_agent().

    Returns (goal, effective_mode). `effective_mode` may differ from `mode`:
    live mode falls back to fixture when the alert carried no usable link.
    """
    mode = (AGENT_TARGET_MODE if mode is None else mode).strip().lower()
    fixture_url = AGENT_FIXTURE_URL if fixture_url is None else fixture_url
    template = AGENT_TASK_TEMPLATE if template is None else template

    item = _clean_field(parsed.get("item")) or "the restocked item"
    price = _clean_field(parsed.get("price")) or "unknown"
    store = _clean_field(parsed.get("store")) or "unknown"
    stock = _clean_field(parsed.get("stock")) or "unknown"
    limit = _clean_field(parsed.get("limit")) or "unknown"
    sku = _clean_field(parsed.get("sku")) or "unknown"
    link = (parsed.get("link") or "").strip()

    if mode == "live" and link:
        target = link
    else:
        mode = "fixture"          # no link to visit -> fall back to the fixture
        target = fixture_url
        link = ""                 # never leak the real URL into a fixture prompt

    if template:
        from collections import defaultdict
        fields = defaultdict(str, item=item, price=price, store=store, stock=stock,
                             limit=limit, link=link, sku=sku, target=target)
        return template.format_map(fields), mode

    if mode == "live":
        goal = (
            f"Go to {target} and report what you find about this product: "
            f"the current price, whether it is in stock, and any purchase limit. "
            f"The restock alert claimed price {price}, stock {stock}, limit {limit}. "
            f"Say whether the page agrees with the alert, then stop."
        )
        return goal, mode

    # ---- fixture mode (the default) ----
    # The instruction the agent follows on a restock. This string becomes
    # run_agent()'s goal, and whatever the agent replies is sent to Telegram.
    # Available, already cleaned: item, price, store, stock, limit, sku, target
    # (`target` is the MockMart URL; `link` is deliberately empty in this mode).
    # Keep it focused — AGENT_MAX_STEPS is 15, and one step is ~one API call.
    instruction = (
        f"Go to {target} — a test storefront. Find '{item}' (search the two or "
        f"three most distinctive words if the full name doesn't match). "
        f"Open its product page FIRST. Then click 'Add to Cart' EXACTLY ONCE, "
        f"on the product page only — do not use the Add to Cart button on a "
        f"listing or search-results card, and do not click it a second time. "
        f"Then open the cart and report the quantity, the cart total, and any "
        f"purchase limit that was applied. If it is out of stock or not "
        f"listed, say so and stop."
    )

    return instruction, mode


def format_agent_start(goal, parsed, mode, target):
    """Telegram message announcing the prompt the agent is about to run."""
    def esc(v):
        return html.escape(str(v)) if v is not None else "—"

    return "\n".join([
        "🤖 <b>Agent starting</b>",
        "",
        f"🛍 <b>Item:</b> {esc(parsed.get('item') or '—')}",
        f"🎯 <b>Target:</b> {esc(mode)} — {esc(target)}",
        "",
        "<b>Prompt:</b>",
        f"<pre>{esc(goal)}</pre>",
    ])


def format_agent_result(result, parsed):
    """Telegram message reporting how the run went."""
    def esc(v):
        return html.escape(str(v)) if v is not None else "—"

    badge = {"completed": "✅ Agent finished",
             "max_steps": "⛔ Agent ran out of steps",
             "error": "💥 Agent failed"}.get(result.status, "Agent done")

    body = result.final_text or result.error or "(no answer produced)"
    # Telegram hard-caps a message at 4096 chars; leave room for the wrapper.
    if len(body) > 3500:
        body = body[:3500] + "\n… (truncated — see the transcript)"

    lines = [
        f"<b>{esc(badge)}</b>",
        "",
        f"🛍 <b>Item:</b> {esc(parsed.get('item') or '—')}",
        f"🔢 <b>Steps:</b> {esc(result.steps)}",
        "",
        esc(body),
    ]
    if result.blocked:
        lines += ["", "⛔ <b>Blocked actions:</b>"]
        lines += [f"• {esc(r)}" for r in result.blocked]
    if result.transcript_path:
        lines += ["", f"📝 <code>{esc(os.path.basename(result.transcript_path))}</code>"]
    return "\n".join(lines)


# ==============================================================================
# TELEGRAM DELIVERY
# ==============================================================================
# Discord caps a webhook message at 2000 characters (Telegram allows 4096).
_DISCORD_LIMIT = 2000


def _html_to_discord(text):
    """
    Convert our Telegram-flavoured HTML into Discord markdown.

    The formatters emit HTML because that is what Telegram's parse_mode wants.
    Discord speaks markdown instead, so translate the few tags we actually use
    and unescape the entities — otherwise alerts arrive full of visible
    &lt;b&gt; noise.
    """
    out = text
    out = re.sub(r'<a href="([^"]*)">(.*?)</a>', r"[\2](\1)", out, flags=re.S)
    out = re.sub(r"</?b>", "**", out)
    out = re.sub(r"<pre>(.*?)</pre>", r"```\n\1\n```", out, flags=re.S)
    out = re.sub(r"</?code>", "`", out)
    out = re.sub(r"<[^>]+>", "", out)          # drop anything left over
    for entity, char in (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'),
                         ("&#x27;", "'"), ("&#39;", "'"), ("&amp;", "&")):
        out = out.replace(entity, char)        # &amp; last, or it double-decodes
    if len(out) > _DISCORD_LIMIT:
        out = out[: _DISCORD_LIMIT - 20] + "\n… (truncated)"
    return out


async def send_discord(session, text):
    """Post one message to the configured Discord webhook."""
    if not DISCORD_WEBHOOK_URL:
        return
    payload = {"content": _html_to_discord(text), "allowed_mentions": {"parse": []}}
    try:
        async with session.post(DISCORD_WEBHOOK_URL, json=payload) as resp:
            if resp.status in (200, 204):
                print("[Success] Alert sent to Discord.")
            else:
                body = await resp.text()
                print(f"[Warning] Discord webhook {resp.status}: {body[:200]}")
    except Exception as e:
        print(f"[Error] Discord webhook failed: {e}")


async def notify(session, text):
    """
    Deliver one alert to every configured destination.

    Each sender swallows its own errors, so a dead webhook can never stop a
    Telegram alert (or vice versa), and neither can break the monitor.
    """
    if DISCORD_WEBHOOK_URL:
        await send_discord(session, text)
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_IDS:
        await notify(session, text)


async def send_telegram(session, text):
    """Send `text` to every configured chat ID; failures are isolated."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for chat_id in TELEGRAM_CHAT_IDS:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }
        try:
            async with session.post(url, json=payload) as resp:
                if resp.status == 200:
                    print(f"[Success] Alert sent to {chat_id}.")
                else:
                    body = await resp.text()
                    print(f"[Warning] Telegram {resp.status} for {chat_id}: {body[:200]}")
        except Exception as e:  # one bad recipient must not block the others
            print(f"[Error] Telegram send to {chat_id} failed: {e}")


# ==============================================================================
# AGENT DISPATCH  (single-flight + cooldown + daily cap)
# ==============================================================================
# asyncio.Lock() is safe to construct at import on Python 3.10+ — primitives no
# longer bind to a loop at creation time.
_agent_lock = asyncio.Lock()
_agent_next_allowed = 0.0        # loop.time() before which new triggers are dropped
_agent_runs_today = 0
_agent_day = None
_agent_tasks = set()             # strong refs: asyncio may GC a bare running task
_run_agent = None


def _load_agent():
    """
    Import browser_agent on first use.

    Lazy on purpose: with AGENT_ENABLED off, a Phase-1-only install never has to
    have openai/mcp installed. The sys.path line makes the import work even when
    this file is run as `python -m src.scraper` rather than by path.
    """
    global _run_agent
    if _run_agent is None:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        from browser_agent import run_agent
        _run_agent = run_agent
    return _run_agent


def _quota_ok():
    """True if we are under the daily cap. Counter resets on date change."""
    global _agent_runs_today, _agent_day
    from datetime import date

    today = date.today()
    if _agent_day != today:
        _agent_day, _agent_runs_today = today, 0
    if _agent_runs_today >= AGENT_MAX_RUNS_PER_DAY:
        return False
    _agent_runs_today += 1
    return True


def _maybe_dispatch_agent(parsed, jump_url):
    """
    Gate and launch an agent run. Deliberately NOT async.

    Every check and the reservation happen with no `await` between them. If this
    were a coroutine, two messages arriving in the same tick could both see an
    unlocked lock and both launch — the classic check-then-act race.
    """
    global _agent_next_allowed
    if not AGENT_ENABLED:
        return

    loop = asyncio.get_running_loop()
    now = loop.time()

    if _agent_lock.locked():
        print("[Agent] skipped — a run is already in progress")
        return
    if now < _agent_next_allowed:
        print(f"[Agent] skipped — cooldown, {_agent_next_allowed - now:.0f}s left")
        return
    if not _quota_ok():
        print(f"[Agent] skipped — daily cap ({AGENT_MAX_RUNS_PER_DAY}) reached")
        return

    _agent_next_allowed = now + AGENT_COOLDOWN_SECONDS   # reserve BEFORE awaiting
    task = asyncio.create_task(_run_agent_for_alert(parsed, jump_url))
    _agent_tasks.add(task)
    task.add_done_callback(_agent_tasks.discard)


async def _send(text):
    """
    Send one Telegram message on its own session.

    on_message's session is closed the moment that handler returns, and the
    agent task outlives it — reusing it would raise "Session is closed".
    """
    async with aiohttp.ClientSession() as session:
        await notify(session, text)


async def _run_agent_for_alert(parsed, jump_url):
    """Announce, run the agent, report back. Never lets a failure reach the bot."""
    global _agent_next_allowed
    async with _agent_lock:
        try:
            run_agent = _load_agent()
            goal, mode = build_agent_goal(parsed)
            target = AGENT_FIXTURE_URL if mode == "fixture" else parsed.get("link")

            if not goal.strip():
                await _send("⚠️ Agent not run: the goal template is empty "
                            "(see build_agent_goal in scraper.py).")
                return

            print(f"[Agent] starting ({mode}) -> {parsed.get('item')!r}")
            if AGENT_ANNOUNCE:
                await _send(format_agent_start(goal, parsed, mode, target))

            result = await asyncio.wait_for(
                run_agent(
                    goal,
                    headless=AGENT_HEADLESS,   # headed by default: watch it work
                    keep_open=False,           # must never wait on Enter here
                    keep_open_seconds=0 if AGENT_HEADLESS else AGENT_KEEP_OPEN_SECONDS,
                    interactive=False,         # no terminal: never call input()
                    max_steps=AGENT_MAX_STEPS,
                ),
                timeout=AGENT_TIMEOUT_SECONDS,
            )
            print(f"[Agent] {result.status} in {result.steps} steps")
            await _send(format_agent_result(result, parsed))

        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            print(f"[Agent] timed out after {AGENT_TIMEOUT_SECONDS:.0f}s")
            try:
                await _send(f"⏱ Agent timed out after {AGENT_TIMEOUT_SECONDS:.0f}s.")
            except Exception:
                pass
        except BaseException as e:
            # BaseException, not Exception: a SystemExit escaping a Task is
            # re-raised into the event loop and would stop bot.run() outright.
            import traceback
            traceback.print_exc()
            try:
                await _send(f"💥 Agent failed: {html.escape(f'{type(e).__name__}: {e}')}")
            except Exception:
                pass
        finally:
            # Measure the cooldown from the finish too, not just the start.
            _agent_next_allowed = asyncio.get_running_loop().time() + AGENT_COOLDOWN_SECONDS


# ==============================================================================
# DISCORD MONITOR
# ==============================================================================
bot = commands.Bot(command_prefix="!", self_bot=True)


def _embeds_to_dicts(message):
    """Adapt discord.py embed objects into the plain dicts parse_alert wants."""
    out = []
    for e in message.embeds:
        out.append(
            {
                "title": e.title,
                "description": e.description,
                "url": e.url,
                "author": e.author.name if e.author else None,
                "footer": e.footer.text if e.footer else None,
                "fields": [{"name": f.name, "value": f.value} for f in e.fields],
            }
        )
    return out


@bot.event
async def on_ready():
    print("--- Sentinel Monitor Online ---")
    print(f"Logged in as: {bot.user}")
    print(f"Monitoring channel: {TARGET_CHANNEL_ID}")
    routes = []
    if DISCORD_WEBHOOK_URL:
        routes.append("Discord webhook")
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_IDS:
        routes.append(f"Telegram ({len(TELEGRAM_CHAT_IDS)} recipient(s))")
    print(f"Alerts via: {', '.join(routes) or 'NOTHING CONFIGURED'}")
    if AGENT_ENABLED:
        print(f"Browser agent: ON — mode={AGENT_TARGET_MODE}, "
              f"headless={AGENT_HEADLESS}, max_steps={AGENT_MAX_STEPS}, "
              f"cooldown={AGENT_COOLDOWN_SECONDS:.0f}s, cap={AGENT_MAX_RUNS_PER_DAY}/day")
    else:
        print("Browser agent: off (set AGENT_ENABLED=1 to enable)")
    print("-------------------------------")


@bot.event
async def on_message(message):
    if message.channel.id != TARGET_CHANNEL_ID:
        return
    if message.author.id == bot.user.id:
        return
    if REQUIRE_EMBED and not message.embeds:
        return

    embed_dicts = _embeds_to_dicts(message)

    # Title filter: only alert for the target packs, matched against the item
    # TITLE (embed titles + the message content line) — not fields/footer, so a
    # stray mention elsewhere won't trigger a buy for the wrong pack.
    if TITLE_KEYWORDS:
        title_text = " ".join(
            p for p in [message.content or ""] + [e["title"] for e in embed_dicts if e["title"]]
        ).lower()
        if not any(k.lower() in title_text for k in TITLE_KEYWORDS):
            return

    parsed = parse_alert(message.content, embed_dicts)
    text = format_telegram(parsed, message.jump_url)
    print(f"[Match] {message.id} -> {parsed.get('item')!r} @ {parsed.get('store')!r}")

    async with aiohttp.ClientSession() as session:
        await notify(session, text)

    # Hand off to Phase 2. Returns immediately — the run happens in a background
    # task so this handler never blocks Discord's gateway.
    _maybe_dispatch_agent(parsed, message.jump_url)


def _check_config():
    """Fail fast with a clear message if required secrets are missing."""
    missing = []
    if not TOKEN:
        missing.append("DISCORD_TOKEN")
    if not TARGET_CHANNEL_ID:
        missing.append("TARGET_CHANNEL_ID")
    # At least one delivery route. Telegram needs both of its vars together.
    has_discord = bool(DISCORD_WEBHOOK_URL)
    has_telegram = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_IDS)
    if not (has_discord or has_telegram):
        missing.append("DISCORD_WEBHOOK_URL (or TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_IDS)")
    if missing:
        sys.exit(
            "Missing required config: "
            + ", ".join(missing)
            + "\nSet them in a .env file (see .env.example) or your environment."
        )


if __name__ == "__main__":
    _check_config()
    bot.run(TOKEN)
