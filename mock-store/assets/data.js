/**
 * MockMart catalog — a static test fixture for the Sentinel browser agent.
 *
 * NOT A REAL STORE. No backend, no orders, no payments. Every page renders
 * from this array so the HTML files stay short.
 *
 * Deliberate test hooks:
 *   - `stock: 0`  -> out-of-stock items, so the agent must handle a dead end.
 *   - `limit`     -> per-order purchase caps the cart enforces.
 *   - Trading-card names match Sentinel's TITLE_KEYWORDS ("prismatic evolution",
 *     "destined rivals", "phantasmal flames") so Phase 1 -> Phase 2 can be
 *     tested end to end later.
 */

const CATEGORIES = [
  { id: "trading-cards", name: "Trading Cards", blurb: "Booster boxes, ETBs, and collector tins" },
  { id: "electronics", name: "Electronics", blurb: "TVs, audio, wearables, and accessories" },
  { id: "home-kitchen", name: "Home & Kitchen", blurb: "Small appliances, cookware, and bedding" },
  { id: "toys-games", name: "Toys & Games", blurb: "Building sets, board games, and outdoor play" },
];

const PRODUCTS = [
  // ---- Trading Cards (10) ----
  { id: "tc-001", cat: "trading-cards", name: "Prismatic Evolutions Elite Trainer Box", price: 49.99, stock: 8, limit: 2, sku: "94860231", rating: 4.7, reviews: 412, color: "#7c4dff",
    desc: "Elite Trainer Box with 9 booster packs, 65 card sleeves, 45 energy cards, and a player's guide." },
  { id: "tc-002", cat: "trading-cards", name: "Destined Rivals Booster Bundle", price: 26.99, stock: 15, limit: 3, sku: "94860232", rating: 4.5, reviews: 208, color: "#e53935",
    desc: "Six booster packs in a collector-friendly display bundle. Each pack contains 10 cards." },
  { id: "tc-003", cat: "trading-cards", name: "Phantasmal Flames Elite Trainer Box", price: 59.99, stock: 0, limit: 2, sku: "94860233", rating: 4.8, reviews: 96, color: "#fb8c00",
    desc: "Elite Trainer Box featuring the Phantasmal Flames expansion. Includes 9 boosters and premium accessories." },
  { id: "tc-004", cat: "trading-cards", name: "Prismatic Evolutions Booster Bundle", price: 29.99, stock: 4, limit: 2, sku: "94860234", rating: 4.6, reviews: 341, color: "#5e35b1",
    desc: "Six-pack booster bundle from the Prismatic Evolutions set." },
  { id: "tc-005", cat: "trading-cards", name: "Destined Rivals Elite Trainer Box", price: 54.99, stock: 22, limit: 2, sku: "94860235", rating: 4.4, reviews: 154, color: "#c62828",
    desc: "Elite Trainer Box with 9 boosters, dice, condition markers, and a deck box." },
  { id: "tc-006", cat: "trading-cards", name: "Phantasmal Flames Booster Bundle", price: 27.99, stock: 6, limit: 3, sku: "94860236", rating: 4.5, reviews: 77, color: "#ef6c00",
    desc: "Six booster packs from the Phantasmal Flames expansion." },
  { id: "tc-007", cat: "trading-cards", name: "Collector Chest Tin", price: 24.99, stock: 30, limit: 4, sku: "94860237", rating: 4.2, reviews: 512, color: "#00897b",
    desc: "Metal storage chest with 4 booster packs, stickers, and a mini portfolio." },
  { id: "tc-008", cat: "trading-cards", name: "Premium Binder Collection", price: 34.99, stock: 11, limit: 2, sku: "94860238", rating: 4.6, reviews: 189, color: "#3949ab",
    desc: "Nine-pocket zippered binder holding 360 cards, packaged with 3 booster packs." },
  { id: "tc-009", cat: "trading-cards", name: "Battle Deck 2-Pack", price: 19.99, stock: 18, limit: 5, sku: "94860239", rating: 4.1, reviews: 267, color: "#43a047",
    desc: "Two ready-to-play 60-card decks with damage counters and a quick-start guide." },
  { id: "tc-010", cat: "trading-cards", name: "Ultra Premium Collection Box", price: 119.99, stock: 2, limit: 1, sku: "94860240", rating: 4.9, reviews: 63, color: "#6d4c41",
    desc: "Limited collector box with 16 booster packs, two promo cards, and an art book." },

  // ---- Electronics (10) ----
  { id: "el-001", cat: "electronics", name: '55" 4K Smart TV', price: 349.99, stock: 5, limit: 2, sku: "58120011", rating: 4.3, reviews: 1204, color: "#263238",
    desc: "55-inch 4K UHD display with HDR support and built-in streaming apps." },
  { id: "el-002", cat: "electronics", name: "Wireless Noise-Cancelling Headphones", price: 179.99, stock: 14, limit: 3, sku: "58120012", rating: 4.6, reviews: 892, color: "#37474f",
    desc: "Over-ear headphones with active noise cancellation and 30-hour battery life." },
  { id: "el-003", cat: "electronics", name: "Portable Bluetooth Speaker", price: 39.99, stock: 27, limit: 4, sku: "58120013", rating: 4.4, reviews: 640, color: "#1e88e5",
    desc: "Waterproof speaker with 12-hour playback and a built-in microphone." },
  { id: "el-004", cat: "electronics", name: "Streaming Media Stick 4K", price: 49.99, stock: 33, limit: 3, sku: "58120014", rating: 4.5, reviews: 2310, color: "#455a64",
    desc: "Plug-in streaming player with 4K HDR output and a voice remote." },
  { id: "el-005", cat: "electronics", name: "Robot Vacuum", price: 229.99, stock: 7, limit: 1, sku: "58120015", rating: 4.2, reviews: 458, color: "#546e7a",
    desc: "Self-charging robot vacuum with app scheduling and multi-surface brushes." },
  { id: "el-006", cat: "electronics", name: "Smartwatch Series 6", price: 199.99, stock: 9, limit: 2, sku: "58120016", rating: 4.5, reviews: 731, color: "#00acc1",
    desc: "Fitness smartwatch with heart-rate tracking, GPS, and a 7-day battery." },
  { id: "el-007", cat: "electronics", name: "20000mAh Power Bank", price: 34.99, stock: 41, limit: 5, sku: "58120017", rating: 4.3, reviews: 1105, color: "#424242",
    desc: "High-capacity USB-C power bank with 20W fast charging and dual outputs." },
  { id: "el-008", cat: "electronics", name: "Mechanical Keyboard", price: 89.99, stock: 12, limit: 3, sku: "58120018", rating: 4.7, reviews: 386, color: "#212121",
    desc: "Tenkeyless mechanical keyboard with hot-swappable switches and RGB lighting." },
  { id: "el-009", cat: "electronics", name: "1080p Webcam", price: 59.99, stock: 19, limit: 3, sku: "58120019", rating: 4.1, reviews: 274, color: "#607d8b",
    desc: "Full-HD webcam with autofocus, dual microphones, and a privacy shutter." },
  { id: "el-010", cat: "electronics", name: "Wireless Earbuds", price: 129.99, stock: 0, limit: 2, sku: "58120020", rating: 4.4, reviews: 967, color: "#0277bd",
    desc: "True-wireless earbuds with adaptive noise cancellation and a charging case." },

  // ---- Home & Kitchen (10) ----
  { id: "hk-001", cat: "home-kitchen", name: "6-Quart Air Fryer", price: 89.99, stock: 16, limit: 2, sku: "22450031", rating: 4.6, reviews: 1520, color: "#455a64",
    desc: "Six-quart basket air fryer with eight presets and a dishwasher-safe drawer." },
  { id: "hk-002", cat: "home-kitchen", name: "12-Cup Coffee Maker", price: 49.99, stock: 24, limit: 3, sku: "22450032", rating: 4.2, reviews: 843, color: "#4e342e",
    desc: "Programmable drip coffee maker with a reusable filter and keep-warm plate." },
  { id: "hk-003", cat: "home-kitchen", name: "Stand Mixer", price: 279.99, stock: 3, limit: 1, sku: "22450033", rating: 4.8, reviews: 612, color: "#c2185b",
    desc: "Tilt-head stand mixer with a 5-quart bowl and three attachments." },
  { id: "hk-004", cat: "home-kitchen", name: "10-Piece Nonstick Cookware Set", price: 119.99, stock: 8, limit: 2, sku: "22450034", rating: 4.3, reviews: 397, color: "#37474f",
    desc: "Ten-piece nonstick set including saucepans, skillets, and tempered-glass lids." },
  { id: "hk-005", cat: "home-kitchen", name: "Memory Foam Pillow 2-Pack", price: 34.99, stock: 35, limit: 4, sku: "22450035", rating: 4.1, reviews: 1088, color: "#78909c",
    desc: "Two ventilated memory-foam pillows with removable cooling covers." },
  { id: "hk-006", cat: "home-kitchen", name: "Cotton Bath Towel Set", price: 27.99, stock: 44, limit: 5, sku: "22450036", rating: 4.4, reviews: 725, color: "#00838f",
    desc: "Six-piece combed-cotton towel set: two bath, two hand, two washcloths." },
  { id: "hk-007", cat: "home-kitchen", name: "16-Piece Ceramic Dinnerware", price: 59.99, stock: 13, limit: 2, sku: "22450037", rating: 4.5, reviews: 289, color: "#8d6e63",
    desc: "Service for four: dinner plates, salad plates, bowls, and mugs." },
  { id: "hk-008", cat: "home-kitchen", name: "Electric Kettle", price: 34.99, stock: 21, limit: 3, sku: "22450038", rating: 4.6, reviews: 934, color: "#546e7a",
    desc: "1.7-liter stainless kettle with rapid boil and automatic shutoff." },
  { id: "hk-009", cat: "home-kitchen", name: "Storage Bin 3-Pack", price: 22.99, stock: 52, limit: 6, sku: "22450039", rating: 4.0, reviews: 456, color: "#9e9e9e",
    desc: "Three stackable 20-liter bins with snap-lock lids." },
  { id: "hk-010", cat: "home-kitchen", name: "15 lb Weighted Blanket", price: 54.99, stock: 0, limit: 2, sku: "22450040", rating: 4.5, reviews: 671, color: "#5d4037",
    desc: "Fifteen-pound glass-bead weighted blanket with a washable cover." },

  // ---- Toys & Games (10) ----
  { id: "tg-001", cat: "toys-games", name: "Building Blocks Space Set", price: 79.99, stock: 10, limit: 2, sku: "77310051", rating: 4.8, reviews: 512, color: "#1565c0",
    desc: "1,024-piece space station building set with four minifigures. Ages 8+." },
  { id: "tg-002", cat: "toys-games", name: "Board Game Night Bundle", price: 39.99, stock: 17, limit: 3, sku: "77310052", rating: 4.4, reviews: 328, color: "#2e7d32",
    desc: "Three family board games bundled together. Ages 6+, 2-6 players." },
  { id: "tg-003", cat: "toys-games", name: "RC Monster Truck", price: 44.99, stock: 9, limit: 2, sku: "77310053", rating: 4.2, reviews: 246, color: "#f4511e",
    desc: "2.4 GHz remote-control truck with all-terrain tires and a rechargeable battery." },
  { id: "tg-004", cat: "toys-games", name: '24" Plush Bear', price: 24.99, stock: 38, limit: 4, sku: "77310054", rating: 4.7, reviews: 889, color: "#a1887f",
    desc: "Twenty-four-inch stuffed bear in soft brushed plush. Ages 3+." },
  { id: "tg-005", cat: "toys-games", name: "Art Supply Mega Kit", price: 29.99, stock: 26, limit: 3, sku: "77310055", rating: 4.5, reviews: 431, color: "#8e24aa",
    desc: "150-piece art set with markers, colored pencils, pastels, and a carry case." },
  { id: "tg-006", cat: "toys-games", name: "1000-Piece Puzzle", price: 16.99, stock: 47, limit: 6, sku: "77310056", rating: 4.3, reviews: 205, color: "#00695c",
    desc: "Thousand-piece landscape jigsaw puzzle, finished size 27 x 20 inches." },
  { id: "tg-007", cat: "toys-games", name: "Action Figure 3-Pack", price: 32.99, stock: 14, limit: 3, sku: "77310057", rating: 4.1, reviews: 178, color: "#d84315",
    desc: "Three articulated 6-inch figures with accessories. Ages 4+." },
  { id: "tg-008", cat: "toys-games", name: "Science Experiment Lab", price: 49.99, stock: 6, limit: 2, sku: "77310058", rating: 4.6, reviews: 362, color: "#00838f",
    desc: "Thirty guided experiments covering chemistry, crystals, and volcanoes. Ages 8+." },
  { id: "tg-009", cat: "toys-games", name: "Dollhouse Deluxe", price: 99.99, stock: 4, limit: 1, sku: "77310059", rating: 4.7, reviews: 143, color: "#ec407a",
    desc: "Three-story wooden dollhouse with 15 furniture pieces. Ages 3+." },
  { id: "tg-010", cat: "toys-games", name: "Outdoor Play Tent", price: 37.99, stock: 20, limit: 3, sku: "77310060", rating: 4.0, reviews: 97, color: "#7cb342",
    desc: "Pop-up play tent with a carry bag and mesh windows. Ages 3+." },
];
