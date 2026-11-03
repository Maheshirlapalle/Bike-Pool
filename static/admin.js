"use strict";
/* Admin console: review pools, record the negotiated deal, confirm fee payments.
   The token is held in memory only (never persisted) and sent as an X-Admin-Token header. */

const $ = (s) => document.querySelector(s);
let token = "";

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v != null && v !== false) el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null) el.append(kid.nodeType ? kid : document.createTextNode(kid));
  return el;
}

async function api(path, opts = {}) {
  const res = await fetch("/api/admin" + path, { ...opts, headers: { "Content-Type": "application/json", "X-Admin-Token": token } });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    throw new Error(Array.isArray(d) ? d.map((e) => e.msg).join(". ") : d || "Request failed");
  }
  return data;
}

function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.add("show"); setTimeout(() => t.classList.remove("show"), 3000); }

$("#login").addEventListener("submit", async (e) => {
  e.preventDefault();
  token = $("#token").value;
  try { await load(); $("#login").hidden = true; $("#pools").hidden = false; }
  catch (ex) { $("#err").textContent = ex.message; }
});

async function load() {
  const pools = await api("/pools");
  $("#pools").replaceChildren(...(pools.length ? pools.map(poolCard) : [h("p", { class: "empty" }, "No pools yet.")]));
}

function poolCard(p) {
  const f = {};
  const field = (name, label, type, value, extra = {}) => {
    f[name] = h("input", { type, value: value ?? "", ...extra });
    return h("label", {}, label, f[name]);
  };
  const status = h("select", {}, ...["open", "negotiating", "deal_secured", "closed"].map((s) => h("option", { value: s, selected: s === p.status }, s)));
  const msg = h("span", { class: "error" });
  const save = h("button", { class: "primary", onclick: async () => {
    const body = { status: status.value, target_size: Number(f.target.value) };
    if (f.dealer.value.trim()) body.dealer_name = f.dealer.value.trim();
    if (f.discount.value) body.discount_inr = Number(f.discount.value);
    if (f.until.value) body.deal_valid_until = f.until.value;
    try { await api("/pools/" + p.id, { method: "PATCH", body: JSON.stringify(body) }); toast("Saved"); load(); }
    catch (ex) { msg.textContent = ex.message; }
  } }, "Save");

  const rows = p.members.map((m) => h("tr", {},
    h("td", {}, m.name), h("td", {}, m.phone), h("td", {}, m.email || "—"), h("td", {}, m.timeline),
    h("td", {}, m.fee_status === "pending"
      ? h("button", { class: "ghost", onclick: async () => { try { await api(`/members/${m.id}/mark-paid`, { method: "POST" }); load(); } catch (ex) { toast(ex.message); } } }, "Mark paid")
      : m.fee_status)));

  return h("section", { class: "card", style: "margin-bottom:16px" },
    h("h3", {}, `${p.bike.brand} ${p.bike.model} — ${p.city}`),
    h("p", { class: "muted" }, `${p.member_count}/${p.target_size} buyers · price ≈ ₹${p.bike.approx_price_inr.toLocaleString("en-IN")}`),
    h("div", { class: "form" },
      h("label", {}, "Status", status), field("target", "Target size", "number", p.target_size, { min: 2 }),
      field("dealer", "Dealer name", "text", p.dealer_name), field("discount", "Discount per buyer (₹)", "number", p.discount_inr, { min: 1 }),
      field("until", "Deal valid until", "date", p.deal_valid_until), h("div", {}, save, " ", msg)),
    h("table", {}, h("thead", {}, h("tr", {}, ["Name", "Phone", "Email", "Buying", "Fee"].map((c) => h("th", {}, c)))), h("tbody", {}, rows)));
}
