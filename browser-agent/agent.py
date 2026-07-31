"""
Browser research agent — raw Anthropic API tool-use loop over Playwright MCP.

Architecture (the four moving parts):

    ┌──────────┐  tool call   ┌──────────────┐  Playwright   ┌──────────┐
    │ this loop│ ───────────► │ Playwright   │ ────────────► │ Chromium │
    │ (agent.py│              │ MCP server   │               │ (headed) │
    │  + Claude)│ ◄────────── │ (npx subproc)│ ◄──────────── │          │
    └──────────┘  a11y tree   └──────────────┘   DOM/a11y     └──────────┘

  1. We spawn `npx @playwright/mcp` as a subprocess speaking MCP over stdio.
  2. It exposes browser actions as tools (navigate, click, type, snapshot...).
  3. We list those tools and hand them to the Claude Messages API verbatim.
  4. Loop: Claude picks a tool -> we execute it via MCP -> feed the result
     (usually an accessibility-tree snapshot) back -> repeat until Claude
     stops asking for tools and gives a final answer.

There is NO intelligence in the MCP server — it's just the hands. All the
deciding happens in the Claude call inside run_agent().
"""

import asyncio
import os
import sys

import anthropic
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# ---------------------------------------------------------------------------
# Config — tweak these
# ---------------------------------------------------------------------------

# claude-sonnet-5 is a strong, cost-effective default for tool-use loops.
# Swap to "claude-opus-4-8" for the hardest reasoning, "claude-haiku-4-5-20251001" for speed/cost.
MODEL = "claude-sonnet-5"

MAX_STEPS = 40          # hard cap on tool calls — stops runaway loops
MAX_TOKENS = 4096       # per Claude response

# Domain allowlist. Empty list = allow any site. Add hostnames to restrict, e.g.
#   ALLOWED_DOMAINS = ["wikipedia.org", "arxiv.org", "google.com"]
# A navigate to anything not on the list will pause for your confirmation.
ALLOWED_DOMAINS: list[str] = []

# If a click/type targets an element whose label contains one of these words,
# pause and ask you before doing it. This is the "don't buy things / don't
# submit forms unattended" guardrail.
RISKY_KEYWORDS = [
    "buy", "purchase", "checkout", "pay", "order", "place order",
    "submit", "confirm", "delete", "remove", "send", "post", "publish",
    "sign in", "log in", "subscribe", "agree", "accept",
]

SYSTEM_PROMPT = """You are a web research agent driving a real Chromium browser through tools.

How to work:
- Call browser_snapshot (or read the snapshot returned after each action) to see
  the page as an accessibility tree. Each element has a `ref` — use it to target
  clicks and typing.
- Take one action at a time, then look at the new snapshot before the next.
- To research a question: navigate to a search engine or a known site, read
  results, click through, and extract the facts you need.
- Never guess a ref — only use refs present in the latest snapshot.
- Do NOT attempt to log in, purchase, submit forms, or post content. If a task
  seems to require that, stop and explain what you'd need the human to do.
- When you have the answer, stop calling tools and reply with a clear summary,
  citing the URLs you used.
"""


# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------

def _hostname(url: str) -> str:
    from urllib.parse import urlparse
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def confirm(prompt: str) -> bool:
    """Ask the human on the terminal. Returns True only on an explicit yes."""
    try:
        answer = input(f"\n⚠️  {prompt} [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")


def guardrail_check(tool_name: str, tool_input: dict) -> tuple[bool, str]:
    """
    Return (allowed, reason). `allowed=False` means the human declined.
    We inspect the tool call and pause for confirmation on risky actions.
    """
    # Domain allowlist on navigation
    if tool_name == "browser_navigate" and ALLOWED_DOMAINS:
        host = _hostname(tool_input.get("url", ""))
        if not any(host == d or host.endswith("." + d) for d in ALLOWED_DOMAINS):
            if not confirm(f"Navigate to non-allowlisted host '{host}'?"):
                return False, f"Human declined navigation to {host}."

    # Risky-keyword scan on click/type targets
    if tool_name in ("browser_click", "browser_type", "browser_press_key"):
        label = " ".join(
            str(tool_input.get(k, "")) for k in ("element", "text", "key")
        ).lower()
        hit = next((kw for kw in RISKY_KEYWORDS if kw in label), None)
        if hit:
            if not confirm(f"'{tool_name}' targets a risky control (matched '{hit}'): {label!r}. Proceed?"):
                return False, f"Human declined the '{hit}' action."

    return True, ""


