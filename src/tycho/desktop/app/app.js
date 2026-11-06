// The app window's page (ADR 17). Everything shown comes from the bridge
// (tycho.desktop.bridge) and every change goes back through it: the page holds no
// settings of its own. Without pywebview (a test, a preview), window.TYCHO_FAKE stands in.
(() => {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const esc = (t) => String(t ?? "").replace(/[&<>"]/g, (m) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[m]);
  const page = $("#page");
  let api = null;
  let view = "status";
  let tab = "Voice and sounds";
  let data = null;

  /* ---------- toast with undo ---------- */
  let toastTimer = 0;
  function toast(text, undo = false) {
    $("#toastText").textContent = text;
    $("#toastUndo").hidden = !undo;
    $("#toast").hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => ($("#toast").hidden = true), 4500);
  }
  $("#toastUndo").addEventListener("click", async () => {
    const r = await api.undo();
    $("#toast").hidden = true;
    if (r.ok) toast(`${label(r.name)} put back.`);
    else toast(r.problem);
    await render();
  });

  /* ---------- status ---------- */
  async function status() {
    page.innerHTML = `<h1>Status</h1><p class="lede">Checking…</p>`;
    const findings = await api.status();
    const bad = findings.filter((f) => f.level !== "ok").length;
    badge(bad);
    const mark = { ok: "✓", warn: "!", fail: "✕" };
    page.innerHTML = `<h1>Status</h1>
      <p class="lede">${bad ? `${bad} thing${bad > 1 ? "s" : ""} to look at.` : "Everything is working."} The same checks as <span class="num">tycho check</span>.</p>
      <div class="checks">${findings.map((f) => `<div class="check ${esc(f.level)}"><span class="mark">${mark[f.level] || "·"}</span>
        <span><b>${esc(f.what)}</b><small>${esc(f.detail)}</small>${f.fix ? `<span class="fix">${esc(f.fix)}</span>` : ""}</span></div>`).join("")}</div>
      <div class="bar"><span class="dim" style="font-size:13px">Tycho also checks each time it starts.</span><button class="btn frame" type="button" id="again">Check again</button></div>`;
    $("#again").addEventListener("click", status);
  }
  function badge(bad) {
    $("#statusBadge").textContent = bad ? `${bad} !` : "";
  }

  /* ---------- settings ---------- */
  const label = (name) => {
    for (const g of data?.groups || []) for (const r of g.rows) if (r.name === name) return r.label;
    return name;
  };
  function chips(r) {
    const out = [];
    if (r.voice === "no") out.push(`<span class="chip">Not by voice</span>`);
    else if (r.voice === "lower_only") out.push(`<span class="chip warn">By voice only ${r.kind === "switch" ? "off" : "lower"}</span>`);
    else out.push(`<span class="chip">By voice too</span>`);
    out.push(r.applies === "now" ? `<span class="chip good">Applies now</span>` : `<span class="chip">Applies at restart</span>`);
    return out.join("");
  }
  function control(r) {
    const v = r.value;
    switch (r.kind) {
      case "switch":
        return `<button class="switch" type="button" role="switch" aria-checked="${v === true}" aria-label="${esc(r.label)}" data-bool="${esc(r.name)}"></button>`;
      case "percent": {
        const lo = Math.round((r.minimum ?? 0) * 100), hi = Math.round((r.maximum ?? 1) * 100);
        return `<input type="range" min="${lo}" max="${hi}" step="5" value="${Math.round(v * 100)}" data-pct="${esc(r.name)}" aria-label="${esc(r.label)}"><span class="num val">${Math.round(v * 100)}%</span>`;
      }
      case "number":
        return `<input type="range" min="${r.minimum ?? 0}" max="${r.maximum ?? 100}" step="1" value="${v}" data-num="${esc(r.name)}" aria-label="${esc(r.label)}"><span class="num val">${v} s</span>`;
      case "choice":
        return `<div class="seg" role="group" aria-label="${esc(r.label)}">${r.choices.map((c) => `<button type="button" aria-pressed="${v === c}" data-pick="${esc(r.name)}" data-v="${esc(c)}">${esc(c)}</button>`).join("")}</div>`;
      case "setup":
        return `<span class="setup"><span class="num">${esc(v ?? "found by itself")}</span>${esc(r.how || "Changed in setup")}</span>`;
      default:
        return `<input class="input" value="${esc(v ?? "")}" data-text="${esc(r.name)}" aria-label="${esc(r.label)}" style="max-width:220px">`;
    }
  }
  const row = (r) => `<div class="set"><div><b>${esc(r.label)}</b>${r.description ? `<p>${esc(r.description)}</p>` : ""}<div class="meta">${chips(r)}</div></div><div class="ctl">${control(r)}</div></div>`;

  async function settingsView() {
    data = await api.settings();
    const names = [...data.groups.map((g) => g.name), "Keys"];
    if (!names.includes(tab)) tab = names[0];
    const tabs = `<div class="tabs" role="tablist">${names.map((n) => `<button type="button" role="tab" aria-selected="${n === tab}" data-tab="${esc(n)}">${esc(n)}</button>`).join("")}</div>`;
    let body;
    if (tab === "Keys") body = await keysView();
    else {
      const group = data.groups.find((g) => g.name === tab);
      const rows = `<div class="rows">${group.rows.map(row).join("")}</div>`;
      body = tab === "The Tile"
        ? `<div class="tilepage">${rows}<div class="preview"><img id="tilePreview" alt="The Tile as it's set now, over a cockpit"><span class="caption">Preview · drawn by the Tile itself · 2560 × 1440</span></div></div>`
        : rows;
    }
    page.innerHTML = `<h1>Settings</h1><p class="lede">Changes apply at once and are saved to settings.toml. Most can also be said to Tycho; each says how.</p>${tabs}${body}`;
    page.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => { tab = b.dataset.tab; render(); }));
    bindSettings();
    if (tab === "The Tile") preview();
    if (tab === "Keys") bindKeys();
  }
  async function preview() {
    const img = $("#tilePreview");
    if (!img) return;
    const width = Math.round(img.getBoundingClientRect().width * (window.devicePixelRatio || 1)) || 720;
    img.src = await api.tile_preview(width);
  }
  async function change(name, value, words) {
    const r = await api.set(name, value);
    if (!r.ok) { toast(r.problem); await render(); return; }
    if (!r.changed) return;
    toast(`${words}.${r.applies === "now" ? "" : " Applies when Tycho restarts."}`, true);
    await render();
  }
  function bindSettings() {
    page.querySelectorAll("[data-bool]").forEach((b) => b.addEventListener("click", () => {
      const on = b.getAttribute("aria-checked") !== "true";
      change(b.dataset.bool, on, `${label(b.dataset.bool)} ${on ? "on" : "off"}`);
    }));
    page.querySelectorAll("[data-pct]").forEach((r) => {
      r.addEventListener("input", () => (r.nextElementSibling.textContent = r.value + "%"));
      r.addEventListener("change", () => change(r.dataset.pct, Number(r.value) / 100, `${label(r.dataset.pct)} at ${r.value}%`));
    });
    page.querySelectorAll("[data-num]").forEach((r) => {
      r.addEventListener("input", () => (r.nextElementSibling.textContent = r.value + " s"));
      r.addEventListener("change", () => change(r.dataset.num, Number(r.value), `${label(r.dataset.num)}: ${r.value} s`));
    });
    page.querySelectorAll("[data-pick]").forEach((b) => b.addEventListener("click", () => change(b.dataset.pick, b.dataset.v, `${label(b.dataset.pick)}: ${b.dataset.v}`)));
    page.querySelectorAll("[data-text]").forEach((i) => i.addEventListener("change", () => change(i.dataset.text, i.value, `${label(i.dataset.text)}: ${i.value || "none"}`)));
  }

  /* ---------- keys ---------- */
  async function keysView() {
    const keys = await api.keys();
    return `<p class="note">Keys are stored in Windows Credential Manager, never in a file or a log, and never by voice. This window never shows one again.</p>
      <form class="card frame" id="keysForm" autocomplete="off">${Object.entries(keys).map(([id, k]) => `<div class="field"><label for="k-${id}">${esc(k.label)} key${id === "elevenlabs" ? " (optional)" : ""}</label>
        ${k.stored ? `<div class="keyrow"><span class="input num" style="flex:1 1 260px">•••• •••• ${esc(k.ends)}</span><span class="chip good">Stored</span><button class="btn frame" type="button" data-remove="${id}">Remove</button></div>`
          : `<div class="keyrow"><input class="input" id="k-${id}" type="password" spellcheck="false" placeholder="${id === "anthropic" ? "sk-ant-…" : "sk_…"}"><button class="btn primary frame" type="button" data-store="${id}">Check and store</button></div>`}
        <span class="err" id="e-${id}"></span></div>`).join("")}</form>`;
  }
  function bindKeys() {
    $("#keysForm")?.addEventListener("submit", (e) => e.preventDefault());
    page.querySelectorAll("[data-store]").forEach((b) => b.addEventListener("click", async () => {
      const id = b.dataset.store, input = $("#k-" + id);
      b.disabled = true; $("#e-" + id).textContent = "Checking…";
      const r = await api.store_key(id, input.value);
      input.value = "";
      b.disabled = false;
      if (!r.ok) { $("#e-" + id).textContent = r.problem; return; }
      toast("Key works, and is stored in Windows Credential Manager.");
      render();
    }));
    page.querySelectorAll("[data-remove]").forEach((b) => b.addEventListener("click", async () => {
      await api.remove_key(b.dataset.remove);
      toast("Key removed from Windows Credential Manager.");
      render();
    }));
  }

  /* ---------- shell ---------- */
  async function render() {
    document.querySelectorAll("nav [data-view]").forEach((b) => b.setAttribute("aria-current", b.dataset.view === view ? "page" : "false"));
    try {
      if (view === "status") await status();
      else await settingsView();
    } catch (e) {
      page.innerHTML = `<h1>Something went wrong</h1><p class="lede">${esc(e && e.message ? e.message : e)}</p>`;
    }
  }
  document.querySelectorAll("nav [data-view]").forEach((b) => b.addEventListener("click", () => { view = b.dataset.view; render(); }));

  async function start(bridge) {
    api = bridge;
    const about = await api.about();
    $("#ver").textContent = about.version;
    await render();
  }
  if (window.TYCHO_FAKE) start(window.TYCHO_FAKE);
  else if (window.pywebview && window.pywebview.api) start(window.pywebview.api);
  else window.addEventListener("pywebviewready", () => start(window.pywebview.api));
})();
