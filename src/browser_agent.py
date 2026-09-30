"""
Browser research agent — provider-agnostic tool-use loop over Playwright MCP.

Architecture (the four moving parts):

    ┌──────────┐  tool call   ┌──────────────┐  Playwright   ┌──────────┐
    │ this loop│ ───────────► │ Playwright   │ ────────────► │ Chromium │
    │(browser_ │              │ MCP server   │               │ (headed) │
    │ agent.py)│ ◄─────────── │ (npx subproc)│ ◄──────────── │          │
    └──────────┘  a11y tree   └──────────────┘   DOM/a11y     └──────────┘

  1. We spawn `npx @playwright/mcp` as a subprocess speaking MCP over stdio.
  2. It exposes browser actions as tools (navigate, click, type, snapshot...).
  3. We list those tools and hand them to whichever LLM provider is configured.
  4. Loop: the model picks a tool -> we execute it via MCP -> feed the result
     (usually an accessibility-tree snapshot) back -> repeat until the model
     stops asking for tools and gives a final answer.

There is NO intelligence in the MCP server — it's just the hands. All the
deciding happens in the model call inside run_agent().

PROVIDERS
---------
Everything except Anthropic talks the OpenAI Chat Completions format, so
switching between them is a .env change, not a code change. Set LLM_PROVIDER:

    gemini      (default)  free tier via Google AI Studio    GEMINI_API_KEY
    groq                   free tier, fastest                GROQ_API_KEY
    cerebras               free tier, high throughput        CEREBRAS_API_KEY
    openrouter             aggregator, some free models      OPENROUTER_API_KEY
    github                 free w/ GitHub account            GITHUB_TOKEN
    ollama                 fully local, no key needed        —
    anthropic              native Messages API               ANTHROPIC_API_KEY

Override the model per provider with LLM_MODEL. Note that on Gemini's FREE
tier Google uses submitted content to improve its products — fine for the
MockMart fixture, but don't point a free-tier run at anything sensitive.
"""

import asyncio
import json
import os
import sys
from dataclasses import dataclass, field

from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Load .env BEFORE the module-level config below reads os.environ — otherwise
# LLM_PROVIDER / HEADLESS set in .env are silently ignored and the defaults win.
load_dotenv()

# ---------------------------------------------------------------------------
# Config — tweak these
# ---------------------------------------------------------------------------

# Per-provider defaults. `base_url = None` means the provider's native SDK.
PROVIDERS = {
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "key_env": "GEMINI_API_KEY",
        "model": "gemini-3.7-flash",
        "sanitize_schema": True,   # Gemini is strict about JSON Schema (see below)
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "key_env": "GROQ_API_KEY",
        "model": "llama-3.3-70b-versatile",
    },
    "cerebras": {
        "base_url": "https://api.cerebras.ai/v1",
        "key_env": "CEREBRAS_API_KEY",
        "model": "llama-3.3-70b",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "key_env": "OPENROUTER_API_KEY",
        "model": "meta-llama/llama-3.3-70b-instruct:free",
    },
    "github": {
        "base_url": "https://models.inference.ai.azure.com",
        "key_env": "GITHUB_TOKEN",
        "model": "gpt-4o",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "key_env": None,           # local, no auth
        "model": "qwen3:30b",
    },
    "anthropic": {
        "base_url": None,          # native SDK path
        "key_env": "ANTHROPIC_API_KEY",
        "model": "claude-sonnet-5",
    },
}

PROVIDER = os.environ.get("LLM_PROVIDER", "gemini").strip().lower()

MAX_STEPS = 40          # hard cap on tool calls — stops runaway loops
MAX_TOKENS = 4096       # per model response

# Show the browser window (default) or run invisibly. Set HEADLESS=1 in .env for
# servers / unattended runs. Playwright MCP is headed by default.
HEADLESS = os.environ.get("HEADLESS", "").strip().lower() in ("1", "true", "yes")


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if raw in ("1", "true", "yes"):
        return True
    if raw in ("0", "false", "no"):
        return False
    return default


