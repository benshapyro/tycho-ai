# Speaking, showing and confirming: the evidence behind Tycho's replies

Researched 2026-11-08. Taken from two reviews the maintainer had written: a review of research on how people take in speech, text and visuals, and a playbook of communication habits from aviation, the military and medicine.
This is a reference doc, not a spec. It explains why Tycho answers the way it does, so a later change can be checked against the evidence and not only against taste.

## How to read this doc

- **Confidence**:
  - **Strong**: meta-analyses that agree, or a finding that has replicated.
  - **Mixed**: meta-analyses that disagree, or a finding that depends on the conditions.
  - **One study**: a single paper, however careful.
  - **Inferred**: a reasonable step from the evidence, not tested.
- Most of this evidence comes from short lab tasks with students, not from people flying a ship. Applying it to Tycho is always a step of inference.
- `[CHECK]` marks a figure that should be checked against the paper before anyone quotes it.

## Summary

- **Speech is quick to say and slow to hear.** Silent reading runs about 238 words a minute (Brysbaert 2019, 190 studies). Speech runs about 150. A listener can't skim, skip or go back. So Tycho takes speech in and keeps its spoken replies short. *Strong.*
- **Listening loses mostly because the listener can't set the pace.** Over 46 studies, reading and listening come out about equal on average (g = 0.07, not significant). Reading wins when the reader sets the pace (g = 0.13) and on questions that need inference (g = 0.36) (Clinton-Lisell 2022). So a spoken answer needs a way to hear it again. *Strong.*
- **Exact values and sets of more than a few items are better read than heard.** Verbal working memory holds about four chunks (Cowan 2001). Tables beat charts for exact look-ups (Vessey 1991). So prices, routes and specs go on the Tile. *Strong.*
- **Don't say what's already on screen.** Reading the same words aloud while they're shown adds little or hurts (the redundancy effect; Clinton-Lisell 2023 found only a trivial gain from reading while listening). Labels inside a visual beat separate captions (Ginns 2006; Schroeder & Cenkci 2018, g = 0.63) `[CHECK: the review gives d = 0.85 over 50 studies for Ginns 2006; as remembered, Ginns reports about 0.72 spatial and 0.78 temporal]`. *Strong.*
- **A voice makes the speaker seem more thoughtful.** People who heard an argument rated the speaker as more thoughtful and more human than people who read the same words (Schroeder, Kardas & Epley 2017, 4 experiments). *One study.* The step to "players will trust a voiced Tycho more than it has earned" is *inferred*.
- **Ask for a read-back in proportion to the stakes.** Read-backs catch errors in air traffic control, but controllers miss many wrong read-backs. A read-back on routine work adds noise and teaches people to skim. *Strong, with limits.*
- **Pressing the keys is not the same as the action working.** Being heard, understood, allowed, pressed and working are separate facts, and each one can fail on its own. *Inferred* for agents, from human-team research.

## What Tycho does, and where it falls short

| Finding | What Tycho does | Where it falls short |
|---|---|---|
| Speech is slow to hear | Spoken replies are a few words; "Speak as Tycho: calm, precise, dry. The player is flying, and hears every word." (`brain/briefing.py`) | — |
| The listener can't set the pace | "Show that again" brings back a card (ADR 16) | Nothing replays a spoken-only answer. Asking Claude costs a call, may come back worded differently, and stops working after half a minute: [#222](https://github.com/benshapyro/tycho-ai/issues/222) |
| Exact values are better read | More than about three items or numbers go on a card; one-line answers stay audio-only (ADR 16) | Needs the Tile on. With it off, three prices are still spoken aloud |
| Don't say what's on screen | Say the one thing that matters, and leave the rest to the card: "Best is Everus Harbor at 3,048 a unit. Two more on the Tile." (`brain/briefing.py`) | — |
| Voice raises trust | "Say only what you're confident is true in the game. If you don't know, say so." Cards never show gauges Tycho can't read (ADR 16) | Spoken answers don't say where a figure came from unless asked. Web answers do ("RSI's roadmap has…") |
| Read-back by stakes | Anything you can't undo waits for a spoken "confirm", and the question is in the gate's own words: "Eject? Say confirm." (ADR 13). Routine commands run at once | — |
| Correcting should be cheap | "No, the other one", "do that again" and settings "undo" (`brain/briefing.py`) | — |
| Pressed is not working | The sounds say whether the keys went in. Claude says what it did ("Gear down, lights on") after the press starts (ADR 14). Every press is logged (ADR 13) | Tycho can't tell whether the game acted. [#29](https://github.com/benshapyro/tycho-ai/issues/29) (know when an action can work) and [#30](https://github.com/benshapyro/tycho-ai/issues/30) (read Game.log) are the way to close that gap |

## What this doesn't settle

- Whether a voiced Tycho makes players trust wrong answers more. The evidence comes from people judging arguments, not an assistant's facts.
- How short is short enough while flying. The working-memory figures come from people sitting still; a dogfight is busier.
- Whether the Tile's 6 to 20 s fade is long enough to read three prices while flying. That's for a play session to show.

## Sources

- Brysbaert, M. (2019). How many words do we read per minute? *Journal of Memory and Language*.
- Clinton-Lisell, V. (2022). Listening ears or reading eyes: a meta-analysis. *Review of Educational Research*.
- Clinton-Lisell, V. (2023). Reading while listening: a meta-analysis.
- Cowan, N. (2001). The magical number 4 in short-term memory. *Behavioral and Brain Sciences*.
- Ginns, P. (2006). Integrating information: spatial and temporal contiguity effects. *Learning and Instruction*.
- Schroeder, J., Kardas, M. & Epley, N. (2017). The humanizing voice. *Psychological Science*.
- Schroeder, N. & Cenkci, A. (2018). Spatial contiguity and spatial split-attention effects: a meta-analysis. *Educational Psychology Review*.
- Vessey, I. (1991). Cognitive fit: graphs versus tables. *Decision Sciences*.
- [FAA read-back procedure](https://www.faa.gov/air_traffic/publications/atpubs/atc_html/chap2_section_4.html).
- [Clark & Brennan, Grounding in communication (1991)](https://web.stanford.edu/~clark/1990s/Clark,%20H.H.%20_%20Brennan,%20S.E.%20_Grounding%20in%20communication_%201991.pdf).
