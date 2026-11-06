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
    if (r.ok) toast(`${r.label || label(r.name)} put back.`);
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
      <div class="bar"><span class="dim" style="font-size:13px">Tycho also checks each time it starts.</span>
        <span class="keyrow"><button class="btn frame" type="button" id="redo">Run setup again</button><button class="btn frame" type="button" id="again">Check again</button></span></div>`;
    $("#again").addEventListener("click", status);
    $("#redo").addEventListener("click", () => { view = "setup"; step = 0; render(); });
  }

  /* ---------- setup: the first run, or again from Status ---------- */
  const STEPS = ["Game", "Push-to-talk", "Microphone", "Keys", "Done"];
  let step = 0;
  let firstRun = false;
  function stepper() {
    return `<ol class="steps">${STEPS.map((s, i) => `<li class="${i < step ? "done" : i === step ? "now" : ""}"><b>${i < step ? "✓" : i === STEPS.length - 1 ? "✦" : i + 1}</b>${esc(s)}</li>`).join("")}</ol>`;
  }
  function stepNav(nextOk, next = "Next") {
    return `<div class="bar"><button class="btn frame" type="button" data-step="-1" ${step === 0 ? "disabled" : ""}>Back</button>
      <button class="btn primary frame" type="button" data-step="1" ${nextOk ? "" : "disabled"}>${esc(next)}</button></div>`;
  }
  const levelWords = {
    good: "Good level: Tycho will hear you clearly.",
    quiet: "Quiet. Speak up, or raise the microphone's level in Windows' sound settings.",
    clipping: "Too loud: it's clipping. Lower the microphone's level in Windows.",
    silent: "Heard nothing. Say something while it listens, and check the microphone isn't muted.",
  };
  async function setupView() {
    const s = await api.setup_state();
    const lede = firstRun
      ? "A few steps, about two minutes. Tycho starts listening when you're done."
      : "Change anything you chose the first time. Changes apply when Tycho restarts.";
    let body = "";
    let ok = true;
    if (step === 0) {
      ok = s.game.found;
      body = `<div class="card frame"><h3>Which game?</h3>
        <div class="seg" role="group" aria-label="Game channel">${s.channels.map((c) => `<button type="button" aria-pressed="${c === s.game.channel}" data-channel="${esc(c)}">${esc(c)}</button>`).join("")}</div>
        ${s.game.found ? `<p class="ok-line">✓ Found Star Citizen ${esc(s.game.channel)} in <span class="num">${esc(s.game.folder)}</span></p>`
          : `<p class="err">${esc(s.game.problem)}. Choose the folder it's in (the RSI Launcher shows it under Settings, Games, Library Folder).</p>`}
        <div class="keyrow"><button class="btn frame" type="button" id="folder">Choose the folder…</button><span class="err" id="folderErr"></span></div>
        <p class="note">Tycho reads your keybinds from the game's files and never changes them.</p></div>`;
    } else if (step === 1) {
      ok = s.push_to_talk.set;
      body = `<div class="card frame"><h3>Push-to-talk</h3>
        <p>Hold it, speak, let go. A mouse side button or a spare joystick button works well.</p>
        <div class="ptt"><span class="key num" id="pttName">${esc(s.push_to_talk.name || "None yet")}</span>
          <button class="btn primary frame" type="button" id="capture">${s.push_to_talk.set ? "Choose another button" : "Choose a button"}</button></div>
        ${s.push_to_talk.conflicts.map((c) => `<p class="warn-line">${esc(c)}</p>`).join("")}</div>`;
    } else if (step === 2) {
      body = `<div class="card frame"><h3>Microphone</h3>
        ${micSelect(s.microphone)}
        <div class="keyrow"><button class="btn frame" type="button" id="micTest">Test: say something</button><span class="note" id="micMsg">It listens for a moment and tells you how you sound.</span></div></div>`;
    } else if (step === 3) {
      body = `<p class="note">An Anthropic key lets Tycho understand anything said your way and answer questions; without one, only the built-in phrases work. ElevenLabs gives Tycho its voice; without it, Tycho answers with sounds and the Tile.</p>${await keysView()}`;
    } else {
      ok = s.ready;
      body = `<div class="card frame"><h3>${firstRun ? "Ready" : "All set"}</h3>
        <p>${firstRun ? "Tycho starts listening when you press Start. Its star sits by the clock: click it to open this window again." : "Changes apply when Tycho next starts."}</p>
        <p class="note">Play in Borderless Window mode so the Tile can show over the game. Then hold your button and try “gear down”.</p>
        <span class="err" id="finishErr"></span></div>`;
    }
    const last = step === STEPS.length - 1;
    page.innerHTML = `<h1>${firstRun ? "Welcome to Tycho" : "Setup"}</h1><p class="lede">${lede}</p>${stepper()}${body}${stepNav(ok, last ? (firstRun ? "Start Tycho" : "Done") : "Next")}`;
    bindSetup(s);
  }
  function micSelect(mic) {
    const options = [["", "Windows' default microphone"], ...mic.devices.map((d) => [d, d])];
    // A saved microphone that isn't listed (a number, part of a name, or unplugged now)
    // is shown as itself, so choosing nothing never overwrites it.
    if (mic.setting && !mic.devices.includes(mic.setting)) options.push([mic.setting, `${mic.setting} (saved, not connected now)`]);
    const later = firstRun ? "" : `<span class="note">Plugged one in just now? Restart Tycho to see it here.</span>`;
    return `<select class="input" id="mic" aria-label="Microphone">${options.map(([v, l]) => `<option value="${esc(v)}" ${v === mic.setting ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>${later}`;
  }
  function bindSetup() {
    page.querySelectorAll("[data-step]").forEach((b) => b.addEventListener("click", async () => {
      const delta = Number(b.dataset.step);
      if (delta > 0 && step === STEPS.length - 1) {
        b.disabled = true;
        const r = await api.finish_setup();
        if (!r.ok) { $("#finishErr").textContent = r.problem; b.disabled = false; return; }
        if (!firstRun) { view = "status"; render(); }
        else page.innerHTML = `<h1>Starting Tycho…</h1><p class="lede">This window closes in a moment. Tycho's star appears by the clock.</p>`;
        return;
      }
      step = Math.max(0, Math.min(STEPS.length - 1, step + delta));
      render();
    }));
    page.querySelectorAll("[data-channel]").forEach((b) => b.addEventListener("click", async () => {
      const r = await api.set_channel(b.dataset.channel);
      if (!r.ok) toast(r.problem);
      render();
    }));
    $("#folder")?.addEventListener("click", chooseFolder);
    $("#capture")?.addEventListener("click", () => capture($("#capture"), $("#pttName")));
    bindMic();
    if (step === 3) bindKeys();
  }
  async function chooseFolder() {
    const r = await api.choose_game_folder();
    if (!r.ok && !r.cancelled) $("#folderErr").textContent = r.problem;
    else if (r.ok) {
      if (r.applies === "restart") toast("Game folder saved. Applies when Tycho restarts.", true);
      render();
    }
  }
  let capturing = false;
  async function capture(button, nameEl) {
    // While it listens, the rest of the page waits: leaving would save the next key
    // pressed (Ctrl+V into a key box) as push-to-talk. Cancel stops it.
    capturing = true;
    document.body.classList.add("capturing");
    button.disabled = true;
    nameEl.textContent = "Press the button now…";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "btn frame";
    cancel.id = "cancelCapture";
    cancel.textContent = "Cancel";
    cancel.addEventListener("click", () => api.cancel_capture());
    button.after(cancel);
    let r;
    try {
      r = await api.capture_push_to_talk();
    } catch (e) {
      r = { ok: false, problem: `Tycho couldn't listen for a button: ${e && e.message ? e.message : e}` };
    } finally {
      capturing = false;
      document.body.classList.remove("capturing");
      cancel.remove();
      button.disabled = false;
    }
    if (r.cancelled) { render(); return; }
    if (!r.ok) { toast(r.problem); render(); return; }
    toast(`Push-to-talk: ${r.name}.${r.conflicts.length ? " It also does something in the game." : ""}${r.applies === "restart" ? " Applies when Tycho restarts." : ""}`, true);
    render();
  }
  function bindMic() {
    $("#mic")?.addEventListener("change", async (e) => {
      const r = await api.set_microphone(e.target.value);
      toast(r.ok ? `Microphone: ${e.target.value || "Windows' default"}.${r.applies === "restart" ? " Applies when Tycho restarts." : ""}` : r.problem, r.ok);
    });
    $("#micTest")?.addEventListener("click", async () => {
      const b = $("#micTest"), msg = $("#micMsg");
      b.disabled = true; msg.textContent = "Listening… say something.";
      const r = await api.microphone_level();
      b.disabled = false;
      msg.textContent = r.ok ? levelWords[r.verdict] || r.verdict : r.problem;
      msg.className = r.ok && r.verdict === "good" ? "ok-line" : "warn-line";
    });
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
        if (r.name === "push_to_talk")
          return `<span class="setup"><span class="num" id="setPtt">${esc(v ?? "None yet")}</span><button class="btn frame" type="button" id="setCapture">Change</button></span>`;
        if (r.name === "microphone")
          return `<span class="setup" style="width:100%">${micSelect({ setting: v ?? "", devices: setupState?.microphone.devices || [] })}<button class="btn frame" type="button" id="micTest">Test</button><span class="note" id="micMsg"></span></span>`;
        return `<span class="setup"><span class="num">${esc(v ?? "found by itself")}</span><button class="btn frame" type="button" id="setFolder">Choose…</button><span class="err" id="folderErr"></span></span>`;
      default:
        return `<input class="input" value="${esc(v ?? "")}" data-text="${esc(r.name)}" aria-label="${esc(r.label)}" style="max-width:220px">`;
    }
  }
  const row = (r) => `<div class="set"><div><b>${esc(r.label)}</b>${r.description ? `<p>${esc(r.description)}</p>` : ""}<div class="meta">${chips(r)}</div></div><div class="ctl">${control(r)}</div></div>`;

  let setupState = null;
  async function settingsView() {
    data = await api.settings();
    if (tab === "Game and controls") setupState = await api.setup_state();
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
    $("#setCapture")?.addEventListener("click", () => capture($("#setCapture"), $("#setPtt")));
    $("#setFolder")?.addEventListener("click", chooseFolder);
    bindMic();
  }

  /* ---------- keys ---------- */
  async function keysView() {
    const keys = await api.keys();
    return `<p class="note">Keys are stored in Windows Credential Manager, never in a file or a log, and never by voice. This window never shows one again.</p>
      <form class="card frame" id="keysForm" autocomplete="off">${Object.entries(keys).map(([id, k]) => `<div class="field"><label for="k-${id}">${esc(k.label)} key${id === "elevenlabs" ? " (optional)" : ""}</label>
        ${k.stored ? `<div class="keyrow"><span class="input num" style="flex:1 1 260px">•••• •••• ${esc(k.ends)}</span><span class="chip good">Stored</span><button class="btn frame" type="button" data-remove="${id}">Remove</button></div>`
          : `<div class="keyrow"><input class="input" id="k-${id}" type="password" spellcheck="false" placeholder="${id === "anthropic" ? "sk-ant-…" : "sk_…"}"><button class="btn primary frame" type="button" data-store="${id}">Check and store</button></div>`}
        <span class="err" id="e-${id}">${esc(k.problem || "")}</span></div>`).join("")}</form>`;
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
      toast("Key works, and is stored in Windows Credential Manager. Tycho uses it from its next start.");
      render();
    }));
    page.querySelectorAll("[data-remove]").forEach((b) => b.addEventListener("click", async () => {
      try {
        const r = await api.remove_key(b.dataset.remove);
        toast(r.ok ? "Key removed from Windows Credential Manager. Tycho stops using it from its next start." : r.problem);
      } catch (e) {
        toast(`Couldn't remove the key: ${e && e.message ? e.message : e}`);
      }
      render();
    }));
  }

  /* ---------- commands ---------- */
  let cmdFilter = "all", cmdQuery = "", cmdOpen = "", cmdData = null;
  async function commandsView(keepFocus = false) {
    // Typing in the search box filters what was fetched; everything else fetches again.
    const d = keepFocus && cmdData ? cmdData : (cmdData = await api.commands());
    const rows = d.rows;
    const noKey = rows.filter((r) => !r.sequence && !r.key && d.game).length;
    $("#noKeyBadge").textContent = noKey ? `${noKey} no key` : "";
    const q = cmdQuery.trim().toLowerCase();
    const shown = rows.filter((r) => {
      if (cmdFilter === "nokey" && (r.sequence || r.key || !d.game)) return false;
      if (cmdFilter === "mine" && !r.mine.length) return false;
      if (cmdFilter === "sequences" && !r.sequence) return false;
      return !q || [r.id, r.label, ...r.said, ...r.mine].join(" ").toLowerCase().includes(q);
    });
    const counts = { all: rows.length, nokey: noKey, mine: rows.filter((r) => r.mine.length).length, sequences: rows.filter((r) => r.sequence).length };
    const filters = [["all", "All"], ["nokey", "No key"], ["mine", "Yours"], ["sequences", "Sequences"]];
    page.innerHTML = `<h1>Commands</h1>
      <p class="lede">What you can say, what it does in the game, and your key for it. Built-in phrases are instant; anything else goes to Claude. Your phrases are saved to <span class="num">${esc(d.file)}</span> and apply when Tycho restarts.</p>
      ${d.problem ? `<p class="err">${esc(d.problem)}</p>` : ""}${d.warnings.map((w) => `<p class="warn-line">${esc(w)}</p>`).join("")}
      ${d.game ? "" : `<p class="warn-line">Star Citizen wasn't found, so keys can't be shown. Set the game folder in Settings.</p>`}
      <div class="toolbar"><input class="input" id="cmdSearch" type="search" placeholder="Search: gear, doors, quantum…" value="${esc(cmdQuery)}" aria-label="Search commands">
        <div class="seg" role="group" aria-label="Show">${filters.map(([k, l]) => `<button type="button" aria-pressed="${cmdFilter === k}" data-filter="${k}">${l} ${counts[k]}</button>`).join("")}</div></div>
      <div class="tablewrap"><table><thead><tr><th>Command</th><th>Say it</th><th>Your key</th><th>Notes</th></tr></thead><tbody>
      ${shown.map(cmdRow).join("") || `<tr><td colspan="4" class="dim">Nothing matches. Try another word.</td></tr>`}
      </tbody></table></div>
      <p class="note" style="margin-top:10px">A phrase you add is taken away from any built-in command that had it. To make a new command or sequence, edit the file (the Tycho agent skill can help).</p>`;
    bindCommands();
    if (keepFocus) { const s = $("#cmdSearch"); s.focus(); s.setSelectionRange(s.value.length, s.value.length); }
  }
  function cmdRow(r) {
    const phrases = r.said.map((p) => `<span>${esc(p)}</span>`).join("") +
      r.mine.map((p) => `<span class="mine" title="Yours">${esc(p)}<button type="button" aria-label="Remove ${esc(p)}" data-remove-phrase="${esc(r.id)}" data-phrase="${esc(p)}">×</button></span>`).join("");
    const add = cmdOpen === r.id
      ? `<form class="addp" data-add="${esc(r.id)}"><input class="input" placeholder="What you'll say" aria-label="New phrase for ${esc(r.label)}" maxlength="60"><button class="btn frame" type="submit">Add</button></form>`
      : "";
    const key = r.sequence ? `<span class="dim">its steps' keys</span>` : r.key ? esc(r.key) : `<span class="chip warn">No key</span>`;
    const notes = [];
    if (r.confirm) notes.push(`<span class="chip bad">Asks first</span>`);
    if (r.toggle) notes.push(`<span class="dim" style="font-size:13px">Uses a toggle, which can flip the wrong way.</span>`);
    if (r.to_bind) notes.push(`<span class="dim" style="font-size:13px">Bind <b>${esc(r.to_bind)}</b> in the game's keybindings. Tycho won't press a near miss.</span>`);
    if (r.sequence) notes.push(`<span class="steps-line">${r.steps.map(esc).join(" → ")}</span>`);
    return `<tr><td><b>${esc(r.label)}</b><div class="num dim" style="font-size:12px">${esc(r.id)}</div></td>
      <td><div class="phr">${phrases}<button class="plus" type="button" data-open="${esc(r.id)}">+ phrase</button></div>${add}</td>
      <td class="key">${key}</td><td>${notes.join(" ")}</td></tr>`;
  }
  function bindCommands() {
    const s = $("#cmdSearch");
    s.addEventListener("input", () => { cmdQuery = s.value; commandsView(true); });
    page.querySelectorAll("[data-filter]").forEach((b) => b.addEventListener("click", () => { cmdFilter = b.dataset.filter; render(); }));
    page.querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", async () => {
      cmdOpen = b.dataset.open; await render(); page.querySelector(`[data-add="${CSS.escape(cmdOpen)}"] input`)?.focus();
    }));
    page.querySelectorAll("[data-add]").forEach((f) => f.addEventListener("submit", async (e) => {
      e.preventDefault();
      const phrase = f.querySelector("input").value;
      const r = await api.add_phrase(f.dataset.add, phrase);
      if (!r.ok) { toast(r.problem); return; }
      cmdOpen = "";
      const said = phrase.trim().toLowerCase();
      toast(r.took_from ? `Added. “${said}” was ${r.took_from.replaceAll("_", " ")}'s; it's this command's now. Applies when Tycho restarts.` : "Added. Applies when Tycho restarts.");
      render();
    }));
    page.querySelectorAll("[data-remove-phrase]").forEach((b) => b.addEventListener("click", async () => {
      const r = await api.remove_phrase(b.dataset.removePhrase, b.dataset.phrase);
      toast(r.ok ? "Removed. Applies when Tycho restarts." : r.problem);
      render();
    }));
  }

  /* ---------- shell ---------- */
  async function render() {
    if (capturing) api.cancel_capture();
    document.querySelectorAll("nav [data-view]").forEach((b) => b.setAttribute("aria-current", b.dataset.view === view ? "page" : "false"));
    try {
      if (view === "status") await status();
      else if (view === "setup") await setupView();
      else if (view === "commands") await commandsView();
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
    firstRun = Boolean(about.first_run);
    if (firstRun) view = "setup";
    await render();
  }
  if (window.TYCHO_FAKE) start(window.TYCHO_FAKE);
  else if (window.pywebview && window.pywebview.api) start(window.pywebview.api);
  else window.addEventListener("pywebviewready", () => start(window.pywebview.api));
})();
