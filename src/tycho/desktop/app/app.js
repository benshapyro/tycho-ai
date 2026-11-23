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
  let tab = "Voice";
  let data = null;

  /* ---------- toast with undo ---------- */
  let toastTimer = 0;
  function toast(text, undo = false) {
    $("#toastText").textContent = text;
    $("#toastUndo").hidden = !undo;
    $("#toast").hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => ($("#toast").hidden = true), undo ? 9000 : 4500); // Undo stays longer
  }
  $("#toastUndo").addEventListener("click", async () => {
    const r = await api.undo();
    $("#toast").hidden = true;
    if (r.ok) toast(`${r.label || label(r.name)} put back.`);
    else toast(r.problem);
    await render();
  });

  /* ---------- home: how Tycho is, at most one thing to fix, the checks one click away ---------- */
  let allChecks = false;
  async function status() {
    page.innerHTML = `<h1>Home</h1><p class="lede">Checking…</p>`;
    const [findings, live] = await Promise.all([api.status(), api.recent()]);
    const bad = findings.filter((f) => f.level !== "ok");
    const stuck = bad.some((f) => f.level === "fail");
    badge(bad.length);
    const mark = { ok: "✓", warn: "!", fail: "✕" };
    const first = bad.find((f) => f.level === "fail") || bad[0];
    const fix = first
      ? `<h2 class="label">${bad.length > 1 ? `One of ${bad.length} things to look at` : "One thing to look at"}</h2>
        <div class="onefix ${esc(first.level)}" id="oneFix"><div><b>${esc(first.what)}</b><small>${esc(first.detail)}</small>${first.fix ? `<span class="fix">${esc(first.fix)}</span>` : ""}</div>
        <button class="btn frame" type="button" id="again">Check again</button></div>`
      : "";
    homeLine = { stuck, button: live.button };
    page.innerHTML = `<div class="state frame" id="homeState"><div class="light"></div><div><h1></h1><p></p></div></div>
      ${fix}
      ${live.listening == null ? "" : `<h2 class="label">Last said</h2><div id="recent">${recentList(live.exchanges)}</div>`}
      <h2 class="label">Checks</h2>
      <div class="bar" style="margin-top:0"><button class="link" type="button" id="showChecks" aria-expanded="${allChecks}">${allChecks ? "Hide the checks" : `Show all ${findings.length} checks`}</button>
        <span class="keyrow"><button class="btn frame" type="button" id="redo">Run setup again</button>${first ? "" : `<button class="btn frame" type="button" id="again">Check again</button>`}</span></div>
      <div class="checks" id="checks" ${allChecks ? "" : "hidden"} style="margin-top:10px">${findings.map((f) => `<div class="check ${esc(f.level)}"><span class="mark">${mark[f.level] || "·"}</span>
        <span><b>${esc(f.what)}</b><small>${esc(f.detail)}</small>${f.fix ? `<span class="fix">${esc(f.fix)}</span>` : ""}</span></div>`).join("")}</div>
      <p class="note" style="margin-top:10px">The same checks as <span class="num">tycho check</span>. Tycho also checks each time it starts.</p>`;
    drawHomeState(live.listening);
    $("#again").addEventListener("click", status);
    $("#showChecks").addEventListener("click", () => { allChecks = !allChecks; status(); });
    $("#redo").addEventListener("click", () => { view = "setup"; step = 0; render(); });
  }

  // What the player said and what Tycho did, newest first: from memory, never saved.
  function recentList(exchanges, empty = "Nothing yet. What you say and what Tycho did shows here until Tycho quits. It's never saved.") {
    if (!exchanges.length) return `<p class="note">${esc(empty)}</p>`;
    const time = (at) => new Date(at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    return `<ol class="recent">${exchanges.map((e) => `<li class="${e.ok ? "ok" : "no"}"><span class="num">${esc(time(e.at))}</span>
      <span><b>“${esc(e.said)}”</b>${e.did ? `<small>${esc(e.did)}</small>` : ""}</span></li>`).join("")}</ol>`;
  }
  // Home's one line: ready or not, listening or paused, and the button to hold.
  let homeLine = { stuck: false, button: "" };
  function drawHomeState(listening) {
    const box = $("#homeState");
    if (!box) return;
    const { stuck, button } = homeLine;
    const paused = listening === false;
    const hold = button ? `Hold <b>${esc(button)}</b> and talk.` : "Hold your push-to-talk button and talk.";
    const [title, line] = stuck ? ["Not ready yet", "Tycho can't do everything yet: the first thing to fix is below."]
      : paused ? ["Paused", "Tycho isn't listening. Resume from Tycho's icon by the clock."]
      : [listening ? "Listening" : "Ready", `${hold} Tycho listens while this window is closed, too.`];
    box.classList.toggle("stuck", stuck);
    box.classList.toggle("paused", paused && !stuck);
    $("h1", box).textContent = title;
    $("p", box).innerHTML = line;
    box.dataset.listening = String(listening);
  }
  async function refreshRecent() {
    if (view !== "status" || !$("#recent") || document.hidden) return;
    const live = await api.recent(false);
    // Paused or resumed from the tray: only the line changes, not the checks.
    if (String(live.listening) !== $("#homeState")?.dataset.listening) drawHomeState(live.listening);
    const box = $("#recent");
    if (box) box.innerHTML = recentList(live.exchanges);
  }

  /* ---------- setup: the first run, or again from Status (#364) ----------
     The game comes first, and what's said about a game is its pack's own text (s.pack).
     Every step can be skipped: the last one says what Tycho still needs to start. */
  const STEPS = ["Game", "Keys", "Talk and listen", "Privacy", "Try it"];
  // What the bridge's to-do list calls a step, and the step it's on now.
  const STEP_OF = { "Push-to-talk": "Talk and listen", Microphone: "Talk and listen", Voice: "Talk and listen" };
  const stepOf = (name) => STEPS.indexOf(STEP_OF[name] || name);
  let step = 0;
  let firstRun = false;
  let canQuit = false;
  // Setup's Try it (ADR 17): while it's open, the running Tycho sends no keys. When it
  // was turned on (seconds since 1970), so only what was said since shows; null: off.
  let trying = null;
  const TRIED_EMPTY = "Nothing yet. Hold your button, say something, and let go.";
  const tried = (live) => live.exchanges.filter((e) => e.at >= trying);
  // Privacy questions a player set up before they existed was never asked: crash reports
  // (ADR 4) and looking at the game window (ADR 14, #31). Asked on their own, first.
  let asking = [];
  const ASKED = { crash_reports: "crash reports", look_at_screen: "looking at your game" };
  // One of them answered: once none is left, done (#442).
  function answeredAsked(name) {
    if (!asking.includes(name)) return;
    asking = asking.filter((a) => a !== name);
    if (!asking.length) view = "status";
  }
  // A step is ticked when what it asks for is there, not when it was walked past: a
  // skipped one keeps its number, marked as still to do, unless the game doesn't need it.
  function stepDone(name, s) {
    if (name === "Game") return !s.game.needed || s.game.found;
    if (name === "Keys") return brainKey(s);
    if (name === "Talk and listen") return s.push_to_talk.set;
    if (name === "Privacy") return s.crash_reports != null && (!s.can_look || s.look_at_screen != null);
    return true;
  }
  // Tycho presses keys in this game: the pack can, and the player's key actions are on.
  // With them off, the built-in phrases press nothing, so only the brain answers (#506).
  const presses = (p) => p.can_press && p.key_actions !== false;
  // Skipped on purpose and not needed: a brain key in a game where Tycho's built-in
  // phrases work without one (#448). Calm, not "still to do" every time setup opens.
  const stepOptional = (name, s) => name === "Keys" && presses(s.pack);
  // Why a brain key is all there is, where it is: no built-in phrases, or key actions off.
  const brainOnly = (p) => p.can_press
    ? `Key actions are off (Settings, Safety), so the built-in phrases press nothing: Tycho needs an Anthropic or OpenRouter key to answer in ${esc(p.name)}.`
    : `Tycho needs an Anthropic or OpenRouter key to answer in ${esc(p.name)}: it has no built-in phrases there.`;
  function stepper(s) {
    return `<ol class="steps">${STEPS.map((name, i) => {
      const past = i < step, done = past && stepDone(name, s);
      const skipped = past && !done && stepOptional(name, s);
      const cls = i === step ? "now" : done ? "done" : skipped ? "skipped" : past ? "todo" : "";
      const title = skipped ? ' title="Skipped. You can add it later."' : past && !done ? ' title="Still to do"' : "";
      return `<li class="${cls}"${title}><b>${done ? "✓" : skipped ? "–" : i === STEPS.length - 1 ? "✦" : i + 1}</b>${esc(name)}</li>`;
    }).join("")}</ol>`;
  }
  function stepNav(nextOk, next = "Next") {
    return `<div class="bar stepnav"><button class="btn frame" type="button" data-step="-1" ${step === 0 ? "disabled" : ""}>Back</button>
      <button class="btn primary frame" type="button" data-step="1" ${nextOk ? "" : "disabled"}>${esc(next)}</button></div>`;
  }
  const levelWords = {
    good: "Good level: Tycho will hear you clearly.",
    quiet: "Quiet. Speak up, or raise the microphone's level in Windows' sound settings.",
    clipping: "Too loud: it's clipping. Lower the microphone's level in Windows.",
    silent: "Heard nothing. Say something while it listens, and check the microphone isn't muted.",
  };
  const list = (items, cls = "") => items.length ? `<ul class="${cls}">${items.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>` : "";
  const modeLine = (p) => p.mode === "advisor" ? `Advisor only: Tycho presses no keys. ${esc(p.why)}` : `Tycho presses your keys.${p.why ? ` ${esc(p.why)}` : ""}`;
  // A game pack in its own words: what Tycho does there, whether it presses keys and
  // why, and what it can look up. `live`: the game is the chosen one, so its controls work.
  function packCard(p, game, live) {
    const keys = !p.can_press
      ? `<p class="info-line" id="packMode">Advisor only: Tycho presses no keys in ${esc(p.name)}. ${esc(p.why)}</p>`
      : live
        ? `<div class="set" id="packMode"><div><b>Let Tycho press keys</b><p>Every press goes through the safety gate, and anything you can't undo waits for you to say “confirm”. Off, Tycho only answers and advises.</p></div>
            <div class="ctl"><button class="switch" type="button" role="switch" aria-checked="${p.key_actions === true}" aria-label="Let Tycho press keys" data-bool="key_actions"></button></div></div>`
        : `<p class="${p.key_actions ? "ok-line" : "warn-line"}" id="packMode">${p.key_actions ? "Tycho presses your own keybinds there, through its safety gate." : "Key actions are off (Settings, Safety), so Tycho only answers and advises there."}</p>`;
    let folder = "";
    if (!game.needed) folder = `<p class="ok-line" id="packFolder">✓ Tycho needs nothing from the game's folder.</p>`;
    else if (!live) folder = `<p class="${game.found ? "ok-line" : "warn-line"}" id="packFolder">${game.found ? `✓ Found in <span class="num">${esc(game.folder)}</span>` : "Its folder wasn't found. Tycho asks for it next."}</p>`;
    else folder = `<div class="field" id="packFolder"><label>The game's folder</label>
        <div class="seg" role="group" aria-label="Game channel">${game.channels.map((c) => `<button type="button" aria-pressed="${c === game.channel}" data-channel="${esc(c)}">${esc(c)}</button>`).join("")}</div>
        ${game.found ? `<p class="ok-line">✓ Found ${esc(game.name)} ${esc(game.channel)} in <span class="num">${esc(game.folder)}</span></p>`
          : `<p class="err">${esc(game.problem)}. Choose the folder it's in.${p.folder_help ? ` ${esc(p.folder_help)}` : ""}</p>`}
        <div class="keyrow"><button class="btn frame" type="button" id="folder">Choose the folder…</button><span class="err" id="folderErr"></span></div>
        <p class="note">Tycho reads your keybinds from the game's files and never changes them.</p></div>`;
    return `<div class="card frame" id="packCard"><h3>Tycho in ${esc(p.name)}</h3>
      ${p.intro ? `<p>${esc(p.intro)}</p>` : ""}${keys}
      ${p.lookups.length ? `<div class="field"><label>What it can look up</label>${list(p.lookups)}</div>` : ""}${folder}</div>`;
  }
  const brainKey = (s) => ["anthropic", "openrouter"].some((k) => s.keys[k] && s.keys[k].stored && !s.keys[k].unusable);
  async function setupView() {
    const s = await api.setup_state();
    const lede = firstRun
      ? "A few steps, about three minutes. Skip any of them and come back; Tycho starts listening when you're done."
      : asking.length && STEPS[step] === "Privacy"
        ? `${asking.length > 1 ? "Two questions" : "One question"} left: ${asking.map((a) => ASKED[a]).join(" and ")}. The rest is as you chose it.`
        : "Change anything you chose the first time. Changes apply when Tycho restarts.";
    const name = STEPS[step];
    let body = "";
    let ok = true;
    if (name === "Game") {
      body = `<div class="card frame"><h3>Which game?</h3>
        <div class="packs" role="group" aria-label="Game">${s.packs.map((g) => `<button type="button" class="pack frame" aria-pressed="${g.id === s.pack.id}" data-game="${esc(g.id)}" data-name="${esc(g.name)}"><b>${esc(g.name)}</b><span class="note">${modeLine(g)}</span></button>`).join("")}</div>
        <p class="note">One game at a time. You can switch later with Switch game, at the side of this window.</p></div>
        ${packCard(s.pack, { ...s.game, channels: s.channels }, true)}`;
    } else if (name === "Keys") {
      body = `<div class="card frame"><h3>Your keys, and what they cost</h3>
        <p>Tycho is free and runs on your own accounts. Each key is optional, and you can add one later under Settings, Keys.</p>
        <ul class="costs">
          <li><b>Anthropic (Claude)</b>: lets Tycho understand anything said your way and answer questions. About $0.0002 a request, plus about $0.002 each time Tycho starts: an evening of play is usually a few cents.</li>
          <li><b>OpenRouter</b>, instead of Anthropic: the same Claude through one OpenRouter key (set Who runs the brain under Settings, Advanced). About what Anthropic charges, on your OpenRouter credit. Web search still needs the Anthropic key.</li>
          <li><b>ElevenLabs</b>: gives Tycho its voice. About 0.5 credits a character: a chatty hour is about 4,000 credits. Without it, Tycho answers with sounds.</li>
        </ul>
        <p class="${presses(s.pack) ? "note" : "warn-line"}" id="keysNote">${presses(s.pack) ? "Without an Anthropic or OpenRouter key, only the built-in phrases work." : brainOnly(s.pack)}</p></div>${await keysView()}`;
    } else if (name === "Talk and listen") {
      // Push-to-talk, the microphone and the voice: how you talk to Tycho and hear it back.
      const ptt = `<div class="card frame"><h3>Push-to-talk</h3>
        <p>Hold it, speak, let go. A mouse side button or a spare joystick button works well.</p>
        <div class="ptt"><span class="key num" id="pttName">${esc(s.push_to_talk.name || "None yet")}</span>
          <button class="btn primary frame" type="button" id="capture">${s.push_to_talk.set ? "Choose another button" : "Choose a button"}</button></div>
        ${s.push_to_talk.set ? "" : `<p class="note">Tycho can't start without one, but you can choose it later.</p>`}
        ${s.push_to_talk.conflicts.map((c) => `<p class="warn-line">${esc(c)}</p>`).join("")}</div>`;
      const mic = `<div class="card frame"><h3>Microphone</h3>
        ${micSelect(s.microphone)}
        <div class="keyrow"><button class="btn frame" type="button" id="micTest">Test: say something</button><span class="note" id="micMsg">It listens for a moment and tells you how you sound.</span></div></div>`;
      const v = s.voice;
      const mine = v.names.find((n) => n.toLowerCase() === v.setting.toLowerCase());
      const voice = `<div class="card frame"><h3>Tycho's voice</h3>
        ${v.key ? `<p>Pick who speaks for Tycho. Later you can just say “switch to Sarah”.</p>`
          : `<p class="warn-line" id="noVoice">No ElevenLabs key yet, so Tycho answers with sounds. Add one in the Keys step to hear a voice; what you pick here is kept.</p>`}
        <div class="seg" role="group" aria-label="Voice">${v.names.map((n) => `<button type="button" aria-pressed="${n === mine}" data-voice="${esc(n)}">${esc(n)}</button>`).join("")}</div>
        ${mine ? "" : `<p class="note" id="voiceNow">Now: ${esc(v.setting)}. Pick one of these to change it.</p>`}
        <p class="note">Its volume, and what Tycho calls you, are under Settings, Voice.</p></div>`;
      body = ptt + mic + voice;
    } else if (name === "Privacy") {
      const leaves = `<div class="card frame"><h3>What leaves your PC</h3>
        <p class="ok-line">✓ Your voice never does: speech recognition runs on this PC.</p>
        <p>Only text is sent${s.can_look ? ", and a picture of the game window only if you turn that on and ask for a look" : ""}, and only what's listed here.</p>
        <div class="tablewrap"><table class="leaves"><thead><tr><th>Who gets it</th><th>What</th><th>When</th></tr></thead><tbody>
        ${s.leaves.map((r) => `<tr><td><b>${esc(r.to)}</b></td><td>${esc(r.what)}</td><td class="dim">${esc(r.when)}</td></tr>`).join("")}</tbody></table></div>
        <p class="note">Tycho counts and tracks nothing about how you use it. Your keys stay in Windows Credential Manager, and a record of what you said is kept on this PC only if you turn it on (Settings, Privacy).</p></div>`;
      const crash = `<div class="card frame" id="crashCard"><h3>Send crash reports to help fix Tycho?</h3>
        <p>Your choice. Nothing is sent unless you say yes, and you can change it any time under Settings, Privacy.</p>
        <div class="seg big" role="group" aria-label="Send crash reports"><button type="button" aria-pressed="${s.crash_reports === false}" data-crash="no">No</button><button type="button" aria-pressed="${s.crash_reports === true}" data-crash="yes">Yes</button></div>
        <div class="field"><label>A report holds</label>${list(["The kind of error, and where in Tycho's own code it happened", "Tycho's version, the Windows version, the game and the brain provider"])}</div>
        <div class="field"><label>Never sent</label>${list(["What you or Tycho said, or anything sent to or from the brain", "Your keys", "Your name, what Tycho calls you, or your Windows user name", "Your files and folders, including the game's", "Your settings, log lines, or anything that identifies you or this PC"])}</div>
        <p class="note">Reports go to Sentry, the service Tycho's maintainer uses to collect them, when Tycho next starts after a crash. Saying no deletes any report still waiting.</p></div>`;
      // Equal choices, neither picked for them; skipped, it stays off (ADR 14, #31).
      const look = s.can_look ? `<div class="card frame" id="lookCard"><h3>Let Tycho look at your game window when you ask?</h3>
        <p>Say “take a look” and Tycho takes one picture of the game window, and sends it to the brain with your question. Only the game window, never saved. You can turn this off any time under Settings, Privacy, or by saying “stop looking at my screen”.</p>
        <div class="seg big" role="group" aria-label="Look at your game window when you ask"><button type="button" aria-pressed="${s.look_at_screen === false}" data-look="no">No</button><button type="button" aria-pressed="${s.look_at_screen === true}" data-look="yes">Yes</button></div>
        <div class="field"><label>Each time</label>${list(["Only when you ask: “take a look”, or “yes” when Tycho offers to look", "One picture of the game window, never the rest of your screen", "A camera sound plays, so it's never silent"])}</div>
        <div class="field"><label>Never</label>${list(["Taken by Tycho on its own", "Saved, logged, or kept for the next question"])}</div>
        <p class="note">The picture goes where what you say goes (the table above or below). Skip this and it stays off.</p></div>` : "";
      // Asked these questions alone (set up before they existed): they come first, not under the table.
      const cards = { crash_reports: crash, look_at_screen: look };
      const first = asking.map((a) => cards[a] || "").join("");
      const rest = Object.keys(cards).filter((a) => !asking.includes(a)).map((a) => cards[a]).join("");
      body = first + leaves + rest;
    } else {
      ok = s.ready;
      const say = s.pack.examples;
      let tryCard = "";
      if (!firstRun) {
        if (trying === null && document.hasFocus()) {
          const t = await api.try_it(true);
          // Left the step, or the window, while that was asked: off again (review of #464).
          const left = view !== "setup" || STEPS[step] !== "Try it";
          if (left || !document.hasFocus() || document.hidden) {
            if (t.ok) await api.try_it(false);
            if (left) return; // the new step stays drawn
          } else trying = t.ok ? t.since : null;
        }
        const live = trying === null ? null : await api.recent(false);
        if (!live && !document.hasFocus()) tryCard = `<div class="card frame" id="tryCard"><h3>Try it now</h3><p>Click here to try it: Tycho tries only while this window is in front.</p></div>`;
        if (live) tryCard = `<div class="card frame" id="tryCard"><h3>Try it now</h3>
          ${live.listening === false ? `<p class="warn-line">Tycho is paused. Resume it from its icon by the clock, then try.</p>`
            : `<p>Tycho is listening. Hold <b>${esc(s.push_to_talk.name || "your button")}</b>, say something, and let go: what it heard and what it would do shows here.</p>`}
          <p class="ok-line">✓ Nothing reaches the game while this step is open and in front: Tycho shows the keys it would press instead. Switch to the game, and Tycho presses keys again.</p>
          <div id="tried">${recentList(tried(live), TRIED_EMPTY)}</div>
          <p class="note">Tycho tries with the settings it started with. Changes you made here apply when it restarts.</p></div>`;
      }
      body = tryCard + `<div class="card frame"><h3>${s.ready ? "Ready" : "Almost there"}</h3>
        ${s.todo.length ? `<div id="todo"><p class="warn-line">Tycho still needs:</p>${s.todo.map((t) => `<div class="keyrow"><span>${esc(t.what)}</span><button class="btn frame" type="button" data-goto="${esc(t.step)}">Go to ${esc(STEP_OF[t.step] || t.step)}</button></div>`).join("")}
          <p class="note">Or close this window and finish later: what you've chosen is kept.</p></div>` : ""}
        ${say.length ? `<div class="field"><label>Hold your button and say</label>${list(say.map((e) => `“${e}”`), "say")}</div>` : `<p>Hold your button and ask Tycho something about ${esc(s.pack.name)}.</p>`}
        ${s.pack.can_press && !presses(s.pack) && brainKey(s) ? `<p class="note" id="keysOffSay">Key actions are off, so Tycho tells you the key for a command instead of pressing it.</p>` : ""}
        ${brainKey(s) ? "" : `<p class="warn-line" id="noBrain">${presses(s.pack) ? "With no Anthropic or OpenRouter key, only the built-in phrases work: What to say lists them." : `With no Anthropic or OpenRouter key${s.pack.can_press ? " and key actions off" : ""}, Tycho can't answer in ${esc(s.pack.name)}. Add one in the Keys step.`}</p>`}
        ${list(s.pack.tips, "tips")}
        <p class="note">${firstRun ? "Press Start Tycho. Once it's listening, this window opens again here so you can try it, with nothing sent to the game. Its star sits by the clock (if you don't see it, it's under the ^ arrow there): click it to open this window again, or open Tycho from the Start menu." : "Changes apply when Tycho next starts."}</p>
        <span class="err" id="finishErr"></span></div>`;
    }
    const last = step === STEPS.length - 1;
    page.innerHTML = `<h1>${firstRun ? "Welcome to Tycho" : "Setup"}</h1><p class="lede">${lede}</p>${stepper(s)}${body}${stepNav(ok, last ? (firstRun ? "Start Tycho" : "Done") : "Next")}`;
    bindSetup(s);
  }

  /* ---------- a new game, picked in Settings: its pack's own setup (#364) ---------- */
  let pendingPack = "";
  let packFrom = "settings"; // where Back goes
  async function packView() {
    const r = await api.pack_setup(pendingPack);
    if (!r.ok) { toast(r.problem); view = packFrom; return render(); }
    const p = r.pack;
    page.innerHTML = `<h1>Set up ${esc(p.name)}</h1>
      <p class="lede">What Tycho does in ${esc(p.name)}, before you switch. Switching restarts Tycho with that game's commands, look-ups and what its brain knows.</p>
      ${packCard(p, r.game, false)}
      <div class="card frame"><h3>Try it</h3>
        ${p.examples.length ? `<div class="field"><label>Hold your button and say</label>${list(p.examples.map((e) => `“${e}”`), "say")}</div>` : `<p>Hold your button and ask Tycho something about ${esc(p.name)}.</p>`}
        ${list(p.tips, "tips")}</div>
      <div class="bar"><button class="btn frame" type="button" id="packBack">Back</button>
        <button class="btn primary frame" type="button" id="packUse">Use ${esc(p.name)}</button></div>`;
    $("#packBack").addEventListener("click", () => { view = packFrom; packFrom = "settings"; render(); });
    $("#packUse").addEventListener("click", async () => {
      const from = packFrom;
      view = from === "setup" ? "status" : "settings";
      packFrom = "settings";
      await change("game", p.id, `Game: ${p.name}`, from); // a refusal goes back where it came from
    });
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
        else page.innerHTML = `<h1>Starting Tycho…</h1><p class="lede">This window closes in a moment, and opens again here once Tycho is listening, so you can try it. The first start downloads the speech model, which can take a few minutes: Tycho's star by the clock (or under the ^ arrow there) says how it's going.</p>`;
        return;
      }
      step = Math.max(0, Math.min(STEPS.length - 1, step + delta));
      render();
    }));
    page.querySelectorAll("[data-goto]").forEach((b) => b.addEventListener("click", () => { step = Math.max(0, stepOf(b.dataset.goto)); render(); }));
    page.querySelectorAll("[data-game]").forEach((b) => b.addEventListener("click", async () => {
      // Run again from Home, another game restarts Tycho: show its own setup first, as
      // Settings does, and switch only from there (#435).
      if (!firstRun && b.getAttribute("aria-pressed") !== "true") { pendingPack = b.dataset.game; packFrom = "setup"; view = "pack"; render(); return; }
      const r = await api.set("game", b.dataset.game);
      if (!r.ok) toast(r.problem);
      else if (r.changed && !firstRun) toast(`Game: ${b.dataset.name}.${r.applies === "restarting" ? " Tycho restarts with it now." : " Applies when Tycho restarts."}`, true);
      render();
    }));
    page.querySelectorAll("[data-voice]").forEach((b) => b.addEventListener("click", async () => {
      const r = await api.set("voice", b.dataset.voice);
      if (!r.ok) toast(r.problem);
      else if (r.changed && !firstRun) toast(`Voice: ${b.dataset.voice}. Applies when Tycho restarts.`, true);
      render();
    }));
    page.querySelectorAll("[data-bool]").forEach((b) => b.addEventListener("click", async () => {
      const on = b.getAttribute("aria-checked") !== "true";
      const r = await api.set(b.dataset.bool, on);
      if (!r.ok) toast(r.problem);
      else if (r.changed && !firstRun) toast(`${r.label} ${on ? "on" : "off"}. Applies when Tycho restarts.`, true);
      render();
    }));
    page.querySelectorAll("[data-crash]").forEach((b) => b.addEventListener("click", async () => {
      const on = b.dataset.crash === "yes";
      const r = await api.set("crash_reports", on);
      if (!r.ok) toast(r.problem);
      else if (r.changed && !firstRun) toast(`Crash reports ${on ? "on" : "off"}.`, true);
      if (r.ok) answeredAsked("crash_reports");
      render();
    }));
    page.querySelectorAll("[data-look]").forEach((b) => b.addEventListener("click", async () => {
      const on = b.dataset.look === "yes";
      const r = await api.set("look_at_screen", on);
      if (!r.ok) toast(r.problem);
      else if (r.changed && !firstRun) toast(`Looking at your game ${on ? "on" : "off"}.`, true);
      if (r.ok) answeredAsked("look_at_screen");
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
    if (STEPS[step] === "Keys") bindKeys();
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
        if (r.packs) return packChoice(r);
        return `<div class="seg" role="group" aria-label="${esc(r.label)}">${r.choices.map((c) => `<button type="button" aria-pressed="${v === c}" data-pick="${esc(r.name)}" data-v="${esc(c)}">${esc(c)}</button>`).join("")}</div>`;
      case "cards": {
        // A switch for each kind of card in this game: on shows it (#422).
        const hidden = new Set(String(v || "").split(",").map((k) => k.trim()).filter(Boolean));
        return `<div class="seg" role="group" aria-label="${esc(r.label)}">${r.cards.map((c) => `<button type="button" aria-pressed="${!hidden.has(c.kind)}" data-card="${esc(c.kind)}" data-name="${esc(c.name)}">${esc(c.name)}</button>`).join("")}</div>`;
      }
      case "callouts": {
        // A switch for each kind of event this game can speak up about, with its line (#4).
        if (!r.callouts.length) return `<span class="dim">This game has nothing to speak up about.</span>`;
        const on = new Set(String(v || "").split(",").map((k) => k.trim()).filter(Boolean));
        return `<div class="callouts">${r.callouts.map((c) => `<label class="callout"><button class="switch" type="button" role="switch" aria-checked="${on.has(c.kind)}" aria-label="${esc(c.name)}" data-callout="${esc(c.kind)}" data-name="${esc(c.name)}"></button><span>${esc(c.name)}<br><span class="dim">“${esc(c.example)}”</span></span></label>`).join("")}</div>`;
      }
      case "screen": // the screens plugged in now (#447)
        return `<select class="input" data-screen="${esc(r.name)}" aria-label="${esc(r.label)}">${r.screens.map((s) => `<option value="${esc(s.value)}" ${s.value === v ? "selected" : ""}>${esc(s.name)}</option>`).join("")}</select>`;
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
  // The Game setting: each game pack, and whether Tycho presses keys there, and why not (#295).
  const packChoice = (r) => `<div class="packs" role="group" aria-label="${esc(r.label)}">${r.packs.map((p) =>
    `<button type="button" class="pack frame" aria-pressed="${r.value === p.id}" data-pick="${esc(r.name)}" data-v="${esc(p.id)}" data-name="${esc(p.name)}">
      <b>${esc(p.name)}</b><span class="note">${modeLine(p)}</span></button>`).join("")}</div>`;
  const row = (r) => `<div class="set"><div><b>${esc(r.label)}</b>${r.description ? `<p>${esc(r.description)}</p>` : ""}<div class="meta">${chips(r)}</div></div><div class="ctl">${control(r)}</div></div>`;

  let setupState = null;
  async function settingsView(shared) {
    data = shared;
    if (tab === "Game and controls") setupState = await api.setup_state();
    // Advanced last: what most players never need to change.
    const names = [...data.groups.map((g) => g.name).filter((n) => n !== TILE && n !== ADVANCED), "Keys", "Updates", ADVANCED];
    if (!names.includes(tab)) tab = names[0];
    const tabs = `<div class="tabs" role="tablist">${names.map((n) => `<button type="button" role="tab" aria-selected="${n === tab}" data-tab="${esc(n)}">${esc(n)}</button>`).join("")}</div>`;
    let body;
    if (tab === "Keys") body = await keysView();
    else if (tab === "Updates") body = updatesView();
    else {
      const group = data.groups.find((g) => g.name === tab);
      // The game's own import of the player's hangar, in its own words (#556).
      const hangar = tab === "Game and controls" && data.hangar ? hangarRow(data.hangar) : "";
      const rows = `<div class="rows">${group.rows.map(row).join("")}${hangar}</div>`;
      body = tab === ADVANCED ? `<p class="note" style="margin:0 0 10px">Most people never need these.</p>${rows}` : rows;
    }
    page.innerHTML = `<h1>Settings</h1><p class="lede">Changes apply at once and are saved to settings.toml. Most can also be said to Tycho; each says how.</p>${tabs}${body}`;
    page.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => { tab = b.dataset.tab; render(); }));
    bindSettings();
    if (tab === "Keys") bindKeys();
    if (tab === "Updates") bindUpdates();
  }
  // Importing the player's hangar (#556): pick the export, see what changes, then save.
  const hangarRow = (h) => `<div class="set"><div><b>${esc(h.label)}</b><p>${esc(h.hint)}</p><div id="hangarPlan" aria-live="polite"></div></div><div class="ctl"><span class="setup"><button class="btn frame" type="button" id="hangarChoose">Import…</button><span class="err" id="hangarErr"></span></span></div></div>`;
  async function chooseHangar() {
    const b = $("#hangarChoose"), plan = $("#hangarPlan"), err = $("#hangarErr");
    err.textContent = ""; plan.innerHTML = "";
    b.disabled = true; b.textContent = "Reading…";
    const r = await api.choose_hangar_file();
    b.disabled = false; b.textContent = "Import…";
    if (r.cancelled) return;
    if (!r.ok) { err.textContent = r.problem; return; }
    const buttons = r.changes ? `<span class="setup"><button class="btn primary" type="button" id="hangarSave">Save</button><button class="btn frame" type="button" id="hangarCancel">Cancel</button></span>` : "";
    plan.innerHTML = `<ul class="tips">${r.lines.map((l) => `<li>${esc(l)}</li>`).join("")}</ul>${buttons}`;
    $("#hangarCancel")?.addEventListener("click", () => { plan.innerHTML = ""; });
    $("#hangarSave")?.addEventListener("click", async () => {
      const s = await api.save_hangar();
      if (!s.ok) { err.textContent = s.problem; return; }
      plan.innerHTML = "";
      toast(s.done);
    });
  }
  // The Tile's own place: its settings beside a preview drawn by the Tile itself.
  const TILE = "The Tile";
  const ADVANCED = "Advanced";
  async function tileView(shared) {
    data = shared;
    const group = data.groups.find((g) => g.name === TILE);
    page.innerHTML = `<h1>The Tile</h1><p class="lede">A small card over your game when there's something worth seeing, like a list of prices. It never takes a click away from the game.</p>
      <div class="tilepage"><div class="rows">${group ? group.rows.map(row).join("") : ""}</div><div class="preview"><img id="tilePreview" alt="The Tile as it's set now, over a cockpit"><span class="caption">Preview · drawn by the Tile itself · 2560 × 1440</span></div></div>`;
    bindSettings();
    preview();
  }
  async function preview() {
    const img = $("#tilePreview");
    if (!img) return;
    const width = Math.round(img.getBoundingClientRect().width * (window.devicePixelRatio || 1)) || 720;
    img.src = await api.tile_preview(width);
  }
  async function change(name, value, words, failed = null) {
    const r = await api.set(name, value);
    if (!r.ok) { if (failed) view = failed; toast(r.problem); await render(); return; }
    if (!r.changed) return;
    const when = { now: "", restarting: " Tycho restarts with it now." }[r.applies] ?? " Applies when Tycho restarts.";
    toast(`${words}.${when}`, true);
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
    page.querySelectorAll("[data-pick]").forEach((b) => b.addEventListener("click", () => {
      // Another game: its pack's own setup first, and the switch from there (#364).
      if (b.dataset.pick === "game" && b.getAttribute("aria-pressed") !== "true") { pendingPack = b.dataset.v; packFrom = "settings"; view = "pack"; render(); return; }
      change(b.dataset.pick, b.dataset.v, `${label(b.dataset.pick)}: ${b.dataset.name || b.dataset.v}`);
    }));
    page.querySelectorAll("[data-card]").forEach((b) => b.addEventListener("click", () => {
      const row = data.groups.flatMap((g) => g.rows).find((r) => r.name === "tile_hidden_cards");
      const hidden = new Set(String(row.value || "").split(",").map((k) => k.trim()).filter(Boolean));
      const showing = hidden.has(b.dataset.card);
      if (showing) hidden.delete(b.dataset.card); else hidden.add(b.dataset.card);
      row.value = [...hidden].join(", "); // a second quick click builds on this one (review of #466)
      change("tile_hidden_cards", row.value, `${b.dataset.name} ${showing ? "shown" : "hidden"} on the Tile`);
    }));
    page.querySelectorAll("[data-callout]").forEach((b) => b.addEventListener("click", () => {
      const row = data.groups.flatMap((g) => g.rows).find((r) => r.name === "callouts");
      const on = new Set(String(row.value || "").split(",").map((k) => k.trim()).filter(Boolean));
      const was = on.has(b.dataset.callout);
      if (was) on.delete(b.dataset.callout); else on.add(b.dataset.callout);
      row.value = [...on].join(", "); // a second quick click builds on this one
      change("callouts", row.value, `${b.dataset.name}: Tycho ${was ? "stays quiet" : "speaks up"}`);
    }));
    page.querySelectorAll("[data-screen]").forEach((sel) => sel.addEventListener("change", () => change(sel.dataset.screen, sel.value, `The Tile's screen: ${sel.selectedOptions[0].textContent}`)));
    page.querySelectorAll("[data-text]").forEach((i) => i.addEventListener("change", () => change(i.dataset.text, i.value, `${label(i.dataset.text)}: ${i.value || "none"}`)));
    $("#setCapture")?.addEventListener("click", () => capture($("#setCapture"), $("#setPtt")));
    $("#setFolder")?.addEventListener("click", chooseFolder);
    $("#hangarChoose")?.addEventListener("click", chooseHangar);
    bindMic();
  }

  /* ---------- updates: checked, never installed (#9) ---------- */
  function updatesView() {
    const auto = data.groups.flatMap((g) => g.rows).find((r) => r.name === "check_for_updates");
    return `<div class="card frame"><h3>You have Tycho <span class="num">${esc($("#ver").textContent)}</span></h3>
        <div class="keyrow"><button class="btn ${lastUpdate.state === "newer" ? "" : "primary "}frame" type="button" id="checkUpdates">Check for updates</button><span id="updateMsg"></span></div>
        <div class="keyrow" id="getRow" ${lastUpdate.state === "newer" ? "" : "hidden"}><button class="btn primary frame" type="button" id="getUpdate">${lastUpdate.state === "newer" ? `Get Tycho ${esc(lastUpdate.latest)}` : ""}</button><span class="err" id="getErr"></span></div>
        <p class="note">Tycho never updates itself. The release page has the installer: quit Tycho (below, or from its icon by the clock), run it, and it installs over this version. Your settings and keys stay.</p>
        <p class="note">${auto && auto.value ? "Tycho also checks each time its window opens, and says so at the side." : "Automatic checks are off (Privacy tab), so Tycho only checks when you press the button."}</p></div>
      ${canQuit ? `<div class="card frame"><h3>Quit Tycho</h3>
        <div class="keyrow"><button class="btn frame" type="button" id="quitTycho">Quit Tycho</button><span class="err" id="quitErr"></span></div>
        <p class="note">Stops listening and closes Tycho, the same as Quit on its icon by the clock. Closing this window alone leaves Tycho listening.</p></div>` : ""}`;
  }
  function updateWords(u) {
    if (u.state === "newer") return [`Tycho ${u.latest} is out.`, "warn-line"];
    if (u.state === "latest") return [`You have the latest version.`, "ok-line"];
    if (u.state === "none") return ["No release published yet.", "note"];
    return [u.problem || "Couldn't check.", "err"];
  }
  function bindUpdates() {
    $("#quitTycho")?.addEventListener("click", () => quit($("#quitTycho"), (t) => { $("#quitErr").textContent = t; }));
    $("#checkUpdates")?.addEventListener("click", async () => {
      const b = $("#checkUpdates"), msg = $("#updateMsg");
      b.disabled = true; msg.className = "note"; msg.textContent = "Checking…";
      try {
        const u = await api.check_updates(true);
        [msg.textContent, msg.className] = updateWords(u);
        $("#getRow").hidden = u.state !== "newer";
        b.classList.toggle("primary", u.state !== "newer"); // a newer one: Get is the button to press
        if (u.state === "newer") $("#getUpdate").textContent = `Get Tycho ${u.latest}`;
        notice(u);
      } catch (e) {
        [msg.textContent, msg.className] = ["Couldn't check.", "err"];  // never stuck on "Checking…" (#376)
      } finally {
        b.disabled = false;
      }
    });
    $("#getUpdate")?.addEventListener("click", async () => {
      try {
        const r = await api.open_release();
        $("#getErr").textContent = r.ok ? "" : r.problem;
      } catch (e) {
        $("#getErr").textContent = "Couldn't open the release page.";
      }
    });
  }
  // Quit, from Settings' Updates tab or the foot of the places (#486): asks the Host, as
  // the tray's Quit does. A problem is said beside the button, or as a toast.
  async function quit(b, say) {
    b.disabled = true;
    try {
      const r = await api.quit();
      if (!r.ok) { say(r.problem); b.disabled = false; }
    } catch (e) {
      say("Couldn't quit. Use Quit on Tycho's icon by the clock.");
      b.disabled = false;
    }
  }
  $("#quitSide").addEventListener("click", () => quit($("#quitSide"), toast));
  let lastUpdate = { state: "" };
  /* The last check's answer: the notice opens the Updates tab with its button (#376). */
  function notice(u) {
    lastUpdate = u;
    const b = $("#updateNotice");
    b.hidden = u.state !== "newer";
    if (u.state === "newer") b.textContent = `Tycho ${u.latest} is out`;
  }
  $("#updateNotice").addEventListener("click", () => { view = "settings"; tab = "Updates"; render(); });

  /* ---------- keys ---------- */
  async function keysView() {
    const keys = await api.keys();
    return `<p class="note">Keys are stored in Windows Credential Manager, never in a file or a log, and never by voice. This window never shows one again.</p>
      <form class="card frame" id="keysForm" autocomplete="off">${Object.entries(keys).map(([id, k]) => `<div class="field"><label for="k-${id}">${esc(k.label)} key${id === "elevenlabs" ? " (optional)" : id === "openrouter" ? " (only if OpenRouter runs the brain)" : ""}</label>
        ${k.stored ? `<div class="keyrow"><span class="input num" style="flex:1 1 260px">•••• •••• ${esc(k.ends)}</span>${k.unusable ? `<span class="chip bad">Unusable</span>` : `<span class="chip good">Stored</span>`}<button class="btn frame" type="button" data-remove="${id}">Remove</button></div>`
          : `<div class="keyrow"><input class="input" id="k-${id}" type="password" spellcheck="false" placeholder="${{ anthropic: "sk-ant-…", openrouter: "sk-or-v1-…" }[id] || "sk_…"}"><button class="btn primary frame" type="button" data-store="${id}">Check and store</button></div>`}
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
    if (!rows.length && d.keys_off && !d.problem) {
      // A game Tycho presses nothing in has no commands to list: say what to do instead.
      $("#noKeyBadge").textContent = "";
      page.innerHTML = `<h1>What to say</h1>
        <p class="lede">There are no commands to list for ${esc(d.game_name)}.</p>
        <div class="card frame" id="noCommands"><h3>Just ask</h3>
          <p>${esc(d.keys_off)}</p>
          <p>Hold your push-to-talk button and ask in your own words. Every question goes to the brain, so there are no set phrases to learn.</p>
          ${d.warnings.map((w) => `<p class="warn-line">${esc(w)}</p>`).join("")}</div>`;
      return;
    }
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
    page.innerHTML = `<h1>What to say</h1>
      <p class="lede">What you can say, what it does in the game, and your key for it. Built-in phrases are instant; anything else goes to Claude. Your phrases are saved to <span class="num">${esc(d.file)}</span> and apply when Tycho restarts.</p>
      ${d.problem ? `<p class="err">${esc(d.problem)}</p>` : ""}${d.warnings.map((w) => `<p class="warn-line">${esc(w)}</p>`).join("")}
      ${d.keys_off ? `<p class="warn-line">Key actions are off. ${esc(d.keys_off)} These commands press nothing.</p>` : d.game ? "" : `<p class="warn-line">${esc(d.game_name)} wasn't found, so keys can't be shown. Set the game folder in Settings, Advanced.</p>`}
      <div class="toolbar"><input class="input" id="cmdSearch" type="search" placeholder="Search by what you say or what it does" value="${esc(cmdQuery)}" aria-label="Search commands">
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
    // Left the question without answering: setup is as usual from here (#442).
    if (asking.length && (view !== "setup" || STEPS[step] !== "Privacy")) asking = [];
    // Left Try it: Tycho presses keys again.
    if (trying !== null && (view !== "setup" || STEPS[step] !== "Try it")) { trying = null; await api.try_it(false); }
    if (capturing) api.cancel_capture();
    document.querySelectorAll("nav [data-view]").forEach((b) => b.setAttribute("aria-current", b.dataset.view === view ? "page" : "false"));
    // The first run has the window to itself; run again, the places stay, so Home is a
    // click away even while something is still missing (#453 review).
    document.body.classList.toggle("solo", firstRun && view === "setup");
    document.body.classList.toggle("insetup", view === "setup");
    try {
      // One fetch of the settings a view: the game box and the view's own (#454).
      const shared = await api.settings();
      gameBox(shared); // in first-run setup too: the game's colour follows the pick
      if (view === "status") await status();
      else if (view === "tile") await tileView(shared);
      else if (view === "setup") await setupView();
      else if (view === "pack") await packView();
      else if (view === "commands") await commandsView();
      else await settingsView(shared);
    } catch (e) {
      page.innerHTML = `<h1>Something went wrong</h1><p class="lede">${esc(e && e.message ? e.message : e)}</p>`;
    }
  }
  document.querySelectorAll("nav [data-view]").forEach((b) => b.addEventListener("click", () => { view = b.dataset.view; render(); }));
  // The chosen game under the places, and the way to another one (Settings, where the
  // other game's own setup shows first, #364).
  function gameBox(shared) {
    const rows = shared.groups.flatMap((g) => g.rows);
    const game = rows.find((r) => r.name === "game");
    const pressing = rows.find((r) => r.name === "key_actions")?.value !== false;
    const p = game && game.packs && game.packs.find((x) => x.id === game.value);
    const box = $("#gameBox");
    // The game's own colour (ADR 17): the rest of the look follows --acc.
    if (p && p.accent) document.documentElement.style.setProperty("--acc", p.accent);
    box.hidden = !p || document.body.classList.contains("solo");
    if (box.hidden) return;
    box.innerHTML = `<span class="label">Game</span><b>${esc(p.name)}</b><span>${p.mode === "advisor" || !pressing ? "Tycho only talks" : "Tycho presses your keys"}</span>
      ${game.packs.length > 1 ? `<button class="link" type="button" id="switchGame">Switch game</button>` : ""}`;
    $("#switchGame")?.addEventListener("click", () => { view = "settings"; tab = "Game and controls"; render(); });
  }

  async function start(bridge) {
    api = bridge;
    const about = await api.about();
    $("#ver").textContent = about.version;
    firstRun = Boolean(about.first_run);
    canQuit = Boolean(about.can_quit);
    $("#quitSide").hidden = !canQuit;
    if (firstRun) view = "setup";
    else if (about.opening === "try_it") { view = "setup"; step = STEPS.indexOf("Try it"); }
    else if (about.ask_privacy && about.ask_privacy.length) { asking = about.ask_privacy; view = "setup"; step = STEPS.indexOf("Privacy"); }
    await render();
    if (!firstRun) notice(await api.check_updates(false));
  }
  setInterval(() => { if (api) refreshRecent().catch(() => {}); }, 3000);
  // Try it lasts while this window is in front (review of #464): switched to the game,
  // Tycho presses keys again, and coming back here tries again.
  async function leaveTry() { if (trying !== null) { trying = null; await api.try_it(false); } }
  window.addEventListener("blur", () => { leaveTry().catch(() => {}); });
  document.addEventListener("visibilitychange", () => { if (document.hidden) leaveTry().catch(() => {}); });
  window.addEventListener("focus", () => { if (api && view === "setup" && STEPS[step] === "Try it" && trying === null) render(); });
  // Try it shows what was heard soon after the button is let go.
  async function refreshTried() {
    if (trying === null || !$("#tried") || document.hidden) return;
    const live = await api.recent(false);
    const box = $("#tried");
    if (box && trying !== null) box.innerHTML = recentList(tried(live), TRIED_EMPTY);
  }
  setInterval(() => { if (api) refreshTried().catch(() => {}); }, 1000);
  if (window.TYCHO_FAKE) start(window.TYCHO_FAKE);
  else if (window.pywebview && window.pywebview.api) start(window.pywebview.api);
  else window.addEventListener("pywebviewready", () => start(window.pywebview.api));
})();
