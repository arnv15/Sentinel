/**
 * MockMart shared logic — cart state, rendering helpers, header/footer chrome.
 *
 * NO BACKEND. Cart state lives in localStorage under `mockmart.cart` so it can
 * survive a page navigation; nothing is ever sent over the network. Clearing
 * site data resets the store completely.
 *
 * Markup notes for the browser agent: every interactive control is a real
 * <button> or <a>, every input has a <label for>, and action buttons carry an
 * aria-label naming the product. That is what shows up in Playwright's
 * accessibility tree, which is how the agent targets things.
 */

const CART_KEY = "mockmart.cart";
const ORDER_KEY = "mockmart.lastOrder";
const TAX_RATE = 0.0825;
const SHIP_FLAT = 5.99;
const FREE_SHIP_OVER = 35;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const money = (n) => "$" + Number(n).toFixed(2);
const qs = (k) => new URLSearchParams(location.search).get(k);
const byId = (id) => PRODUCTS.find((p) => p.id === id);
const catById = (id) => CATEGORIES.find((c) => c.id === id);

/** Inline SVG placeholder — keeps the site self-contained (no image requests). */
function placeholder(p, size = 320) {
  const initials = p.name.replace(/[^A-Za-z0-9 ]/g, "").split(/\s+/).slice(0, 2)
    .map((w) => w[0].toUpperCase()).join("");
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 320 320">` +
    `<rect width="320" height="320" fill="${p.color}"/>` +
    `<text x="160" y="160" font-family="system-ui,sans-serif" font-size="96" font-weight="700" ` +
    `fill="#ffffff" fill-opacity="0.85" text-anchor="middle" dominant-baseline="central">${initials}</text>` +
    `</svg>`;
  return "data:image/svg+xml;utf8," + encodeURIComponent(svg);
}

function stars(rating) {
  const full = Math.round(rating);
  return "★".repeat(full) + "☆".repeat(5 - full);
}

// ---------------------------------------------------------------------------
// Cart state
// ---------------------------------------------------------------------------

