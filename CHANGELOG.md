# Changelog

What changed in each version of Tycho, for players. The newest version is first.

Each version's full notes, with examples, are on its [GitHub release](https://github.com/benshapyro/tycho-ai/releases). The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/): while Tycho is 0.x, the second number goes up for new features and the third for fixes only.

**Adding to it:** a PR that changes something a player can notice adds a line under **Unreleased**, in plain words, with its PR or issue. Fixes to work that hasn't been released yet, and work behind the scenes, don't need a line. The release moves **Unreleased** under the new version's number and date (see the `release` skill).

## [Unreleased]

### Added
- With one card at a time, the card that gives way now stays as a thin strip for a minute, and so does the one before it. Say "go back a card" or "last card" to bring one back, and "next card" to go forward again. "Hide the tile" and "keep the tile there" also work now. ([#491](https://github.com/benshapyro/tycho-ai/issues/491))
- Two new Tile cards: a loadout (what a ship is fitted with, by slot and size, for questions like "what's the best loadout for my M80?") and a comparison (two ships side by side, figure against figure). Lists of numbers now show a bar for each, so the biggest and smallest stand out at a glance. ([#491](https://github.com/benshapyro/tycho-ai/issues/491))

### Changed
- Tycho gets the brain ready while you hold push-to-talk, so its answer starts a little sooner, and the first question after a break of an hour or more is no longer about 3 seconds slower. ([#21](https://github.com/benshapyro/tycho-ai/issues/21))
- When Tycho can only reach an action through a toggle (say, "open the doors" with only Open/Close Doors bound), it now says "Toggled Open/Close Doors. I can't see which way it went." instead of a line that claims the doors are open. If it pressed something else as well, the rest of its line stays and this follows it. Give the action its own key and the line goes away. Actions the game announces itself, like the landing gear, stay quiet as before. ([#29](https://github.com/benshapyro/tycho-ai/issues/29), [#537](https://github.com/benshapyro/tycho-ai/issues/537))
- A slow web search gives up sooner (18 s instead of 25 s), so Tycho always has time to answer from it rather than running out of time just after it finishes. Searches took 15 s at most when measured. ([#365](https://github.com/benshapyro/tycho-ai/issues/365))

## [0.9.0] - 2026-11-22

### Added
- New commands for remote turrets, such as the Idris's: "enter remote turret" (or "enter remote turret two" or "three") and "exit remote turret". The game has no key for entering one by default, so bind Enter Remote Turret 1, 2 and 3 in the game's keybinds first. ([#356](https://github.com/benshapyro/tycho-ai/issues/356))
- Ask Tycho which key does something ("how do I fire the guns?", "what's my strafe key?") and it names your own key, from your keybinds. ([#357](https://github.com/benshapyro/tycho-ai/issues/357))

### Fixed
- A card the brain left empty no longer ends in the not-done sound: Tycho says its line without the card. ([#443](https://github.com/benshapyro/tycho-ai/issues/443))
- "Open hangar" heard as "open hangers", and "close doors" heard as "closed doors", now work instantly instead of waiting for the brain. ([#358](https://github.com/benshapyro/tycho-ai/issues/358))
- Asked about "the address" when you said the Idris, Tycho now answers about the Idris instead of asking which ship you mean. A real address is still left alone: the brain is only told what the word may be. ([#509](https://github.com/benshapyro/tycho-ai/issues/509))
- Tycho no longer hears an everyday word such as "hunger" as a game word that only sounds like it ("hangar"). ([#505](https://github.com/benshapyro/tycho-ai/issues/505))
- Setup's Try it is safer. What you say there is tried, not pressed, even if you switch to the game while Tycho is still working it out. If the window misses your switch to the game and Try it stays on, what you say there plays the not-done sound and nothing is pressed, rather than a done sound for keys that never reached the game. After setting up a new game you picked in Settings, the window opens on Try it, as after a first run. Actions with no name in the game's files now read as words ("Toggle landing system"), not as raw ids, and a follow-up question no longer takes something you only tried as done. ([#467](https://github.com/benshapyro/tycho-ai/issues/467), [#465](https://github.com/benshapyro/tycho-ai/issues/465))
- Tycho no longer says it "stopped unexpectedly last time" when you ended it from Task Manager after the window hit a harmless Windows error that it recovered from. ([#474](https://github.com/benshapyro/tycho-ai/issues/474))
- A notice that comes while Tycho says "Let me check." during a look-up is now said before the answer, and the filler line isn't cut off. ([#440](https://github.com/benshapyro/tycho-ai/issues/440))
- Names and quotes in Greek or Cyrillic on the Tile now draw in Windows' own fonts. Since 0.8's bundled fonts they drew as boxes. ([#458](https://github.com/benshapyro/tycho-ai/issues/458))
- With speech recognition on the processor (the default), Tycho now uses at most as many threads as your PC has cores, so a 4-core laptop keeps more for the game. If the new speech model couldn't download after an update, Tycho starts on the one you already have instead of not starting. `tycho gpu install` now says how to move speech recognition to the graphics card, and `tycho check` says when the old 2.5 GB model is no longer used and can be deleted. ([#480](https://github.com/benshapyro/tycho-ai/issues/480))

### Changed
- Answers with more than a few numbers, such as a trade route or a loadout, now go on the Tile more often, with one sentence spoken. Asking to see "something interesting" on the Tile gets a card, not a question back. ([#490](https://github.com/benshapyro/tycho-ai/issues/490))
- A stray word that isn't a request, or one misheard, gets "Copy." instead of a question back, and "is that you?" gets a few words. A vague "the usual" gets a question, never a guess. ([#359](https://github.com/benshapyro/tycho-ai/issues/359), [#495](https://github.com/benshapyro/tycho-ai/issues/495))
- With key actions off, Tycho now knows every key you've bound, steering, strafing and emotes included, and says when one is held ("Boost (Hold)"). Asked for your strafe key, it names it instead of saying there's none. ([#437](https://github.com/benshapyro/tycho-ai/issues/437))
- In setup, skipping the Keys step in Star Citizen now shows it as skipped, not as still to do: a key is optional there, since the built-in phrases work without one. With key actions off it still shows as to do, and says why: the built-in phrases press nothing then. At the window's smallest size, Next sits fully in view, and in World of Warcraft: Forever, What to say now shows any problem with your own commands file. ([#448](https://github.com/benshapyro/tycho-ai/issues/448))
- **Quit** is now at the bottom of the left side of Tycho's window, so it's easy to find. It's still under Settings, Updates, and on Tycho's icon by the clock. ([#486](https://github.com/benshapyro/tycho-ai/issues/486))
- With the brain through OpenRouter, Tycho now tells OpenRouter never to use a host that keeps or trains on what you say, whatever your OpenRouter account's privacy settings are. ([#488](https://github.com/benshapyro/tycho-ai/issues/488))
- `tycho check`'s focus test no longer says "(another program was)" when it can't tell which program was in front; it names the program only when it knows. ([#430](https://github.com/benshapyro/tycho-ai/issues/430))

## [0.8.0] - 2026-11-22

### Added
- The Tile now shows cards over World of Warcraft: Forever too, in gold, for lists, steps and routes that are easier to read than hear. Setup tells you to set Display Mode to Windowed (Fullscreen), which the Tile needs. ([#381](https://github.com/benshapyro/tycho-ai/issues/381))
- Setup's last step, Try it, is now a real test. Hold your button and speak: the window shows what Tycho heard and the keys it would press, and nothing reaches the game while that step is open and in front, so you can try it at the desktop. Switch to the game and Tycho presses keys again. After a first run's setup, the window opens again on that step once Tycho is listening. ([#464](https://github.com/benshapyro/tycho-ai/pull/464))
- Choose which screen the Tile shows on, on the Tile page in Tycho's window: follow the game (as before), or a screen of your own, such as the one beside the game. If that screen isn't plugged in, the Tile follows the game and says so once. ([#447](https://github.com/benshapyro/tycho-ai/issues/447))
- Home in Tycho's window now says whether Tycho is listening (or paused) and which button to hold, and lists the last 20 things you said with what Tycho did, newest first, as they happen. They're kept in memory only: never saved, and gone when Tycho quits.
- Crash reports, only if you say yes: set `crash_reports` in Tycho's window (Settings, Privacy) or in `settings.toml`. When Tycho crashes, it keeps a short report and sends it the next time it starts: where in Tycho's code it broke, Tycho's version, the Windows version, the game and the brain provider. It never sends what you or Tycho said, your keys, your name, your Windows user name, your files or folders, or anything that identifies your PC. Until you answer, nothing is sent. If you set Tycho up before this, its window asks you the next time it opens, and at each start until you answer. By voice, crash reports only switch off, and switching them off deletes any report still waiting. ([#199](https://github.com/benshapyro/tycho-ai/issues/199))
- Change Tycho's voice by asking: "switch your voice to Sarah". By voice you can pick George, Sarah, Lily or Brian; any other voice is still chosen in Tycho's window (Settings, Voice and sounds) or `settings.toml`. The new voice is used from the next start, and Tycho says so. ([#355](https://github.com/benshapyro/tycho-ai/issues/355))
- Setup now starts with your game. Pick Star Citizen or World of Warcraft: Forever first, and every step after it fits that game: what Tycho does there, whether it presses keys and why, what it can look up, and two or three things to say first. A PC with only World of Warcraft no longer lands on "Star Citizen wasn't found". The steps are the game, your keys (with what each costs), push-to-talk, the microphone, Tycho's voice, what leaves your PC (with the crash reports question: no and yes, and neither is picked for you), and a first thing to try. You can skip any step: the last one lists what Tycho still needs before it can start, and what you chose is kept if you close the window. ([#364](https://github.com/benshapyro/tycho-ai/issues/364))
- Picking another game under Settings, Game and controls, now shows what Tycho does in that game before you switch, with a Back button. Before, one click restarted Tycho with it. ([#364](https://github.com/benshapyro/tycho-ai/issues/364))
- If Tycho ever vanishes without a word (a crash deep inside Windows, the speech model or the sound system, which no error message can catch), it now leaves a record of what it was doing in its logs folder. The next time it starts, it tells you on the Tile that it stopped unexpectedly and where that file is, and `tycho check` and the window's status say so for a few days. Nothing is sent anywhere: the file stays on your PC, and holds no keys and nothing you said. ([#296](https://github.com/benshapyro/tycho-ai/issues/296))
- If the Tile or Tycho's icon by the clock stops working while you play, Tycho now tells you once, and keeps listening. A lost icon is a card on the Tile (or spoken, if the Tile is off); a lost Tile is spoken, and from then on Tycho says its answers instead of putting them on a Tile you can't see. Before, both went silently. ([#297](https://github.com/benshapyro/tycho-ai/issues/297))
- **Quit Tycho** is now a button in Tycho's window too (Settings, Updates). Before, the only Quit was on the icon by the clock, which Windows can hide and which can stop working. ([#424](https://github.com/benshapyro/tycho-ai/issues/424))

### Changed
- With the brain through OpenRouter, Tycho now keeps its instructions cached with Anthropic for an hour between requests, as it does when you use Claude directly. Before, the cache lasted 5 minutes, so the first thing you asked after a quiet spell took about 3 seconds longer.
- **Speech recognition now runs on the processor by default, so the game keeps the graphics card.** With Tycho's speech model on the same card as the game (3 GB of its memory), one player's PC had graphics crashes in two games and a full freeze. Tycho now uses a smaller 8-bit copy of the same model on the processor: as accurate, and about 0.4 seconds a phrase instead of about 0.25 with the game running. **The first start after upgrading downloads it, 0.7 GB, once**; new players download 0.7 GB instead of 2.5. If you want the card's speed and have the memory to spare, set Speech recognition to the graphics card (Settings, Advanced, or `transcription_device = "gpu"` in `settings.toml`). ([#188](https://github.com/benshapyro/tycho-ai/issues/188))
- Home in Tycho's window says how Tycho is in a much bigger word, so you can read it from across the room: amber when something needs fixing, grey when paused. Settings rows light up on their left edge under the pointer, and section labels are smaller. ([#471](https://github.com/benshapyro/tycho-ai/pull/471))
- The Tile page in Tycho's window now has a switch for each kind of card your game uses, instead of a box to type card names into. World of Warcraft: Forever no longer lists Star Citizen's ship and fleet cards. ([#422](https://github.com/benshapyro/tycho-ai/issues/422))
- The Tile's preview in Tycho's window shows a card in your game's own words: World of Warcraft: Forever no longer shows a Star Citizen trade card. The Tile's colour choice that follows the game is now called `game` (it was `ship`; an older `settings.toml` still works). ([#422](https://github.com/benshapyro/tycho-ai/issues/422))
- Tycho's window and the Tile now use their own fonts, so they look the same on every PC.
- In World of Warcraft: Forever, Tycho's window and the Tile are gold instead of blue, so you can tell at a glance which game Tycho is set for. A ship's own colour on the Tile, or a colour you picked, still comes first.
- Settings are in plainer groups: Voice, Game and controls, Safety, Privacy, Keys and Updates. What most players never change (the brain's provider and model, the voice model, the game's channel and folder, and where speech is recognised) is under Advanced, at the end.
- Setup is five steps now: Game, Keys, Talk and listen (your push-to-talk button, microphone and Tycho's voice on one page), Privacy, and Try it. Running setup again and picking another game shows what Tycho does there first, instead of switching at once and closing the window. A message from setup no longer covers its Next button. ([#435](https://github.com/benshapyro/tycho-ai/issues/435))
- Tycho's window has its places down the left now: Home, What to say, The Tile and Settings, with your game and a Switch game button under them. Home says in one line whether Tycho is ready and shows the one thing to fix first; all the checks are a click away. Setup has the whole window to itself. ([#200](https://github.com/benshapyro/tycho-ai/issues/200))
- In a game where Tycho only advises, the Commands page now says to just ask, instead of showing an empty table, and Settings no longer offers a game folder that game doesn't have. ([#446](https://github.com/benshapyro/tycho-ai/pull/446))
- When a newer version is out, **Get the update** is now the highlighted button under Settings, Updates. The Undo message after a change stays for nine seconds, up from four and a half. ([#446](https://github.com/benshapyro/tycho-ai/pull/446))
- Tycho's wording was gone through line by line, so one game's words don't turn up in another. `tycho check` now asks you to switch to the game you chose (it said Star Citizen whatever the game), and skips that test where Tycho presses no keys. The descriptions of the web search and Tile settings no longer talk about Star Citizen or a ship, and the ones that are Star Citizen's say so. In World of Warcraft: Forever, and in Star Citizen with key actions off, simple questions get shorter answers. ([#363](https://github.com/benshapyro/tycho-ai/issues/363))
- With key actions off in Star Citizen, Tycho now names your own key when you say a built-in command ("night vision" gets "it's on Right Alt + N" if that's your key), instead of "your night vision key, if you've bound one". If the action has no key, it says so. ([#386](https://github.com/benshapyro/tycho-ai/issues/386))
- With key actions off in Star Citizen, Tycho now names your own key however you ask, not only for the exact built-in phrases: "hit the brakes", "flashlight" and "can you get the lights" get your key instead of "check your keybinds". To do it, your key bindings (what each key does, nothing else) are now part of what Tycho sends to its brain when key actions are off. ([#392](https://github.com/benshapyro/tycho-ai/issues/392))

### Fixed
- Speech recognition on the graphics card no longer takes Tycho down when the card itself fails mid-game (a driver reset, for example). It moves to the processor and keeps listening, as it was meant to: say the command again. ([#473](https://github.com/benshapyro/tycho-ai/issues/473))
- Setup's table of what leaves your PC now also lists the check of a key when you store it, and ElevenLabs' check at start that your key can use your voice. Choosing a game folder without the game now names the game, not a Star Citizen file. ([#435](https://github.com/benshapyro/tycho-ai/issues/435))
- Setup's Back and Next buttons now stay at the bottom of the window. Before, you had to scroll down to find Next on every step. ([#446](https://github.com/benshapyro/tycho-ai/pull/446))
- Setup no longer ticks a step you skipped. A skipped step keeps its number, in amber, so you can see what's still to do. ([#446](https://github.com/benshapyro/tycho-ai/pull/446))
- When setup opens again only to ask about crash reports, the question is now at the top, not under the table of what leaves your PC. ([#446](https://github.com/benshapyro/tycho-ai/pull/446))
- Shutting down or signing out of Windows while Tycho runs, or closing its console window, no longer makes the next start say Tycho stopped unexpectedly. ([#428](https://github.com/benshapyro/tycho-ai/issues/428))

## [0.7.0] - 2026-11-13

### Added
- Run Tycho's brain through OpenRouter instead of Anthropic, with one OpenRouter key: set `brain_provider = "openrouter"` in Tycho's window (Settings, Brain) or in `settings.toml`, and store the key with `tycho key set openrouter` or on the Keys page. It's the same Claude Haiku as before; only models that passed Tycho's test with no wrong key presses can be picked, and confirm-grade actions still ask first. Web search still needs an Anthropic key: without one, Tycho tells you it can't search instead of failing quietly. `tycho check` names the provider and asks it (free) whether your key works. Claude direct stays the default, so nothing changes unless you pick it. ([#112](https://github.com/benshapyro/tycho-ai/issues/112))
- Tycho's window shows which game it helps with (Settings, Game and controls), and `tycho check` names it. Your commands and fleet files stay where they are. ([#293](https://github.com/benshapyro/tycho-ai/issues/293))
- Turn key actions off to use Tycho as an advisor only: set `key_actions` off in Tycho's window (Settings, Safety) or in `settings.toml`. Tycho then answers, looks things up and shows cards, and presses nothing: no command, sequence, Claude pick or "confirm" gets a key pressed. Say a built-in command ("night vision") and Tycho tells you how to do it yourself instead. By voice it can only be turned off, never on. A game whose rules forbid tools that press keys will always be advisor only. `tycho check` and the window's status say whether key actions are on, and why not. When the game isn't the window in front, Tycho now names the game that isn't focused. ([#191](https://github.com/benshapyro/tycho-ai/issues/191))
- Tycho's window now tells you when a newer version is out: a notice at the top right when the window opens, and a **Check for updates** button in Settings, Updates, that opens the release page with the installer. Before, the check at start only wrote to the log file, which the Start-menu Tycho never shows, so 0.4.0 and 0.5.0 never said a newer Tycho was out. Tycho still never updates itself, and `check_for_updates = false` still stops the automatic check (the button asks only when you press it). ([#9](https://github.com/benshapyro/tycho-ai/issues/9))
- Tycho's window lists each game under Settings, Game and controls, with whether Tycho presses keys there and, if not, why. Picking another game restarts Tycho with it at once, and its window opens again. The Commands page shows the chosen game's commands, and says when key actions are off. ([#295](https://github.com/benshapyro/tycho-ai/issues/295))
- World of Warcraft: Forever, as an advisor: pick it under Settings, Game and controls, and Tycho answers questions about the game (classes, the Skyborne, dungeons, professions, the Legacy System), explains how things work, and shows cards, by voice. It's for Forever, Blizzard's level-60 take on the original Azeroth, not modern WoW or Classic. It presses no keys there, since Blizzard's rules don't allow tools that play the game for you, and it says so if you ask it to do something. It calls you nothing there unless you choose a name, and it doesn't borrow Star Citizen's words. It looks nothing up yet: answers come from what Claude knows, plus a few dated facts about the game. ([#300](https://github.com/benshapyro/tycho-ai/issues/300), [#349](https://github.com/benshapyro/tycho-ai/issues/349))
- When the Star Citizen sites have no clear answer, web search now looks further: any website (guides, forums, Reddit) and a page it finds, so it can answer "where is" and "how do I" questions from community posts. Answers from players say so. It trusts RSI's patch notes and the community wiki over older guides, and when it does no better, fails or runs out of time, you get the Star Citizen sites' answer instead. That wider search usually takes about 11 seconds (at most 25) and costs about 3 cents. It's on by default: set `web_search_open = false` (Settings, Privacy, or `settings.toml`) to keep to the Star Citizen sites. ([#354](https://github.com/benshapyro/tycho-ai/issues/354), [#373](https://github.com/benshapyro/tycho-ai/issues/373))
- `brain_seconds` sets how long Tycho waits for the brain before saying it couldn't answer (8 seconds, as before). ([#112](https://github.com/benshapyro/tycho-ai/issues/112))

### Fixed
- Long spoken answers no longer stop mid-sentence: Tycho now ends them at the last whole sentence that fits, and keeps "From UEX." on the end. ([#350](https://github.com/benshapyro/tycho-ai/issues/350))
- "Never mind" (or "cancel") when there's nothing to cancel is now taken as heard, with the done sound, instead of the not-understood one. It also ends the talk so far, so what you say next isn't read as an answer to Tycho's last question. ([#351](https://github.com/benshapyro/tycho-ai/issues/351))
- Asked where something is or how to do something in Star Citizen, Tycho now searches the web on the first ask instead of guessing from memory, and no longer says it can't look something up when it can. Asked again, it builds on its last answer and says which holds if they differ. ([#353](https://github.com/benshapyro/tycho-ai/issues/353))
- Web search answers that run long no longer end on the point in a version number ("patch 4.") or on a comma, and a sentence that starts with a number or a quote is no longer lost. ([#370](https://github.com/benshapyro/tycho-ai/issues/370))
- Rebinding several keys in the game no longer reloads Tycho's keybinds once per key: it waits until you've finished, then says so once. The new keys still work at once. ([#352](https://github.com/benshapyro/tycho-ai/issues/352))
- Upgrading while Tycho was running ended in a string of file errors, and the old version stayed. The installer now tells you Tycho is running and waits for you to quit it (right-click its star by the clock, Quit Tycho) before it changes anything. Uninstalling does the same. ([#333](https://github.com/benshapyro/tycho-ai/issues/333))

## [0.6.0] - 2026-11-12

### Changed
- Tycho guesses less. A request it can't place ("faster", "same as before") or one that could mean something drastic ("kill it") now gets a one-line question instead of a press. What you say to your squad on the same push-to-talk ("break right", "form up on me") presses nothing, not even shields or targeting, and gets a plain "Copy.", while "look behind" still turns your view. "Take us to Crusader" opens the star map (if it has a key) and tells you to set the destination there, since Tycho can't choose where quantum goes, and "open fire" says the trigger is yours instead of powering weapons. ([#271](https://github.com/benshapyro/tycho-ai/issues/271))

### Fixed
- Web search answers no longer stop mid-word, and fewer searches come back with nothing: the search had too little room to answer in. When an answer does run long, Tycho keeps only the whole sentences, and reads cited text without stray spaces before commas. ([#366](https://github.com/benshapyro/tycho-ai/issues/366))
- Chat that isn't a request ("good fight everyone, o7") sometimes got an error instead of Tycho's reply. Now you hear the reply, without Tycho explaining why it pressed nothing ("not a request for me, so…"). ([#278](https://github.com/benshapyro/tycho-ai/pull/278))
- Asking for a long how-to or plan ("teach me how to salvage") could end in the error sound, because the card ran past the length Claude may answer in. Tycho now has twice the room, asks Claude to keep cards to what the Tile shows, and if an answer still runs too long it says so ("Ask me for part of it") instead of showing half a card. ([#266](https://github.com/benshapyro/tycho-ai/issues/266))

## [0.5.0] - 2026-11-09

### Added
- Missed what Tycho said? "Say that again" (or "repeat that", "come again") repeats the last thing it said word for word, an answer or what it just did ("Lights."), at once and as often as you like, with the Tile on or off. Asked while Tycho waits for you to confirm, it asks the question again and gives you the full time to answer. It only repeats the words: it never presses a key again. Before, it went back to Claude, took a second or two, might come back worded differently, and stopped working after half a minute. ([#225](https://github.com/benshapyro/tycho-ai/pull/225), [#222](https://github.com/benshapyro/tycho-ai/issues/222), [#226](https://github.com/benshapyro/tycho-ai/issues/226))

- Choose Tycho's voice with the new `voice` setting, in Tycho's window (Settings, Voice and sounds) or in `settings.toml`: George, Sarah, Lily, Brian, or any voice ID from your ElevenLabs account, such as one you designed. Each voice uses its own model (George is on Flash v2.5, the quickest; the others on v4 Turbo), and `voice_model` can pick the other. It applies when Tycho next starts. At start, Tycho asks ElevenLabs whether your key can use the voice (free, no characters used). If it can't, Tycho speaks as George and says so on the Tile, in its window and in the log, instead of paying for a failed first line. `tycho check` names the voice and model and warns if yours isn't available. ([#242](https://github.com/benshapyro/tycho-ai/issues/242))

### Changed
- **The default voice is now George on Flash v2.5,** which answers about 0.1 s sooner. Before, the default was The Ship's Mind, a designed voice only its maker's account can use, so every other key got George on v4 Turbo after a failed first request. If you made The Ship's Mind or another voice of your own, set `voice` to it. ([#242](https://github.com/benshapyro/tycho-ai/issues/242), [ADR 9](docs/adr/0009-voice.md))
- **Check after upgrading if you edit `commands.toml` by hand:** a `requires` or `accepted_risks` value Tycho doesn't know, such as a typo, now stops the whole file loading. Until you fix it, Tycho uses only its built-in commands, and the Commands page says so. `tycho check` names the value and the ones allowed: correct or remove it, then run `tycho check` again. Before, the typo was ignored and that command was always refused. ([#206](https://github.com/benshapyro/tycho-ai/pull/206), [#218](https://github.com/benshapyro/tycho-ai/issues/218))
- A trade route asked from where you are ("I'm at Obituary, best route for refined gold?") is one small request to UEX instead of two, and a slow shop is left out instead of holding up the answer. A list UEX sends too slowly for one answer is kept for the next time you ask, instead of failing every time. ([#211](https://github.com/benshapyro/tycho-ai/pull/211), [#231](https://github.com/benshapyro/tycho-ai/issues/231))
- When a price, route, mining or item source hangs, Tycho gives up on it sooner (after 3 to 5 seconds, down from 4 to 6) and answers without it, so a slow site no longer uses up the time Claude needs to reply. Every look-up answered in under a second when timed with the game running. ([#230](https://github.com/benshapyro/tycho-ai/pull/230))
- Asked about the wrong kind of thing by name, Tycho checks the Star Citizen Wiki's search: "Cutlass Black" is the ship, not the plushie, and "the Gallant" as a ship finds the Gallant rifle. Weapons named after a ship, like the "Mantis gatling", stay weapons, and the kind of item you name counts: "Arrow pistol" isn't taken for the Arrowhead sniper rifle, nor "Gladius cannon" for a model of the Gladius. ([#220](https://github.com/benshapyro/tycho-ai/pull/220), [#233](https://github.com/benshapyro/tycho-ai/issues/233))
- A phrase of yours that is one of Tycho's own words ("repeat that", "confirm", "clear the Tile") never reached your command, because Tycho answers those first. The Commands page now refuses it, and `tycho check` warns about one already in your `commands.toml` and says what Tycho uses it for. ([#227](https://github.com/benshapyro/tycho-ai/issues/227))
- Tycho checks the drive has room before it downloads the speech model, and says how much it needs, how much is free, and where. ([#203](https://github.com/benshapyro/tycho-ai/pull/203))

### Fixed
- When the graphics card runs short mid-request, Tycho plays the "not done" sound at once instead of acting on what you said about 20 seconds later, and loads the processor copy in the background. It also frees the card's memory first. ([#209](https://github.com/benshapyro/tycho-ai/pull/209))
- If the switch to the processor fails, Tycho tries again 30 seconds later, then waits longer between tries (up to 5 minutes), instead of staying without speech recognition until a restart. ([#215](https://github.com/benshapyro/tycho-ai/pull/215))
- Since 0.4, an answer that needed a look-up could run out of time while it was still coming in. Each look-up gives it more time again. ([#212](https://github.com/benshapyro/tycho-ai/pull/212))
- An Anthropic key stored with a hidden character, such as a zero-width space pasted from a web page, no longer reaches Claude and fails there. Tycho treats it as no key, says so in the log and in `tycho check`, and asks you to store it again, as it already does for an ElevenLabs key. `tycho key set` and Tycho's window refuse such a key. ([#248](https://github.com/benshapyro/tycho-ai/issues/248))
- A key stored with a hidden character now shows as **Unusable** on the Keys page of Tycho's window, with a note to remove it and paste it again, instead of "Stored". `tycho check` says the key is stored but unusable, rather than "no key", and `tycho key check` says the same. This is true for both the Anthropic and ElevenLabs keys. ([#250](https://github.com/benshapyro/tycho-ai/issues/250))
- Sounds no longer hold Tycho up, the first request after a keybind change is as quick as any other, and quitting shuts down cleanly. ([#207](https://github.com/benshapyro/tycho-ai/pull/207))

### Security
- Tycho won't press a key combination when part of it is, on its own, an action you'd have to confirm (Lights on right Alt + X, with Eject on right Alt alone), and the refusal names the key that clashes. ([#206](https://github.com/benshapyro/tycho-ai/pull/206), [#217](https://github.com/benshapyro/tycho-ai/pull/217))

## [0.4.0] - 2026-11-07

### Added
- Tycho's own window, opened from the Start menu or the star by the clock: **Status**, **Settings** (with **Undo**) and **Commands**. Right-click the star to pause listening, show or hide the Tile, open the logs, or quit.
- A guided first run: find the game, choose the push-to-talk button, check the microphone, add your keys.
- The Tile: a small see-through panel over the game for details better read than heard, like prices and routes. Its colour follows your ship's maker, and you can move, resize or hide it by voice. It needs Borderless window mode.
- New look-ups: where to mine a resource, where to refine an ore, and where to buy a ship with aUEC.
- "Checking." while Tycho looks something up, kept on your PC after the first time so it doesn't use voice credits.
- Settings by voice ("speak a bit quieter", "undo that"), except anything that would make Tycho less careful.
- Tycho calls you what you like: "Commander", "Captain", your name, or nothing.
- Your own phrases for any command, from the Commands page.

### Changed
- Trade answers work without saying where you are, and understand "refined gold", "gold ore" and whole systems like Pyro.
- A command and your ship in one go ("power off, I'm in my Pitbull") does both.
- Prices and routes from UEX always say so.

### Fixed
- Item names said as letters ("S F nine") are found.
- Tycho never reads out the instructions meant for the Tile, and picks again if it chose a key you don't have.

## [0.3.3] - 2026-11-04

### Changed
- The speech model takes about 3.0 GB of graphics memory instead of 3.5 GB, at the same speed. Tycho uses the card when 4.0 GB is free (it needed 4.5 GB).
- Answers from Claude arrive about a quarter of a second sooner.
- Long sentences are matched against the built-in commands in under a millisecond (up to 40 ms before).
- After pressing keys, Tycho says what it sent ("Max power to engines"), not a result it can't check.
- The log shows when each key went down.

### Fixed
- A command with no key bound gets the action to bind, instead of a press of something nearby.

## [0.3.2] - 2026-11-04

### Fixed
- Starting Tycho while Star Citizen holds most of the graphics card's memory no longer stops with an error: speech recognition switches to the processor, at the start or later in a session.

## [0.3.1] - 2026-11-04

### Added
- Setup by an AI agent (Claude Code, Codex, Cursor and others), which opens a window whenever it needs you and never sees your keys.
- `tycho check --json`, `tycho model download` and `tycho model status`, and `tycho actions <words>` to find the game's action names.

### Security
- `tycho key set` won't read a key piped in by a script or agent, so a key never ends up in a transcript.

## [0.3.0] - 2026-11-04

The first release with an installer, as a preview. Earlier versions weren't released.

### Added
- Built-in commands matched on your PC and pressed instantly, through your own keybinds; Claude for anything said differently; your own phrases and sequences in `commands.toml`.
- Keys pressed only while Star Citizen is in front, "confirm" before anything you can't undo, and push-to-talk stops everything.
- Answers about ships, prices and trade routes (UEX), weapons, armor, components and crafting, and news.
- A fleet Tycho remembers, and speech recognition on your PC.
- A check for a newer version when Tycho starts (it never updates itself).

[Unreleased]: https://github.com/benshapyro/tycho-ai/compare/v0.9.0...HEAD
[0.9.0]: https://github.com/benshapyro/tycho-ai/releases/tag/v0.9.0
[0.8.0]: https://github.com/benshapyro/tycho-ai/releases/tag/v0.8.0
[0.7.0]: https://github.com/benshapyro/tycho-ai/releases/tag/v0.7.0
[0.6.0]: https://github.com/benshapyro/tycho-ai/releases/tag/v0.6.0
[0.5.0]: https://github.com/benshapyro/tycho-ai/releases/tag/v0.5.0
[0.4.0]: https://github.com/benshapyro/tycho-ai/releases/tag/v0.4.0
[0.3.3]: https://github.com/benshapyro/tycho-ai/releases/tag/v0.3.3
[0.3.2]: https://github.com/benshapyro/tycho-ai/releases/tag/v0.3.2
[0.3.1]: https://github.com/benshapyro/tycho-ai/releases/tag/v0.3.1
[0.3.0]: https://github.com/benshapyro/tycho-ai/releases/tag/v0.3.0