# ---------------------------------------------------------------------------
# MCP <-> Anthropic glue
# ---------------------------------------------------------------------------

def mcp_tools_to_anthropic(mcp_tools) -> list[dict]:
    """Convert the MCP tool list into the schema the Messages API expects."""
    out = []
    for t in mcp_tools:
        out.append({
            "name": t.name,
            "description": t.description or "",
            "input_schema": t.inputSchema or {"type": "object", "properties": {}},
        })
    return out


def mcp_result_to_content(result) -> list[dict]:
    """
    Convert an MCP tool result into Anthropic tool_result content blocks.
    Text stays text; images (screenshots) become base64 image blocks.
    """
    blocks: list[dict] = []
    for item in result.content:
        item_type = getattr(item, "type", None)
        if item_type == "text":
            blocks.append({"type": "text", "text": item.text})
        elif item_type == "image":
            blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": getattr(item, "mimeType", "image/png"),
                    "data": item.data,
                },
            })
    if not blocks:
        blocks.append({"type": "text", "text": "(tool returned no content)"})
    return blocks


# ---------------------------------------------------------------------------
# The agent loop
# ---------------------------------------------------------------------------

async def run_agent(goal: str) -> None:
    load_dotenv()
    if not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set. Add it to a .env file (see .env.example).")

    client = anthropic.Anthropic()

    # Launch Playwright MCP as a subprocess. `--headed` shows the browser window.
    server = StdioServerParameters(
        command="npx",
        args=["-y", "@playwright/mcp@latest", "--headed"],
    )

    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tool_list = (await session.list_tools()).tools
            tools = mcp_tools_to_anthropic(tool_list)
            print(f"Connected to Playwright MCP — {len(tools)} tools available.")
            print(f"Goal: {goal}\n")

            messages: list[dict] = [{"role": "user", "content": goal}]

            for step in range(1, MAX_STEPS + 1):
                resp = client.messages.create(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    system=SYSTEM_PROMPT,
                    tools=tools,
                    messages=messages,
                )
                messages.append({"role": "assistant", "content": resp.content})

                # Print any text the model emitted this turn
                for block in resp.content:
                    if block.type == "text" and block.text.strip():
                        print(f"\n🤖 {block.text.strip()}\n")

                if resp.stop_reason != "tool_use":
                    print("✅ Done.")
                    return

                # Execute every tool call the model requested this turn
                tool_results = []
                for block in resp.content:
                    if block.type != "tool_use":
                        continue

                    print(f"[{step}] → {block.name}({_short(block.input)})")
                    allowed, reason = guardrail_check(block.name, block.input)
                    if not allowed:
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": [{"type": "text", "text": f"BLOCKED: {reason}"}],
                            "is_error": True,
                        })
                        continue

                    try:
                        result = await session.call_tool(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": mcp_result_to_content(result),
                            "is_error": bool(getattr(result, "isError", False)),
                        })
                    except Exception as e:  # keep the loop alive on a single failure
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": [{"type": "text", "text": f"ERROR: {e}"}],
                            "is_error": True,
                        })

                messages.append({"role": "user", "content": tool_results})

            print(f"⛔ Hit MAX_STEPS ({MAX_STEPS}) without finishing.")


def _short(d: dict, n: int = 80) -> str:
    s = ", ".join(f"{k}={v!r}" for k, v in d.items())
    return s if len(s) <= n else s[:n] + "…"


if __name__ == "__main__":
    task = " ".join(sys.argv[1:]) or input("What should the agent research? ")
    asyncio.run(run_agent(task))
