# 3. Staying within CIG's rules

Date: 2026-10-29 · Status: accepted

## Context

RSI's Terms of Service (§IV) prohibit using or distributing "macro" software. In practice CIG tolerates voice keybind tools: HCS VoicePacks are officially licensed, and no bans for VoiceAttack, HCS or Wingman AI have been found. But no CIG staff statement explicitly approves tools like Tycho. The Fankit FAQ treats donations as commercial use and requires an "unofficial" disclaimer.

## Decision

- Tycho only sends keypresses through the player's own keybinds. It never reads or writes game memory, injects into the process, or hooks DirectX.
- Every action and sequence is started by the user and has a fixed end. No loops, no timers acting alone, no combat automation (aim assist, capacitor cycling).
- Screen capture uses the Windows Graphics Capture API for the game window only.
- No donations or sponsorships tied to Star Citizen IP. The README carries CIG's disclaimer. The name contains no CIG marks.
- Ask CIG support for a written position before the first public release.

## Consequences

Some "power user" automation requests will be declined by design.

## Amendment, 23 November 2026: typing a place name into the star map

*Status: accepted. The maintainer approved this text on 23 November 2026 (#591), after agreeing to the direction and asking for route plotting in 1.0 ([#588](https://github.com/benshapyro/tycho-ai/issues/588)).*

**Context.** Players want to say "plot a route to Port Tressler" and have it done. Other voice tools already do this; HCS VoicePacks, which CIG licenses, sell it. Until now Tycho only pressed the player's own keybinds. Most of plotting a route is keybinds too: the star map has its own keys (`mapui_action_planroute`, R by default), and Game.log writes "Quantum route plotted to X" with the place's name. The one new thing is typing the place's name into the star map's search box. Typing letters anywhere else would press flight keys, so it needs rules of its own.

**Decision.** Tycho may type a place's name into the game's own search box, under these rules:

- **Only in a flow a built-in pack declares.** A pack names the flow (open the map, the box, the keys after it) as data. Only a pack that ships inside Tycho may declare one (ADR 18), and Star Citizen's first is plotting a route. Never into chat, a contract, a terminal or any other box.
- **Only names from the game's own list.** The brain picks a place; the code turns it into the exact name from that patch's game data. Unknown or unclear names are asked about, never typed. Letters, digits, spaces and simple punctuation only; never Enter in the middle of a name, never a command.
- **Only after a local check says the box is ready** (ADR 14's amendment of the same date). If the check can't see the open map with the box ready, nothing is typed, and Tycho says it couldn't open the map.
- **Started by the player, with a fixed end.** One request types one name once. It may check a few times while the map opens (at most 3 seconds), then it stops. No retries on its own.
- **Proved by the game's log.** Success is Game.log's "Quantum route plotted to X" with the chosen place. Anything else is reported as not done, and the map is left open for the player.
- **The mouse, only if the keyboard can't.** If the PC test shows the search box can't be reached by keys, Tycho may make one left click on the box, located in the same frame the check read, while the check still holds. Never a click at a fixed screen position, never anywhere else, never a drag or a cursor move to steer.
- Still never game memory, injection or DirectX hooks, and still no combat automation.

**Consequences.**
- The safety gate gains a "route" flow: not confirm-grade (a route doesn't move the ship), refused with key actions off, the game window not focused, or the check failing. Every key in it still goes through `safety.check`.
- Tests: nothing typed when the check fails, a name not in the list is never typed, typing stops at its fixed end, and success needs the log line naming the place. Brain eval cases: plot a route, an unknown place, a place spelt two ways.
- Before it ships, a PC test at two resolutions and two UI scales.
- Star map changes in a patch can break the flow. When the check or the log line fails, Tycho says so and presses nothing more; it never guesses.

## Amendment, 23 November 2026 (evening): clicking the search box, after the PC test

*Status: accepted. The maintainer approved this text on 24 November 2026 (#630), after asking for it following the PC test ("I am fine with Tycho clicking the search box", [#588](https://github.com/benshapyro/tycho-ai/issues/588)).*

**Context.** The PC test (#588) found that the star map's search box can't be reached from the keyboard: Tab and typing do nothing until the box is clicked. R (`mapui_action_planroute`) plots a route once a place is picked, and C clears it. Game.log doesn't write "Quantum route plotted to X" with a shown name. It writes `Successfully calculated route to rs_ext_pyro3_leo`, using the game's internal id. Letting the player pick the place and Tycho press R saves nothing, because R is one key. So the click is the main way, not a fallback. This replaces the morning amendment's "the mouse, only if the keyboard can't" point and its "proved by the game's log" point. The rest of that amendment stands.

**Decision.**

- **Up to two left clicks per request, each on something the check just found.** The first is on the search box. The second is only needed if Enter doesn't pick the place, and it goes on the first search result, only if its text matches the chosen place's name. Each click is aimed at a spot the local check (ADR 14) found in a frame taken just before that click, and that same frame must show the map still open. Never a fixed screen position, never a drag, never a right click, and never moving the cursor to steer.
- **Only from a ship's seat, and in NAV mode first.** Before the first click Tycho sets NAV (`v_master_mode_set_nav`), which takes the ship's weapons offline (ADR 12's mode guard). That way a click that misses the map can't fire a gun. On foot, plotting is refused, because a drawn weapon can't be ruled out. Tycho says "Sit down in your ship first."
- **Proved by the game's log, by its id.** Success means Game.log's `Successfully calculated route to <id>` line, where `<id>` is the chosen place's id in that patch's game data. Anything else is reported as not done, and the map is left open.
- **Accepted risk.** If the map closes during the gap between the last frame and the click (well under a second), the click lands in the cockpit. With NAV set, that can't fire. It could engage the quantum drive only if the player had quantum mode on with a route already set. Tycho never turns quantum mode on in this flow.

**Consequences.**
- The pack's route flow names its steps as data: open the map, find the box, click, type, Enter, then click the result if needed, then R, then check the log, then close the map (F).
- Tests: no click without a fresh frame showing the map; never on foot; NAV is set before any click; at most two clicks; success only with the matching id.
- The per-patch game data needs the starmap ids (`rs_...`) and their shown names.

## Amendment, 24 November 2026: the second click closes the search panel

*Status: accepted. The maintainer approved this text on 24 November 2026 (#631), after the PC retest ([#588](https://github.com/benshapyro/tycho-ai/issues/588)).*

**Context.** In the retest, Enter picked the top result. But the search box kept the keys, so R typed "r" into the box, and Esc closed the whole map. Clicking the search panel's close button (its "x") gave the keys back to the map, and then R plotted: `Successfully calculated route to levski_all-001`. So the second click the evening amendment allowed, on the first result, isn't the one needed. The close button is.

**Decision.** The second of the evening amendment's two clicks goes on the search panel's close button instead of the first result. All its other terms stand. In addition:

- **Only after the frame shows the right place picked.** The frame taken just before the click must show the map open and the result for the chosen place.
- **Aimed from the search box, not from the "x" alone.** A lone "x" is a weak match for text recognition. The button's spot is worked out from where the check found the search box in that same frame, using the panel's layout, which the pack states as data. Text recognition only has to agree that something is there. If they don't agree, nothing is clicked, and Tycho says it couldn't finish.
- **No first-result click.** Enter does that job.
- **Not an empty spot on the map.** Clicking empty map also gives the keys back (maintainer, 24 November 2026). But a spot that looks empty can hold a marker, and clicking it would change what's picked. The close button is always in the same place relative to the box.

**Consequences.**
- The pack's route flow becomes: NAV, open the map, find the box, click it, type, Enter, check the result, click close, then C (`mapui_action_clearroute`) if a route is already set (the game won't plot over one: maintainer, 24 November 2026), R, check the log, close the map (F). C is the player's own keybind, like R. Saying "plot a route to X" counts as asking to replace the current route, and Tycho says "Replacing your route to Y."
- The route line names the game's internal id (`levski_all-001`, `rs_ext_pyro3_leo`). The per-patch game data has to map each id to its shown name, so that "Route set to Levski" is said only when the id matches.
- The PC test still needs a second resolution and UI scale, to show that the close button's offset from the box holds.

## Amendment, 25 November 2026: place names from UEX, checked by the map

*Status: accepted. The maintainer chose UEX's list on 24 November 2026 ("UEX list, checked by the map", [#588](https://github.com/benshapyro/tycho-ai/issues/588)) and approved this text on 25 November 2026 (#643).*

**Context.** The morning amendment says names come from the game's own list, and the evening one that the route line's id is checked against that patch's game data. Tycho can't read either yet. UEX's terminal list has the names of the cities, stations, outposts, moons and planets the map plots to.

**Decision.**

- **Names come from UEX's list until Tycho reads the game's own.** Only cities, stations, outposts, points of interest, moons and planets: never a shop's terminal, an orbit or a whole system. A name that matches nothing, or more than four places, is never typed: Tycho says "I don't know a place called X."
- **The map's own result decides.** After Enter, the frame must show the typed name among the search results, under the box, before the close button is clicked. A name UEX has and the map doesn't ends there, with nothing more pressed.
- **The route line's id is checked by its words.** Until there's a list of ids, the plotted route counts as done only when one of the place's own words (at least 3 letters, not "station" or "port") is a word of the id (`levski_all-001` names Levski). Anything else is reported as not checked, never as done.
- **The player's words start it, not the brain.** Only a request that starts with one of the pack's route phrases ("plot a route to Levski") plots. The brain has no tool for it: for "take us to Crusader" it says which words to use.
- **The map closes with the key that opened it** (`v_starmap`), not F.

**Consequences.**
- When Tycho can read the game's own place and id lists, they replace UEX's and the word check.
- The first route request fetches UEX's terminal list (two requests), as a trade question does.

## Amendment, 25 November 2026: misheard requests, and the brain may hand over a place

*Status: accepted. The maintainer approved it on 25 November 2026 ("Yes, change ADR 3", [#647](https://github.com/benshapyro/tycho-ai/issues/647)).*

**Context.** In the first test in Nyx, speech-to-text heard "Plotta Route to Lewski" and "Pona route to Dlevsky". Neither started with a route phrase, so both went to the brain, which had no tool: it said "Plotting a route to Levski." and nothing happened. The player had to repeat himself, and Tycho claimed something it hadn't done.

**Decision.** This replaces "The player's words start it, not the brain" above.

- **Misheard phrases still count.** The words before the phrase's last word ("to") may be heard a little wrong ("plotta route", "pona route"), as long as the first word doesn't make it a question ("best route to sell gold", "fastest route to Pyro"). Words that ask for more than a route ("to Hurston and spool up") go to the brain whole.
- **A misheard place is the one it sounds like**, when one place sounds clearly closer than any other ("Lewski", "Dlevsky" are Levski). The map's own result still has to show it before anything more is clicked.
- **The brain may hand over a place, and nothing more.** For a route asked in other words ("take us to Crusader"), the brain's `plot_route` tool only names the place. Once the brain is done, Tycho runs the same flow as for the phrase, with the same gate, the map's own search and the log check. Tycho's own line says how it went; the brain's words are dropped, so it can't claim a route. A place is taken only when the brain looked at nothing and looked nothing up in that request (no look, web search, search of past talk or other look-up): otherwise it's dropped, as nothing presses after those (ADR 14, ADR 15).
- **The brain never names the player's system from a guess.** Only the request's situation says where the player is; a home base in their notes doesn't.

**Consequences.**
- Brain eval cases: "take us to Crusader", "Pona route to Dlevsky" and "a course over to Lorville" use `plot_route` and never say a route is set; "which system am I in" with a home base note never says Stanton.

## Amendment, 26 November 2026: when Enter opens the place's own panel

*Status: accepted. The maintainer approved it on 26 November 2026 ("Yes, click the name", [#661](https://github.com/benshapyro/tycho-ai/issues/661)).*

**Context.** In the 26 November test at 5120x2160, Enter didn't show a results list. It opened the place's own panel straight away: its name ("LEVSKI") on a line of its own under the box, its kind, a description and a "Route" button, with no close button. Tycho stopped there, because the frame didn't show the search results. The maintainer said it only needed a click outside the search bar, then R.

**Decision.** This adds to the close-button amendment above; its other terms stand.

- **The place's own panel counts as the map's own result.** It counts only when the place's whole name is a line of its own just under the box, and no results or recent list is there. The name only inside a description doesn't count.
- **The second click goes on that name.** It's text the check just read in that frame, it keeps the chosen place picked, and it takes the keys away from the search box. When the panel isn't there, the second click stays on the close button.
- Still at most two clicks, both on the map, NAV first, and a fresh frame before each.

**Consequences.**
- If the PC test shows the click on the name doesn't give the keys back, the next try is an empty spot of the panel, with a new amendment.

## Amendment, 26 November 2026 (later): when Enter empties the box

*Status: proposed. Waiting for the maintainer's OK ([#661](https://github.com/benshapyro/tycho-ai/issues/661)).*

**Context.** In the next test ("Take me to Orison", 5120x2160), Enter picked Orison and emptied the box: its "SEARCH LOCATIONS..." words came back, Orison was first under RECENT, and the whole unfiltered list was under ALL RESULTS. There was no place panel and no "x" that text recognition could read, so Tycho stopped. Text recognition also read the name as "ORISOW".

**Decision.** This adds to the morning amendment above.

- **The place under RECENT counts as the map's own result**, but only once Enter has emptied the box (its empty words are back on the box's line), and only in the RECENT list, above ALL RESULTS.
- **The second click goes on that name**, as on the place's own panel: it keeps the place picked and takes the keys from the box.
- **One misread letter is allowed** when the map's name is checked, on the place's panel or under RECENT: only in names of five letters or more, and never a digit ("ARC-L2" is not ARC-L1). The route line's id in the log still has to name the place before Tycho says the route is set.

**Consequences.**
- At most two clicks, both on the map, with NAV set first and a fresh frame before each, as before.