# When the loop finishes, run_agent() exits the stdio_client context, which kills
# the MCP subprocess and takes Chromium down with it — so the window vanishes
# before you can look at the result. KEEP_OPEN waits for Enter first.
# Defaults on when headed, off when headless (nothing to look at, and a server
# run must never block on stdin).
KEEP_OPEN = _flag("KEEP_OPEN", not HEADLESS)

# Seconds to pause after each tool call, so you can actually watch it work.
try:
    STEP_DELAY = float(os.environ.get("STEP_DELAY", "0") or 0)
except ValueError:
    STEP_DELAY = 0.0

# Write a Markdown transcript of each run to runs/. Answers "did it actually do
# what I asked" after the window is gone.
TRANSCRIPT = _flag("TRANSCRIPT", True)

# ---- Confirmation guardrails: OFF by design ----
#
# Both lists are empty, so guardrail_check() never pauses and the agent runs
# uninterrupted. This is the user's deliberate choice: they supervise every run
# and do not want y/N prompts breaking the flow.
#
# The MECHANISM is intact — put entries back in either list (or pass
# allowed_domains= / risky_keywords= to run_agent) and confirmation returns
# immediately, no other code changes needed.
#
# Domain allowlist. Empty = allow any site. To restrict:
#   ALLOWED_DOMAINS = ["wikipedia.org", "arxiv.org", "localhost"]
# A navigate off the list prompts for confirmation.
ALLOWED_DOMAINS: list[str] = []

# Click/type targets whose label contains one of these words prompt first. To
# re-arm the "don't buy things unattended" guardrail:
#   RISKY_KEYWORDS = ["buy", "purchase", "checkout", "pay", "order",
#                     "sign in", "log in", "submit", "confirm", "delete"]
RISKY_KEYWORDS: list[str] = []

SYSTEM_PROMPT = """You are a web research agent driving a real Chromium browser through tools.

How to work:
- Call browser_snapshot (or read the snapshot returned after each action) to see
  the page as an accessibility tree. Each element has a `ref` — use it to target
  clicks and typing.
- Take one action at a time, then look at the new snapshot before the next.
- To research a question: navigate to a search engine or a known site, read
  results, click through, and extract the facts you need.
- Never guess a ref — only use refs present in the latest snapshot.
- When you have the answer, stop calling tools and reply with a clear summary,
  citing the URLs you used.
"""


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

class AgentConfigError(RuntimeError):
    """
    Bad or missing provider config.

    Raised instead of sys.exit() so a caller can catch it. This matters more
    than it looks: SystemExit escaping an asyncio Task is re-raised into the
    event loop and stops run_forever(), so a sys.exit() in here would take down
    an embedding process (e.g. the Discord monitor) rather than just the run.
    """


@dataclass
class AgentResult:
    """What a run produced, so callers can report on it instead of scraping stdout."""

    goal: str
    status: str                       # "completed" | "max_steps" | "error"
    final_text: str = ""              # the model's closing answer ("" if none)
    steps: int = 0                    # tool-call steps actually executed
    transcript_path: str | None = None
    blocked: list[str] = field(default_factory=list)   # guardrail denial reasons
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "completed"


# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------

def _hostname(url: str) -> str:
    from urllib.parse import urlparse
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def confirm(prompt: str, *, interactive: bool = True) -> bool:
    """
    Ask the human on the terminal. Returns True only on an explicit yes.

    With interactive=False there is nobody at a terminal (e.g. the agent was
    started by the Discord monitor), so we auto-deny instead of calling input().
    Blocking on stdin from inside an event loop would stall the caller's whole
    process — for a Discord bot, that means a dropped gateway heartbeat.
    """
    if not interactive:
        print(f"\n⛔ {prompt} — auto-denied (non-interactive run).")
        return False
    try:
        answer = input(f"\n⚠️  {prompt} [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")