function getCart() {
  try {
    const raw = localStorage.getItem(CART_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function saveCart(cart) {
  localStorage.setItem(CART_KEY, JSON.stringify(cart));
  paintCartBadge();
}

function cartCount() {
  return getCart().reduce((n, l) => n + l.qty, 0);
}

/**
 * Add to cart, clamped by both the product's purchase limit and its stock.
 * Returns {ok, message} so the caller can show a status the agent can read.
 */
function addToCart(id, qty = 1) {
  const p = byId(id);
  if (!p) return { ok: false, message: "Product not found." };
  if (p.stock === 0) return { ok: false, message: "Out of stock — cannot add to cart." };

  const cart = getCart();
  const line = cart.find((l) => l.id === id);
  const current = line ? line.qty : 0;
  const ceiling = Math.min(p.limit, p.stock);

  if (current >= ceiling) {
    return { ok: false, message: `Limit reached — max ${ceiling} per order for this item.` };
  }
  const want = Math.min(current + qty, ceiling);
  if (line) line.qty = want;
  else cart.push({ id, qty: want });
  saveCart(cart);

  const added = want - current;
  return { ok: true, message: `Added ${added} × ${p.name} to cart. Cart now has ${cartCount()} item(s).` };
}

function setQty(id, qty) {
  const p = byId(id);
  const cart = getCart();
  const line = cart.find((l) => l.id === id);
  if (!line || !p) return;
  const ceiling = Math.min(p.limit, p.stock);
  line.qty = Math.max(1, Math.min(qty, ceiling));
  saveCart(cart);
}

function removeFromCart(id) {
  saveCart(getCart().filter((l) => l.id !== id));
}

function clearCart() {
  saveCart([]);
}

function cartTotals() {
  const lines = getCart().map((l) => {
    const p = byId(l.id);
    return { ...l, product: p, lineTotal: p.price * l.qty };
  });
  const subtotal = lines.reduce((s, l) => s + l.lineTotal, 0);
  const shipping = subtotal === 0 || subtotal >= FREE_SHIP_OVER ? 0 : SHIP_FLAT;
  const tax = subtotal * TAX_RATE;
  return { lines, subtotal, shipping, tax, total: subtotal + shipping + tax };
}

// ---------------------------------------------------------------------------
// Chrome — header, footer, cart badge
// ---------------------------------------------------------------------------

function paintCartBadge() {
  const el = document.getElementById("cart-count");
  if (el) {
    const n = cartCount();
    el.textContent = String(n);
    el.setAttribute("aria-label", `${n} item${n === 1 ? "" : "s"} in cart`);
  }
}

function renderHeader(active = "") {
  const navLinks = CATEGORIES.map(
    (c) =>
      `<a href="category.html?cat=${c.id}"${active === c.id ? ' aria-current="page"' : ""}>${c.name}</a>`
  ).join("");

  document.getElementById("site-header").innerHTML = `
    <div class="demo-banner" role="note">
      <strong>DEMO STORE.</strong> MockMart is a fake storefront used to test an automated
      browser agent. Nothing here is real — no products ship, no payment is processed,
      and no data leaves your browser. Do not enter real personal or payment information.
    </div>
    <header class="topbar">
      <div class="bar-inner">
        <a class="brand" href="index.html" aria-label="MockMart home">
          <span class="brand-mark" aria-hidden="true">M</span> MockMart
        </a>
        <form class="search" action="search.html" method="get" role="search">
          <label for="q" class="sr-only">Search products</label>
          <input type="search" id="q" name="q" placeholder="Search products…" autocomplete="off">
          <button type="submit">Search</button>
        </form>
        <nav class="account-nav" aria-label="Account">
          <a href="signin.html">Sign In</a>
          <a class="cart-link" href="cart.html" aria-label="View cart">
            Cart <span id="cart-count" class="badge">0</span>
          </a>
        </nav>
      </div>
      <nav class="catnav" aria-label="Categories">${navLinks}</nav>
    </header>`;
  paintCartBadge();
}

function renderFooter() {
  const el = document.getElementById("site-footer");
  if (!el) return;
  el.innerHTML = `
    <footer class="footer">
      <p><strong>MockMart</strong> — a static test fixture. No backend, no database, no payments.</p>
      <p>Built to exercise the Sentinel browser agent loop. Cart state is stored only in your browser.</p>
    </footer>`;
}

// ---------------------------------------------------------------------------
// Product card
// ---------------------------------------------------------------------------

function productCard(p) {
  const out = p.stock === 0;
  const low = p.stock > 0 && p.stock <= 5;
  return `
    <li class="card">
      <a class="card-img" href="product.html?id=${p.id}" tabindex="-1" aria-hidden="true">
        <img src="${placeholder(p)}" alt="" width="320" height="320" loading="lazy">
      </a>
      <div class="card-body">
        <h3 class="card-title"><a href="product.html?id=${p.id}">${p.name}</a></h3>
        <p class="rating" aria-label="Rated ${p.rating} out of 5 from ${p.reviews} reviews">
          <span aria-hidden="true">${stars(p.rating)}</span> ${p.rating} (${p.reviews})
        </p>
        <p class="price">${money(p.price)}</p>
        <p class="stock ${out ? "oos" : low ? "low" : "in"}">
          ${out ? "Out of stock" : low ? `Only ${p.stock} left` : `In stock (${p.stock})`}
        </p>
        <button class="btn add" data-add="${p.id}" ${out ? "disabled" : ""}
                aria-label="Add ${p.name} to cart">
          ${out ? "Out of Stock" : "Add to Cart"}
        </button>
      </div>
    </li>`;
}

/** Wire every [data-add] button on the page and announce the result. */
function wireAddButtons(statusId = "status") {
  document.querySelectorAll("[data-add]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const res = addToCart(btn.getAttribute("data-add"), 1);
      const status = document.getElementById(statusId);
      if (status) {
        status.textContent = res.message;
        status.className = "status " + (res.ok ? "ok" : "warn");
      }
    });
  });
}
