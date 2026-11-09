# Trademark check: the name "Tycho"

Date: 2026-11-08, official-register update 2026-11-09 · For: [#26](https://github.com/benshapyro/tycho-ai/issues/26), [ADR 7](../adr/0007-name-tycho.md)

**This is not legal advice.** It is a search of free public sources by an AI agent, written to help the maintainer decide. It is not a clearance opinion, and a lawyer could find things it missed.

## Result

No live trademark for TYCHO covering software, games or AI turned up, in the US, EU or UK. The one live US TYCHO mark is the musician Scott Hansen's, for music only. In the EU and UK, TYCHO is registered for lab instruments, e-cigarettes and financial services. The closest real risk is not a registration: it's **Tycho AI, Inc.**, a funded US company that uses "Tycho AI" and owns tycho.ai, which is also this project's repository and package name.

**Overall risk for a free, open-source, unofficial fan tool called Tycho: low.** It rises to moderate if Tycho is ever sold, branded "Tycho AI", or registered as a mark.

## Method and its limits

First pass (2026-11-08): web searches of public mirrors of the USPTO data (Justia Trademarks, Trademarkia) and other public pages; GitHub through its API.

Official registers (2026-11-09, from the maintainer's PC):
- **USPTO TSDR** (status pages for known serials): loaded. Confirmed the two marks marked "TSDR" below.
- **TMview, EU and UK offices**: loaded, 21 results for TYCHO, all registered, none pending.
- **USPTO Trademark Search** (the full search): not reached; the site serves a bot check that only a person in a browser passes. So a US filing nobody has mentioned yet, for example by Tycho AI, Inc., is **not ruled out**.
- **WIPO Global Brand Database** and TMview's US and WIPO offices: not reached, same reason.

Classes in focus: 9 (software, downloadable recordings), 41 (games and entertainment), 42 (software as a service, AI).


## Trademark registers

### United States

| Mark | Owner | Status | Goods and services | Risk to us |
|---|---|---|---|---|
| TYCHO, serial 77899442, reg. 4017612 (US) | Scott Hansen, the ambient musician Tycho | **Live** (TSDR): registered 2011-08-30, renewed 2020-10-19 | Class 9 CDs, records, downloadable music and video recordings; live musical performances and DJ services | Low. Same classes on paper, different goods. Hansen's act is well known, so avoid anything music-flavoured in branding. |
| TYCHO, reg. 5723018 (US) | NanoTemper Technologies GmbH | **Dead** (TSDR): cancelled 2025-10-24 | Lab instruments for studying proteins | None. |
| TYCHO, reg. 1934026 (US) | (education software firm) | Cancelled 2002 | Class 9 software for teachers on pen computers | None. Dead. |
| TYCHO, serial 90175362 (US) | Individual | Abandoned 2021 | Lighting, heating, cooking | None. Dead. |
| TYCHO, serial 97000258 (US) | Same individual | Unknown | Sunscreen | Very low. Unrelated goods. |
| TYCHO THERAPEUTICS (US) | Tycho Therapeutics | Abandoned 2018 | Biotech | None. |

Rows not marked TSDR come from public mirrors and weren't re-checked; all are dead or unrelated.

### EU and UK (TMview, 2026-11-09)

| Mark | Office, number | Owner | Classes | Risk to us |
|---|---|---|---|---|
| TYCHO | EU 017082538; UK00917082538 | NanoTemper Technologies GmbH | 9 | Very low. Same company as the dead US lab-instrument mark; the EU goods wording didn't load, but it's presumably the same lab instruments. |
| Tycho | EU 018361977; UK00003701363 | Hong Kong IVPS International Ltd | 34 (tobacco, e-cigarettes) | None. |
| TYCHO | UK00004152887 | Kepler Partners LLP | 36 (financial services) | None. |
| TYCHO THERAPEUTICS | EU 017377789; UK00917377789 | Tycho Therapeutics, Inc. | 5 (pharmaceuticals) | None. |
| TYCHON | EU 019274260 | Tychon Packaging Inc | 16, 20, 40, 42 | Very low. A different word, for packaging. |

No EU or UK mark uses Tycho as a word in class 41 or 42, and none belongs to Tycho AI, Inc. The other 13 results only contain the letters (SAFETYCHOICE and similar).

Near-miss marks (TYCHE, TYCHON, TYTHO, TYCO) are different enough in sound and spelling that they don't change the picture.

## Products and companies using the name

- **Tycho AI, Inc. (tycho.ai)**, Cambridge, Massachusetts: MIT spin-out founded 2022, $10M Series A in October 2025. Makes GPS-free navigation hardware and software for military drones (product lines Voyager and Halley). No trademark filing for TYCHO found, but it has common-law rights in "Tycho AI" for AI navigation and may well file. Markets are far apart (defense autonomy versus a game voice assistant), so confusion is unlikely, but the identical "tycho-ai" string is the most likely source of a complaint.
- **Steam:** an upcoming action RPG, *NightSong* (also listed as *Tycho's Apocalypse*), whose hero is called Tycho. A character name, not a product mark. Low.
- **App stores:** "Tycho", a quiz-bowl study app from OutplayedApps (formerly "Science Bowl"). Different field. Low.
- **GitHub:** about 1,000 repositories named Tycho. The best known is Eclipse Tycho, a Java build tool. None is a voice assistant or game tool.
- **Well-known characters** (from general knowledge, not searched): Tycho Brahe in the Penny Arcade webcomic, and Tycho Station in *The Expanse*. Both are space- or gaming-adjacent, but fictional names, not product marks in our field.
- **Tycho Brahe** himself is a historical figure, so the name is common and weak as a mark, which cuts both ways: others can't easily stop us, and we couldn't easily stop others.

## Domains (checked 2026-11-09)

| Domain | State |
|---|---|
| tycho.ai | Taken (2019, behind a privacy service; the site is Tycho AI, Inc.'s) |
| tycho.app | Taken (2018, registrant not shown) |
| tychoai.com | Taken (2023), parked at a domain-sale service, so possibly for sale |
| tycho.gg | Appears free (the .gg registry returned "not found") |

The project doesn't need its own domain for launch; the GitHub repository is the home page. If one is wanted, tycho.gg fits a game tool.

## Recommendation

1. **Keep the name Tycho.** Nothing found blocks it for a free, open-source fan tool.
2. **Present it as "Tycho", not "Tycho AI".** Use a tagline such as "voice co-pilot for Star Citizen". The repository and package names (`tycho-ai`) can stay; they are technical identifiers, but don't make "Tycho AI" the product name or logo.
3. **Before the public launch, run the one remaining search** (about 5 minutes, free; below).
4. **A lawyer's clearance is not worth it yet.** A US clearance search and opinion typically costs a few hundred to a couple of thousand dollars. It becomes worth it if Tycho is sold, takes money (donations aside), or if you want to register TYCHO yourself.

## What's left

One search needs a person in a browser, because the site blocks automated look-ups:

- **USPTO Trademark Search** (tmsearch.uspto.gov): search `TYCHO`, live and dead. Look for anything in classes 9, 41 or 42 beyond Hansen's music mark, and for any owner named Tycho AI. If nothing new shows up, #26 is done.

Optional, lower value: WIPO Global Brand Database (branddb.wipo.int) for international marks, `TYCHO` in classes 9, 41, 42.

If either finds a live TYCHO mark for software, games or AI, stop and reconsider before launch.

## Sources

- [TYCHO 77899442 owner page, Justia](https://trademarks.justia.com/owners/scott-robert-hansen-1699563); [kestrel-heavy PR #137](https://github.com/becktj90/kestrel-heavy/pull/137) (secondary report of reg. 4017612's status)
- [TYCHO 5723018, NanoTemper, Justia](https://trademarks.justia.com/875/58/tycho-87558228.html)
- [TYCHO 74482349 (reg. 1934026), Justia](https://trademarks.justia.com/744/82/tycho-74482349.html)
- [TYCHO 90175362, Trademarkia](https://www.trademarkia.com/tycho-90175362)
- [TYCHO THERAPEUTICS 87422142, Justia](https://trademarks.justia.com/874/22/tycho-87422142.html)
- [Tycho AI Series A, FinSMEs](https://www.finsmes.com/2025/10/tycho-ai-raises-10m-in-series-a-funding.html); [Tycho AI terms of use](https://tycho.ai/terms-of-use)
- [NightSong on Steam](https://store.steampowered.com/app/3007220)
- [OutplayedApps Tycho](https://github.com/OutplayedApps/Tycho)
- [Tycho (musician), Wikipedia](https://en.wikipedia.org/wiki/Tycho_(musician))
- USPTO TSDR status pages for serials 77899442 and 87558228; TMview (tmdn.org) search for TYCHO, EU and UK offices; RDAP and whois look-ups, all 2026-11-09
- GitHub repository search for `tycho in:name`, 2026-11-08
