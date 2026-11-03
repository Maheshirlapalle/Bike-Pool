"use strict";
/* BikePool frontend. All user-supplied text is inserted with textContent (never innerHTML) to prevent XSS. */

const $ = (sel) => document.querySelector(sel);
const inr = (n) => "₹" + Number(n).toLocaleString("en-IN");
const CITIES = ["Hyderabad", "Bengaluru", "Mumbai", "Delhi", "Chennai", "Pune", "Kolkata", "Ahmedabad", "Vijayawada", "Visakhapatnam"];
const TIMELINES = { "2_weeks": "Within 2 weeks", "1_month": "Within a month", "3_months": "Within 3 months", just_exploring: "Just exploring" };
const STATUS = { open: "Collecting buyers", negotiating: "Negotiating with dealers", deal_secured: "Deal secured", closed: "Closed" };

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(kid));
  return el;
}

async function api(path, opts = {}) {
  const res = await fetch("/api" + path, { headers: { "Content-Type": "application/json", ...(opts.headers || {}) }, ...opts });
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    const msg = Array.isArray(d) ? d.map((e) => (e.msg || "").replace(/^Value error, /, "")).join(". ") : d || "Something went wrong";
    throw new Error(msg);
  }
  return data;
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove("show"), 3200);
}

/* ---- local storage of the buyer's own memberships (secret never leaves this browser except as a header) ---- */
const store = {
  all() { try { return JSON.parse(localStorage.getItem("bikepool.memberships") || "[]"); } catch { return []; } },
  save(list) { try { localStorage.setItem("bikepool.memberships", JSON.stringify(list)); } catch { /* private mode */ } },
  add(m) { this.save([...this.all().filter((x) => x.member_id !== m.member_id), m]); },
  remove(id) { this.save(this.all().filter((x) => x.member_id !== id)); },
};

const state = { brand: "", brands: [] };

/* ---- tabs ---- */
document.querySelectorAll("#tabs button").forEach((b) =>
  b.addEventListener("click", () => showTab(b.dataset.tab)));

function showTab(name) {
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach((s) => (s.hidden = s.id !== "tab-" + name));
  $("#hero").hidden = name !== "new";
  if (name === "pools") loadPools();
  if (name === "mine") loadMine();
}

/* ---- catalog ---- */
async function loadStats() {
  const s = await api("/stats");
  const items = [[s.interested_buyers, "interested buyers"], [s.active_pools, "active pools"], [s.deals_secured, "deals secured"], [s.bikes, "bikes · " + s.brands + " brands"]];
  $("#stats").replaceChildren(...items.map(([n, label]) => h("li", {}, h("b", {}, n), h("span", {}, label))));
}

async function loadBrands() {
  state.brands = await api("/brands");
  renderBrands();
}

function renderBrands() {
  const chip = (slug, label, count) =>
    h("button", { class: "chip" + (state.brand === slug ? " active" : ""), role: "listitem", onclick: () => { state.brand = slug; renderBrands(); loadBikes(); } },
      label, h("small", {}, count));
  const total = state.brands.reduce((a, b) => a + b.bike_count, 0);
  $("#brands").replaceChildren(chip("", "All brands", total), ...state.brands.map((b) => chip(b.slug, b.name, b.bike_count)));
}

async function loadCategories() {
  const cats = await api("/categories");
  $("#category").append(...cats.map((c) => h("option", { value: c }, c[0].toUpperCase() + c.slice(1))));
}

async function loadBikes() {
  const p = new URLSearchParams();
  if (state.brand) p.set("brand", state.brand);
  for (const id of ["q", "category", "fuel", "sort"]) if ($("#" + id).value) p.set(id, $("#" + id).value.trim());
  const bikes = await api("/bikes?" + p);
  $("#bikes").replaceChildren(...(bikes.length ? bikes.map(bikeCard) : [h("p", { class: "empty" }, "No bikes match these filters.")]));
}