def guardrail_check(
    tool_name: str,
    tool_input: dict,
    *,
    interactive: bool = True,
    allowed_domains: list[str] | None = None,
    risky_keywords: list[str] | None = None,
) -> tuple[bool, str]:
    """
    Return (allowed, reason). `allowed=False` means the action was declined.

    Both lists default to the module-level constants, which ship EMPTY — so by
    default this is a pass-through and nothing ever prompts. Pass explicit lists
    to turn confirmation on for a single run.
    """
    allowed_domains = ALLOWED_DOMAINS if allowed_domains is None else allowed_domains
    risky_keywords = RISKY_KEYWORDS if risky_keywords is None else risky_keywords

    # Domain allowlist on navigation
    if tool_name == "browser_navigate" and allowed_domains:
        host = _hostname(tool_input.get("url", ""))
        if not any(host == d or host.endswith("." + d) for d in allowed_domains):
            if not confirm(f"Navigate to non-allowlisted host '{host}'?", interactive=interactive):
                return False, f"Declined navigation to {host}."

    # Risky-keyword scan on click/type targets
    if tool_name in ("browser_click", "browser_type", "browser_press_key") and risky_keywords:
        label = " ".join(
            str(tool_input.get(k, "")) for k in ("element", "text", "key")
        ).lower()
        hit = next((kw for kw in risky_keywords if kw in label), None)
        if hit:
            if not confirm(
                f"'{tool_name}' targets a risky control (matched '{hit}'): {label!r}. Proceed?",
                interactive=interactive,
            ):
                return False, f"Declined the '{hit}' action."

    return True, ""


# ---------------------------------------------------------------------------
# Schema sanitizing
# ---------------------------------------------------------------------------

# Gemini's function declarations accept a much narrower slice of JSON Schema
# than Anthropic does, and Playwright MCP's tool schemas use keywords it
# rejects ($schema, additionalProperties, anyOf, ...). Whitelisting the keys we
# know are portable is safer than blacklisting the ones that currently break.
_SCHEMA_KEEP = {"type", "properties", "required", "items", "enum", "description"}


def sanitize_schema(node):
    """Recursively reduce a JSON Schema to the subset every provider accepts."""
    if not isinstance(node, dict):
        return node

    # Collapse anyOf/oneOf/allOf to their first non-null branch.
    for combiner in ("anyOf", "oneOf", "allOf"):
        if combiner in node:
            branches = [b for b in node[combiner] if b.get("type") != "null"]
            if branches:
                merged = sanitize_schema(branches[0])
                if "description" in node:
                    merged.setdefault("description", node["description"])
                return merged

    out = {}
    for key, value in node.items():
        if key not in _SCHEMA_KEEP:
            continue
        if key == "properties" and isinstance(value, dict):
            out[key] = {k: sanitize_schema(v) for k, v in value.items()}
        elif key == "items":
            out[key] = sanitize_schema(value)
        else:
            out[key] = value

    if out.get("type") == "object":
        out.setdefault("properties", {})
    return out


# ---------------------------------------------------------------------------
# Gemini thought signatures
# ---------------------------------------------------------------------------

def _plain(value):
    """Normalise pydantic models / nested objects to plain JSON-able data."""
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _extra_content(tool_call):
    """
    Pull Gemini's per-tool-call `extra_content` (which carries
    `{"google": {"thought_signature": "..."}}`) off an OpenAI-SDK tool call.

    The OpenAI SDK has no declared field for it, so pydantic parks it in
    `model_extra`. Returns None for providers that don't send it.
    """
    ec = getattr(tool_call, "extra_content", None)
    if ec is None:
        extra = getattr(tool_call, "model_extra", None) or {}
        ec = extra.get("extra_content")
    return _plain(ec) if ec else None


