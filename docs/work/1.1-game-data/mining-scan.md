---
status: draft
parent: docs/work/1.1-game-data/README.md
---

# Plan: name the ore behind a mining scan signature (#691)

## The idea

In scan mode the ship's scanner shows a number for each rock, such as 10,620. That number is the ore's base signature times the number of rocks: 10,620 = 3 × 3,540, and 3,540 is Beryl. Players look this up in spreadsheets mid-flight. Tycho can do the sum.

Example: the player says "what's ten thousand six twenty?" and Tycho says "Beryl, three rocks."

## What we know

- Base signatures for patch 4.9 (Beryl 3,540, Quantainium 3,170 and 24 more, plus salvage 2,000, hand-mining 3,000, ROC 4,000) are listed on #691. **Stated** by two independent community tables that agree; **unknown** for the current patch.
- They aren't in the game's data files, so they're kept by hand (**stated**, audit 26 Nov).
- Some numbers fit two ores: 19,200 is Savrilium × 6 and Aslarite × 5. Tycho must say both.
- Screen-reading tools that use a fixed box drift with field of view and on ultrawide screens (**stated**, community reports). Tycho already has local text recognition from route plotting (`eyes/ocr.py`).

## The plan, step by step

**Step 1: the table (pack data).** A new file in the Star Citizen pack, `signatures.toml`: one entry per ore with its base number, the patch it was checked on, and its source. Data only (ADR 18); the pack loader checks it (whole numbers, no duplicates, every entry has a patch).

**Step 2: the sums (code).** A built-in look-up, `scan_signature`, in `games/star_citizen/`. Given a number, it tries every ore and every rock count from 1 to 20 and returns every exact match. The brain never does the arithmetic: it passes the number and reads back the answer. Results:
- one match: "Beryl, three rocks"
- several: "Either Savrilium, six rocks, or Aslarite, five"
- none: "I don't know that signature." Never the nearest guess, because one wrong digit would name the wrong ore.

**Step 3: the player says the number.** Works today with no screen at all. The brain gets the tool and a short line in the pack's briefing: a scan number asked about goes to `scan_signature`. Spoken numbers ("ten thousand six twenty", "ten six twenty") are turned into digits before the look-up.

**Step 4: Tycho reads the number (only when asked).** "What's that signature?" or "what am I scanning?" counts as asking to look (#31): one capture of the game window (ADR 3), local text recognition looks for a number shaped like `10,620` near the middle of the screen, and the look-up runs. If no number is found, the picture goes to the brain as any look does, and it calls the look-up with the number it reads. Only on request, never a constant overlay, and nothing is pressed.

**Step 5: check on Ben's PC, current patch.** Five real scans, two screen resolutions. Correct any base number that's changed and record the patch.

**Step 6:** CHANGELOG line, the setup page's look-ups line, and the players' guide.

## Tests and eval

- Unit tests for the sums: one ore, a tie, no match, an off-by-one-digit number (no guess), a salvage panel, numbers over 20 rocks.
- Pack test: the table loads and every entry has a patch.
- Brain eval cases: each of the above said aloud; zero wrong presses still holds.
- Text recognition replayed on saved frames from the step 5 PC check.

## Decisions for Ben

1. **Where the numbers come from.** *Recommendation:* our own hand-checked table in the pack, each entry naming its patch, with the community tables credited as the source we checked against. The numbers are facts about the game, not anyone's code or text.
2. **Spoken number first, screen reading second.** *Recommendation:* yes. Step 3 is useful on its own, needs no capture and no new look phrase, and can ship in 1.1 even if screen reading slips.
3. **The look phrases.** Plain "what's that?" is too broad to mean "look at my scanner". *Recommendation:* add only scan-specific phrases ("what's that signature", "what am I scanning", "what's that rock") to the pack's `look` list.

## Size

Steps 1 to 3: one PR, cloud only. Step 4: one PR, cloud, then step 5 on the PC.
