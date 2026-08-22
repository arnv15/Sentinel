# MockMart — test storefront for the browser agent

A **static, fake** e-commerce site used to exercise the Phase 2 browser agent
loop end to end: browse → search → product page → add to cart → checkout →
order confirmation.

It is deliberately **not** a clone of any real retailer. It has its own made-up
brand, a permanent demo banner, and a checkout that submits nowhere — so it is
safe to host on a public URL like GitHub Pages. Pointing an automated agent at a
real store's checkout risks placing real orders; this exists so you never have to.

## What's here

```
mock-store/
├── index.html          # home — department tiles + featured row
├── category.html       # ?cat=<id>  — 10 products per department
├── product.html        # ?id=<id>   — detail page, qty picker, add to cart
├── search.html         # ?q=<term>  — client-side search over the catalog
├── cart.html           # line items, qty edit, remove, totals
├── checkout.html       # shipping + payment form (submits NOWHERE)
├── confirmation.html   # fake order number
├── signin.html         # accepts anything, authenticates nobody
└── assets/
    ├── data.js         # 40 products across 4 departments
    ├── store.js        # cart state, rendering helpers, header/footer
    └── styles.css
```

**No backend, no database, no network calls.** Cart state lives in `localStorage`
(`mockmart.cart`); the fake order lives in `mockmart.lastOrder`. Clearing site
data resets everything. Product images are inline SVG data URIs, so the page
makes zero external requests.

Each HTML file is a thin shell that renders from `data.js` — that's what keeps
the markup short with 40 products in the catalog.

## Run it locally

```bash
cd mock-store && python3 -m http.server 8000
```

Then point the agent at `http://localhost:8000/index.html`.

```bash
python src/browser_agent.py "Go to http://localhost:8000, find the Prismatic Evolutions Elite Trainer Box, add one to the cart, and tell me the cart total"
```

## Publish to GitHub Pages

Every path is relative, so the site works unchanged from a subdirectory. In the
repo settings enable Pages for the `main` branch, root folder — the store is then
at `https://<user>.github.io/Sentinel/mock-store/`. No build step.

## Built-in test scenarios

The catalog has hooks so the agent hits more than the happy path:

| Scenario | Where |
|---|---|
| Purchase limit clamps quantity | `tc-001` limit 2 — asking for 5 adds 2 and says so |
| Limit already reached | Add `tc-001` twice — second attempt is refused with a readable message |
| Out of stock (dead end) | `tc-003`, `el-010`, `hk-010` — button disabled, reads "Out of Stock" |
| Low-stock urgency | Any item with `stock <= 5` renders "Only N left" |
| Free-shipping threshold | Subtotal ≥ $35 flips shipping from $5.99 to FREE |
| Empty cart | Checkout with nothing in the cart shows a dead end, not a crash |
| Search miss | `search.html?q=zzzz` returns a no-results page |

## How it interacts with the agent's guardrails

`RISKY_KEYWORDS` in `src/browser_agent.py` currently matches `submit`, `confirm`,
`delete`, `remove`, `send`, `post`, `publish`, `subscribe`, `agree`, `accept`.
Against this store that means:

- **"Remove" in the cart → pauses for y/N.** Good — exercises the guardrail path.
- **"Add to Cart", "Proceed to Checkout", "Place Order", "Sign In" → do not pause,**
  because the purchase/login keywords were intentionally removed for testing.
  The agent will walk straight through checkout unattended.

That is exactly why this fixture exists. Restore those keywords before pointing
the agent at anything real.

## Accessibility is the point

The agent reads Playwright's accessibility tree, not pixels — so this fixture
uses real `<button>`/`<a>` elements, a `<label for>` on every input, landmark
regions, and `aria-label`s that name the product (e.g. *"Add Prismatic Evolutions
Elite Trainer Box to cart"*). That gives the agent unambiguous targets. If you add
pages, keep that up or the fixture stops being a useful test.

## Safety notes

- The checkout form has **no `action`, no `fetch`, no XHR**. `preventDefault()`
  stops the submit and fakes a result locally.
- Payment fields are pre-filled with the standard `4111 1111 1111 1111` dummy
  test card and marked `autocomplete="off"`. **Never type real card or personal
  details here** — and if you extend this, don't add anything that transmits.
- Every page carries a demo banner and `<meta name="robots" content="noindex">`.