# ---------------------------------------------------------------------------
# Provider adapters
#
# Each adapter normalises one provider to the same three operations the loop
# needs, so the loop below has exactly one implementation.
#   .tools(mcp_tools)                 -> provider-shaped tool list
#   .complete(messages, tools)        -> (text, tool_calls, wants_tools)
#   .record(messages, ...)            -> append turns in the provider's shape
# ---------------------------------------------------------------------------

class OpenAIAdapter:
    """Anything speaking the OpenAI Chat Completions API — the default path."""

    def __init__(self, cfg, model):
        from openai import OpenAI

        key = os.environ.get(cfg["key_env"], "") if cfg["key_env"] else "ollama"
        if cfg["key_env"] and not key:
            raise AgentConfigError(
                f"{cfg['key_env']} is not set. Add it to a .env file (see .env.example).\n"
                f"For Gemini, create a free key at https://aistudio.google.com/apikey"
            )
        self.client = OpenAI(api_key=key or "none", base_url=cfg["base_url"])
        self.model = model
        self.sanitize = cfg.get("sanitize_schema", False)

    def tools(self, mcp_tools):
        out = []
        for t in mcp_tools:
            schema = tool_schema(t)
            out.append({
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": (t.description or "")[:1024],
                    "parameters": sanitize_schema(schema) if self.sanitize else schema,
                },
            })
        return out

    def seed(self, goal):
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": goal},
        ]

    def complete(self, messages, tools):
        resp = self.client.chat.completions.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            messages=messages,
            tools=tools,
            tool_choice="auto",
        )
        msg = resp.choices[0].message
        calls = []
        for tc in (msg.tool_calls or []):
            # OpenAI-style arguments arrive as a JSON *string*, not a dict.
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append({
                "id": tc.id,
                "name": tc.function.name,
                "input": args,
                "extra_content": _extra_content(tc),   # Gemini thought signature
            })
        self._last = msg
        return (msg.content or ""), calls, bool(calls)

    def record_assistant(self, messages, _text, calls):
        entry = {"role": "assistant", "content": self._last.content or ""}
        if calls:
            entry["tool_calls"] = [self._tool_call_entry(c) for c in calls]
        messages.append(entry)

    def _tool_call_entry(self, c):
        entry = {
            "id": c["id"],
            "type": "function",
            "function": {"name": c["name"], "arguments": json.dumps(c["input"])},
        }
        # Gemini 3 thinking models attach an opaque `thought_signature` to every
        # function call and REQUIRE it echoed back on the next turn, or the
        # request 400s with "Function call is missing a thought_signature".
        # It rides on the tool call as extra_content.google.thought_signature.
        # Harmless to other providers: OpenAI-compatible servers ignore unknown
        # fields, and we only attach it when the model actually sent one.
        if c.get("extra_content"):
            entry["extra_content"] = c["extra_content"]
        return entry

    def record_results(self, messages, results):
        # OpenAI-style tool results are one message per call, content is a
        # plain string. Images can't ride along, so they follow as a user turn.
        images = []
        for r in results:
            messages.append({
                "role": "tool",
                "tool_call_id": r["id"],
                "content": r["text"] or "(tool returned no content)",
            })
            images.extend(r["images"])
        if images:
            messages.append({
                "role": "user",
                "content": [{"type": "text", "text": "Screenshot(s) from the tool call:"}]
                + [{"type": "image_url", "image_url": {"url": f"data:{m};base64,{d}"}}
                   for m, d in images],
            })