function bikeCard(b) {
  const spec = [b.category, b.engine_cc ? b.engine_cc + " cc" : "Electric"].join(" · ");
  return h("article", { class: "card" },
    h("span", { class: "brand" }, b.brand.name),
    h("h3", {}, b.model),
    h("span", { class: "meta" }, spec),
    h("span", { class: "price" }, "≈ " + inr(b.approx_price_inr)),
    h("span", { class: "interest" }, h("b", {}, b.interested), b.interested === 1 ? " buyer interested" : " buyers interested"),
    h("button", { class: "primary", onclick: () => openBike(b.id) }, "See group & join"));
}

/* ---- pools ---- */
function progress(pct) { return h("div", { class: "progress", role: "progressbar", "aria-valuenow": pct, "aria-valuemin": 0, "aria-valuemax": 100 }, h("i", { style: `width:${pct}%` })); }

function dealLine(p) {
  if (p.status !== "deal_secured") return null;
  return h("p", {}, h("b", {}, "Deal: " + inr(p.discount_inr) + " off"), " via " + p.dealer_name + (p.deal_valid_until ? " · valid until " + p.deal_valid_until : ""));
}

async function loadPools() {
  const pools = await api("/pools");
  $("#pools").replaceChildren(...(pools.length ? pools.map((p) =>
    h("article", { class: "card" },
      h("span", { class: "brand" }, p.bike.brand + " · " + p.city),
      h("h3", {}, p.bike.model),
      h("span", { class: "badge " + p.status }, STATUS[p.status]),
      progress(p.progress_pct),
      h("span", { class: "interest" }, h("b", {}, p.member_count), " of " + p.target_size + " buyers"),
      dealLine(p),
      h("button", { class: "ghost", onclick: () => openBike(p.bike.id, p.city) }, "View & join"))) :
    [h("p", { class: "empty" }, "No pools yet. Open a bike and be the first to join.")]));
}

/* ---- bike dialog ---- */
async function openBike(id, preferCity = "") {
  const b = await api("/bikes/" + id);
  const dlg = $("#bikeDialog");
  const details = await Promise.all(b.pools.map((p) => api("/pools/" + p.id)));
  const cityInput = h("input", { name: "city", list: "cities", required: true, autocomplete: "off", value: preferCity, placeholder: "e.g. Hyderabad" });
  const datalist = h("datalist", { id: "cities" }, ...Array.from(new Set([...b.pools.map((p) => p.city), ...CITIES]), (c) => h("option", { value: c })));
  const err = h("p", { class: "error", role: "alert" });
  const submit = h("button", { class: "primary full", type: "submit" }, "Join the pool — free");

  const form = h("form", { class: "form", novalidate: true, onsubmit: async (e) => {
    e.preventDefault();
    err.textContent = ""; submit.disabled = true;
    const f = Object.fromEntries(new FormData(form));
    try {
      const r = await api("/pools/join", { method: "POST", body: JSON.stringify({ bike_id: id, ...f }) });
      store.add({ member_id: r.member_id, secret: r.member_secret, bike: b.brand.name + " " + b.model, city: r.pool.city });
      toast("You're in! Track it under “My pools”.");
      loadStats(); loadBikes();
      openBike(id, r.pool.city);
    } catch (ex) { err.textContent = ex.message; } finally { submit.disabled = false; }
  } },
    h("label", {}, "Your name", h("input", { name: "name", required: true, autocomplete: "name" })),
    h("label", {}, "Mobile number", h("input", { name: "phone", required: true, inputmode: "tel", autocomplete: "tel", placeholder: "10-digit number" })),
    h("label", {}, "City", cityInput, datalist),
    h("label", {}, "Buying", h("select", { name: "timeline" }, ...Object.entries(TIMELINES).map(([v, l]) => h("option", { value: v, selected: v === "1_month" }, l)))),
    h("label", { class: "full" }, "Email (optional)", h("input", { name: "email", type: "email", autocomplete: "email" })),
    err, h("p", { class: "muted full", style: "margin:0;font-size:.85rem" }, "Joining is free. A small success fee (₹199) is due only if we secure a discount for your group. Your phone is visible only to our team."), submit);

  const poolsEl = details.length ? details.map((p) =>
    h("div", { class: "pool-box" + (p.city === preferCity ? " selected" : "") },
      h("div", { style: "display:flex;justify-content:space-between;gap:8px" }, h("b", {}, p.city), h("span", { class: "badge " + p.status }, STATUS[p.status])),
      progress(p.progress_pct),
      h("span", { class: "interest" }, h("b", {}, p.member_count), " of " + p.target_size + " buyers"),
      dealLine(p),
      h("div", { class: "people", "aria-label": "Interested buyers" }, ...p.members.map((m) => h("span", { title: TIMELINES[m.timeline] }, m.display_name))))) :
    [h("p", { class: "muted" }, "No one has joined yet — be the first buyer in your city.")];

  dlg.replaceChildren(h("div", { class: "dlg" },
    h("header", {}, h("div", {}, h("span", { class: "brand", style: "color:var(--accent);font-weight:700;text-transform:uppercase;font-size:.75rem" }, b.brand.name),
      h("h2", { id: "dlgTitle" }, b.model), h("p", { class: "muted", style: "margin:4px 0 0" }, [b.category, b.engine_cc ? b.engine_cc + " cc" : "Electric", "≈ " + inr(b.approx_price_inr) + " ex-showroom"].join(" · "))),
      h("button", { class: "x", "aria-label": "Close", onclick: () => dlg.close() }, "×")),
    h("h3", {}, "Interested buyers by city"), ...poolsEl,
    h("h3", {}, "Join a pool"), form));
  if (!dlg.open) dlg.showModal();
}

