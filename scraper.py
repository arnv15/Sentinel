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
TARGET_CHANNEL_ID = int(os.environ.get("TARGET_CHANNEL_ID", "0"))  # channel to monitor

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
# TELEGRAM DELIVERY
# ==============================================================================
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
    print(f"Telegram recipients: {len(TELEGRAM_CHAT_IDS)}")
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
        await send_telegram(session, text)


def _check_config():
    """Fail fast with a clear message if required secrets are missing."""
    missing = []
    if not TOKEN:
        missing.append("DISCORD_TOKEN")
    if not TARGET_CHANNEL_ID:
        missing.append("TARGET_CHANNEL_ID")
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not TELEGRAM_CHAT_IDS:
        missing.append("TELEGRAM_CHAT_IDS")
    if missing:
        sys.exit(
            "Missing required config: "
            + ", ".join(missing)
            + "\nSet them in a .env file (see .env.example) or your environment."
        )


if __name__ == "__main__":
    _check_config()
    bot.run(TOKEN)