class AnthropicAdapter:
    """Native Anthropic Messages API — kept so the original path still works."""

    def __init__(self, cfg, model):
        import anthropic

        if not os.environ.get(cfg["key_env"]):
            raise AgentConfigError(f"{cfg['key_env']} is not set. Add it to a .env file (see .env.example).")
        self.client = anthropic.Anthropic()
        self.model = model

    def tools(self, mcp_tools):
        return [{
            "name": t.name,
            "description": t.description or "",
            "input_schema": tool_schema(t),
        } for t in mcp_tools]

    def seed(self, goal):
        return [{"role": "user", "content": goal}]

    def complete(self, messages, tools):
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=tools,
            messages=messages,
        )
        self._last = resp
        text = " ".join(b.text for b in resp.content if b.type == "text")
        calls = [{"id": b.id, "name": b.name, "input": b.input}
                 for b in resp.content if b.type == "tool_use"]
        return text, calls, resp.stop_reason == "tool_use"

    def record_assistant(self, messages, _text, _calls):
        messages.append({"role": "assistant", "content": self._last.content})

    def record_results(self, messages, results):
        blocks = []
        for r in results:
            content = [{"type": "text", "text": r["text"] or "(tool returned no content)"}]
            content += [{"type": "image",
                         "source": {"type": "base64", "media_type": m, "data": d}}
                        for m, d in r["images"]]
            blocks.append({
                "type": "tool_result",
                "tool_use_id": r["id"],
                "content": content,
                "is_error": r["is_error"],
            })
        messages.append({"role": "user", "content": blocks})


def build_adapter():
    """Pick the adapter for LLM_PROVIDER and report what we're using."""
    if PROVIDER not in PROVIDERS:
        raise AgentConfigError(f"Unknown LLM_PROVIDER {PROVIDER!r}. Options: {', '.join(PROVIDERS)}")
    cfg = dict(PROVIDERS[PROVIDER])
    model = os.environ.get("LLM_MODEL", "").strip() or cfg["model"]
    # Escape hatch: point at any other OpenAI-compatible endpoint (a local
    # proxy, a self-hosted vLLM, a test double) without editing PROVIDERS.
    override = os.environ.get("LLM_BASE_URL", "").strip()
    if override and cfg["base_url"] is not None:
        cfg["base_url"] = override
    adapter = (AnthropicAdapter if cfg["base_url"] is None else OpenAIAdapter)(cfg, model)
    print(f"Provider: {PROVIDER} — model {model}")
    return adapter


# ---------------------------------------------------------------------------
# MCP result normalising
#
# NOTE: the `mcp` Python package exposes snake_case attributes (`input_schema`,
# `is_error`, `mime_type`); the camelCase spellings are only JSON wire aliases.
# Reading `.inputSchema` raises AttributeError, and `.isError` / `.mimeType`
# silently fall back to the default — so we go through these helpers, which
# accept either spelling and stay correct across mcp versions.
# ---------------------------------------------------------------------------

def _attr(obj, snake: str, camel: str, default=None):
    """Read a field by either naming convention."""
    return getattr(obj, snake, None) or getattr(obj, camel, None) or default


def tool_schema(tool) -> dict:
    return _attr(tool, "input_schema", "inputSchema", {"type": "object", "properties": {}})


def mcp_result_to_parts(result) -> tuple[str, list, bool]:
    """Flatten an MCP tool result into (text, [(media_type, base64), ...], is_error)."""
    texts, images = [], []
    for item in result.content:
        kind = getattr(item, "type", None)
        if kind == "text":
            texts.append(item.text)
        elif kind == "image":
            images.append((_attr(item, "mime_type", "mimeType", "image/png"), item.data))
    return "\n".join(texts), images, bool(_attr(result, "is_error", "isError", False))


# ---------------------------------------------------------------------------
# Watching the run
# ---------------------------------------------------------------------------

async def hold_browser_open() -> None:
    """
    Block until the human presses Enter, keeping Chromium alive.

    Must be called INSIDE the stdio_client context — once that exits, the MCP
    subprocess dies and the window closes. Runs input() on a worker thread so
    the event loop (and the MCP connection) stays responsive.
    """
    if not sys.stdin.isatty():
        return   # nobody could press Enter; never wedge a non-interactive run
    print("\n🔎 Browser left open so you can inspect the result.")
    try:
        await asyncio.to_thread(input, "   Press Enter to close it… ")
    except (EOFError, KeyboardInterrupt):
        pass  # no TTY, or the user hit Ctrl-C — just tear down