/* ---- my pools ---- */
async function loadMine() {
  const list = store.all();
  if (!list.length) { $("#mine").replaceChildren(h("p", { class: "empty" }, "You haven't joined any pool in this browser yet.")); return; }
  const cards = await Promise.all(list.map(async (m) => {
    try {
      const fee = await api(`/members/${m.member_id}/fee`, { headers: { "X-Member-Secret": m.secret } });
      return mineCard(m, fee);
    } catch { store.remove(m.member_id); return null; }  // entry no longer exists on the server
  }));
  $("#mine").replaceChildren(...cards.filter(Boolean));
}

function mineCard(m, fee) {
  const leave = h("button", { class: "ghost danger", onclick: async () => {
    if (!confirm("Leave this pool?")) return;
    try { await api("/members/" + m.member_id, { method: "DELETE", headers: { "X-Member-Secret": m.secret } }); store.remove(m.member_id); toast("You left the pool."); loadMine(); loadStats(); loadBikes(); }
    catch (ex) { toast(ex.message); }
  } }, "Leave pool");
  const body = [h("span", { class: "brand" }, m.city), h("h3", {}, m.bike), h("span", { class: "badge " + fee.fee_status }, { not_due: "No fee due yet", pending: "Fee pending", paid: "Fee paid" }[fee.fee_status])];
  if (fee.fee_status === "not_due") body.push(h("p", { class: "muted" }, "We'll notify you if a deal is secured. Pool status: " + STATUS[fee.pool_status] + "."));
  if (fee.fee_status === "pending") body.push(
    h("p", {}, h("b", {}, inr(fee.discount_inr) + " off"), " via " + fee.dealer_name + ". Pay the " + inr(fee.amount_inr) + " fee to claim it:"),
    h("img", { class: "qr", alt: "UPI QR code for the platform fee", src: fee.qr_png }),
    h("a", { href: fee.upi_uri }, "Open in UPI app (mobile)"),
    h("p", { class: "muted", style: "font-size:.85rem" }, "After paying, our team confirms receipt manually."));
  if (fee.fee_status === "paid") body.push(h("p", {}, "Thanks! Our team will connect you with " + (fee.dealer_name || "the dealer") + "."));
  if (fee.fee_status !== "paid") body.push(leave);
  return h("article", { class: "card" }, ...body);
}

/* ---- wiring ---- */
let debounce;
$("#q").addEventListener("input", () => { clearTimeout(debounce); debounce = setTimeout(loadBikes, 250); });
["category", "fuel", "sort"].forEach((id) => $("#" + id).addEventListener("change", loadBikes));
$("#bikeDialog").addEventListener("click", (e) => { if (e.target === e.currentTarget) e.currentTarget.close(); });

Promise.all([loadStats(), loadBrands(), loadCategories(), loadBikes()]).catch((e) => toast(e.message));