def write_transcript(goal: str, lines: list[str], *,
                     headless: bool | None = None,
                     provider: str = PROVIDER) -> str | None:
    """Save a Markdown record of the run to runs/. Returns the path, or None."""
    if not TRANSCRIPT:
        return None
    from datetime import datetime

    headless = HEADLESS if headless is None else headless

    runs = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "runs")
    os.makedirs(runs, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    path = os.path.join(runs, f"run-{stamp}.md")
    header = [
        f"# Agent run — {stamp}",
        "",
        f"- **Goal:** {goal}",
        f"- **Provider:** {provider}",
        f"- **Headless:** {headless}",
        "",
        "## Steps",
        "",
    ]
    with open(path, "w") as f:
        f.write("\n".join(header + lines) + "\n")
    return path


# ---------------------------------------------------------------------------
# The agent loop
# ---------------------------------------------------------------------------

async def run_agent(
    goal: str,
    *,
    headless: bool | None = None,
    keep_open: bool | None = None,
    interactive: bool | None = None,
    max_steps: int | None = None,
    step_delay: float | None = None,
    allowed_domains: list[str] | None = None,
    risky_keywords: list[str] | None = None,
) -> AgentResult:
    """
    Drive the browser until the model answers, then return what happened.

    Every keyword defaults to None meaning "use the module-level value", so the
    CLI behaves exactly as before. They exist because the module-level flags are
    read once at IMPORT time: a long-lived caller (the Discord monitor) imports
    this at startup, so it must be able to set headless/keep_open per call
    rather than inheriting whatever the environment looked like back then.

    interactive=False is for callers with no terminal — guardrail confirmations
    auto-deny instead of blocking on input(), which would otherwise stall the
    caller's event loop indefinitely.
    """
    # Resolve every knob to a local, then use the locals below — never the globals.
    headless = HEADLESS if headless is None else headless
    keep_open = KEEP_OPEN if keep_open is None else keep_open
    interactive = True if interactive is None else interactive
    max_steps = MAX_STEPS if max_steps is None else max_steps
    step_delay = STEP_DELAY if step_delay is None else step_delay

    status = "error"
    final_text = ""      # the model's closing answer (no tool calls alongside it)
    last_text = ""       # most recent narration, whether or not it was the last turn
    steps_done = 0
    blocked: list[str] = []
    error: str | None = None
    log: list[str] = []

    adapter = build_adapter()   # raises AgentConfigError; nothing has run yet

    # Launch Playwright MCP as a subprocess.
    # Current @playwright/mcp is HEADED BY DEFAULT and only accepts `--headless`;
    # the old `--headed` flag was removed and passing it kills the subprocess
    # with "unknown option" (surfacing here as MCPError: Connection closed).
    args = ["-y", "@playwright/mcp@latest"]
    if headless:
        args.append("--headless")
    server = StdioServerParameters(command="npx", args=args)

    try:
        async with stdio_client(server) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tool_list = (await session.list_tools()).tools
                tools = adapter.tools(tool_list)
                print(f"Connected to Playwright MCP — {len(tools)} tools available.")
                print(f"Goal: {goal}\n")

                messages = adapter.seed(goal)

                for step in range(1, max_steps + 1):
                    steps_done = step

                    # adapter.complete() uses the SYNCHRONOUS OpenAI/Anthropic
                    # client — a blocking 5-30s HTTPS call. On the caller's event
                    # loop that stalls everything else (for a Discord bot, the
                    # gateway heartbeat). to_thread keeps the loop responsive;
                    # adapter._last is written on the worker and read after the
                    # await, so the ordering is safe.
                    text, calls, wants_tools = await asyncio.to_thread(
                        adapter.complete, messages, tools
                    )
                    adapter.record_assistant(messages, text, calls)

                    if text.strip():
                        last_text = text.strip()
                        print(f"\n🤖 {last_text}\n")
                        log.append(f"**Model:** {last_text}\n")

                    if not wants_tools:
                        final_text = text.strip()
                        status = "completed"
                        print("✅ Done.")
                        log.append("\n## Result\n\n✅ Finished normally.")
                        break   # NOT return — the browser must stay up for keep_open

                    results = []
                    for call in calls:
                        print(f"[{step}] → {call['name']}({_short(call['input'])})")
                        log.append(f"- `[{step}]` **{call['name']}**(`{_short(call['input'], 200)}`)")

                        allowed, reason = guardrail_check(
                            call["name"], call["input"],
                            interactive=interactive,
                            allowed_domains=allowed_domains,
                            risky_keywords=risky_keywords,
                        )
                        if not allowed:
                            blocked.append(reason)
                            results.append({"id": call["id"], "text": f"BLOCKED: {reason}",
                                            "images": [], "is_error": True})
                            log.append(f"  - ⛔ blocked: {reason}")
                            continue

                        try:
                            raw = await session.call_tool(call["name"], call["input"])
                            body, images, errored = mcp_result_to_parts(raw)
                            results.append({"id": call["id"], "text": body, "images": images,
                                            "is_error": errored})
                            head = " ".join(body.split())[:160]
                            log.append(f"  - {'⚠️ error' if errored else '✓'} {head}")
                        except Exception as e:  # keep the loop alive on a single failure
                            results.append({"id": call["id"], "text": f"ERROR: {e}",
                                            "images": [], "is_error": True})
                            log.append(f"  - ⚠️ exception: {e}")

                        if step_delay:
                            await asyncio.sleep(step_delay)

                    adapter.record_results(messages, results)
                else:
                    # Loop ran to max_steps without the model ever finishing.
                    status = "max_steps"
                    print(f"⛔ Hit MAX_STEPS ({max_steps}) without finishing.")
                    log.append(f"\n## Result\n\n⛔ Hit MAX_STEPS ({max_steps}) without finishing.")
                
                    # No closing answer was produced. Pass along the model's last
                    # narration if there was one, but LABEL it — an unlabelled
                    # half-finished thought reads like a conclusion to whoever
                    # gets this in Telegram. `last_text` can be empty if the model
                    # only ever emitted tool calls and never narrated.
                    if last_text:
                        final_text = (
                            f"⚠️ Incomplete — stopped after {max_steps} steps without "
                            f"finishing. Last thing the agent said:\n\n{last_text}"
                        )
                    else:
                        final_text = (
                            f"⚠️ Incomplete — stopped after {max_steps} steps without "
                            f"producing an answer. See the transcript for what it tried."
                        )

                # Still inside the MCP session, so Chromium is still alive.
                if keep_open:
                    await hold_browser_open()

    except Exception as e:
        status = "error"
        error = f"{type(e).__name__}: {e}"
        print(f"\n💥 Run failed: {error}")
        log.append(f"\n## Result\n\n💥 Failed: {error}")

    path = write_transcript(goal, log, headless=headless)
    if path:
        print(f"📝 Transcript: {path}")

    return AgentResult(
        goal=goal,
        status=status,
        final_text=final_text,
        steps=steps_done,
        transcript_path=path,
        blocked=blocked,
        error=error,
    )


def _short(d: dict, n: int = 80) -> str:
    s = ", ".join(f"{k}={v!r}" for k, v in d.items())
    return s if len(s) <= n else s[:n] + "…"


if __name__ == "__main__":
    task = " ".join(sys.argv[1:]) or input("What should the agent research? ")
    try:
        asyncio.run(run_agent(task))
    except AgentConfigError as e:
        # Same message and exit code the old sys.exit() produced. The raise/catch
        # split exists so importers can handle it instead of dying.
        sys.exit(str(e))
