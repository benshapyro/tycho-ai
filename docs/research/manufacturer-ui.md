# Star Citizen manufacturer visual identity: reference for the Tycho Tile

Researched 2026-11-04. Covers the live game up to roughly Alpha 4.10.
This is a reference doc, not a spec. No images were downloaded or embedded.

## How to read this doc

- **Confidence** in the table:
  - **High**: a published source says it directly.
  - **Medium**: several community sources agree, or one says it and it matches the lore.
  - **Low**: my estimate from memory of the game. Nothing I could fetch confirms it.
- **Every hex value is an estimate.** No source gives hex values for the in-game HUD or MFD colours. CIG has not published any.
- Where a fan tool picked a hex value, I name the tool. Those are design choices, not values taken from the game.
- `[UNCERTAIN]` means "check in game before relying on it". `[OUT OF DATE?]` means the source is old.

## Summary

- CIG rebuilt the cockpit HUD and MFDs in **Alpha 3.24.2 (18 Oct 2024)**. The rebuild added **"manufacturer styles"**. ([3.24.2 notes](https://starcitizen.tools/Update:Star_Citizen_Alpha_3.24.2))
- That patch shipped **5 MFD styles: 4 for specific manufacturers and 1 shared default** for every other ship. ([Simulation Daily, 13 Sep 2024](https://simulationdaily.com/news/star-citizen-zeus-mfds/))
- No source I could reach names the 4 manufacturers. A forum post from the PTU confirms a Crusader style existed. ([Overclockers, 26 Sep 2024](https://forums.overclockers.co.uk/threads/official-star-citizen-squadron-42-thread.18603648/page-981)) My best guess for the other three is Aegis, Anvil and Drake. `[UNCERTAIN]`
- **Players cannot change HUD or MFD colours in game.** I found no setting for it in any patch up to 4.x. Players have asked for one for years, mainly for colour-blind accessibility.
- The base SC UI language is **blue/cyan holograms, white text, thin straight lines, flat panels and geometric shapes**. ([mobiGlas design notes, 2015](https://robertsspaceindustries.com/en/comm-link/engineering/14466-Design-Notes-MobiGlas)) `[OUT OF DATE?]`
- CIG's Terms of Service say fan work must **say clearly that it is unofficial**. It must **not imply any relationship with RSI**. It must **not use marks that are "confusingly similar"**. Commercial use is not allowed. ([RSI ToS, effective 30 Oct 2026](https://robertsspaceindustries.com/en/tos))
- Two fan tools already do what Tycho wants: [SCMFD](https://github.com/Dima-McArrow/SCMFD) and the [FlowKits Xeneon Edge panel](https://getflowkits.com/blog/star-citizen-corsair-xeneon-edge/). Both theme a panel by the manufacturer of the current ship. SCMFD also lists the Game.log entity prefix for each manufacturer, such as `DRAK_` and `AEGS_`. This is directly useful for Tycho.

## Main table

"In-game HUD/MFD colour" means what the cockpit MFDs look like since 3.24.2. Hex values are my estimates unless the row says otherwise.

| Manufacturer | In-game HUD/MFD colour(s) + hex (estimate) | Confidence | Example ships | Source |
|---|---|---|---|---|
| Aegis Dynamics | Cool white/pale blue on dark, red accents. ~`#CFE6FF`, accent ~`#E0413A` | Low | Gladius, Avenger Titan, Sabre, Vanguard | [3.24.2 notes](https://starcitizen.tools/Update:Star_Citizen_Alpha_3.24.2); [Aegis wiki](https://starcitizen.tools/Aegis_Dynamics) |
| Anvil Aerospace | Military look. Teal-green or olive on dark grey. ~`#7FD1B9` `[UNCERTAIN]` | Low | Arrow, F7 Hornet Mk II, Carrack, C8 Pisces | [3.24.2 notes](https://starcitizen.tools/Update:Star_Citizen_Alpha_3.24.2) |
| Argo Astronautics | Probably the shared default style (cyan). Brand colour is yellow/orange. ~`#FF9A1F` brand | Low | MPUV, RAFT, MOLE, SRV | [Simulation Daily](https://simulationdaily.com/news/star-citizen-zeus-mfds/) (default style) |
| Aopoa (Xi'an) | Alien style. Pale green-yellow glyphs on dark. ~`#B6ED68` | Low | Nox, Khartu-al, San'tok.yāi | [LAMP page](https://starcitizen.tools/LAMP) lists San'tok.yāi |
| Banu | Probably the default style. Brand feel is warm gold/brass. ~`#E8C27A` | Low | Defender, Merchantman (not yet flyable) | [LAMP page](https://starcitizen.tools/LAMP) lists Defender |
| Consolidated Outland | Probably the default style. ~`#4FB3FF` default | Low | Mustang Alpha, Nomad, Pioneer | [LAMP page](https://starcitizen.tools/LAMP) |
| Crusader Industries | Has its own MFD style since 3.24.2. Clean light blue/white. ~`#8CC8FF` | Medium (style exists); Low (colour) | Mercury Star Runner, C2 Hercules, Ares, Intrepid, Spirit | [Overclockers post](https://forums.overclockers.co.uk/threads/official-star-citizen-squadron-42-thread.18603648/page-981) |
| Drake Interplanetary | Orange/amber on near-black, industrial. ~`#F5901E` | Medium | Cutlass Black, Corsair, Vulture, Caterpillar | [FlowKits](https://getflowkits.com/blog/star-citizen-corsair-xeneon-edge/) says "orange and industrial" |
| Esperia | Probably the default style. Vanduul replicas may differ. ~`#4FB3FF` default | Low | Prowler, Talon, Glaive and Blade replicas | [LAMP page](https://starcitizen.tools/LAMP) |
| Gatac | Alien (Xi'an) style. Teal/cyan. ~`#6DE6F2` | Low | Syulen, Railen | [Monthly Report Mar 2026](https://starcitizen.tools/Comm-Link:Star_Citizen_Monthly_Report_-_March_2026) ("Gatac-style spaces") |
| Greycat Industrial | Probably the default style. Brand is utility yellow. ~`#F4D35E` brand | Low | ROC, PTV, STV | none found |
| Kruger Intergalactic | Probably the default style. ~`#4FB3FF` default | Low | P-52 Merlin, P-72 Archimedes, L-21 Wolf | [LAMP page](https://starcitizen.tools/LAMP) |
| MISC | Teal/green on dark is my guess. ~`#58DDD1` | Low | Freelancer, Prospector, Reliant, Starfarer, Hull C | [FlowKits](https://getflowkits.com/blog/star-citizen-corsair-xeneon-edge/) says MISC has "its own" colour |
| Mirai | Sporty. Possibly the default style. ~`#FF647C` brand guess | Low | Fury, Razor, Pulse, Guardian | [LAMP page](https://starcitizen.tools/LAMP) |
| Origin Jumpworks | Pale/white luxury look. Light panels, gold-ish accent. ~`#F2F4F7`, accent ~`#B08D57` | Medium (pale); Low (hex) | 300i/325a, 400i, 600i, 890 Jump, X1 | [FlowKits](https://getflowkits.com/blog/star-citizen-corsair-xeneon-edge/) says "pale, clean" |
| Roberts Space Industries | Blue. Close to the shared SC default. ~`#4FB3FF` | Medium | Aurora, Constellation, Zeus Mk II, Polaris | [FlowKits](https://getflowkits.com/blog/star-citizen-corsair-xeneon-edge/) says RSI is blue; [mobiGlas notes](https://robertsspaceindustries.com/en/comm-link/engineering/14466-Design-Notes-MobiGlas) |
| Tumbril | Ground vehicles. Probably the default style. ~`#DDB582` brand guess | Low | Cyclone, Nova, Storm | none found |

The FlowKits post is from 20 Aug 2026. It describes its own panel's colours, which it bases on the ship's manufacturer. It does not claim they match the game exactly.

### Seen in the maintainer's screenshots (2026-11-04)

Screenshots Ben shared show three HUD colours, in different cockpits. They aren't stored in the repo, because the images are CIG's.

- **Orange/amber on near-black,** in a cockpit with yellow-and-black hazard striping. This fits Drake's orange.
- **Pale cyan,** with "ORIGIN" shown in the HUD. This fits Origin being pale and clean, though here it reads cyan rather than white.
- **Bright green,** in a combat cockpit, with the manufacturer not identified. This supports at least one manufacturer style being green.

The next step is to take one screenshot per ship in game and read the HUD colour off it. That turns the estimates above into confirmed values. Keep those screenshots locally; record only the hex values here.

## Per-manufacturer notes

Lore lines come from the Star Citizen Wiki manufacturer pages. I could fetch the Drake, Aegis and MISC pages this session. The wiki then timed out, so the other lore lines are from general knowledge, with a link to the right page. Treat them as Medium.

SCMFD hex values come from its source files, [`ManufacturerTheme.swift`](https://github.com/Dima-McArrow/SCMFD/blob/HEAD/SCMFD/Theme/ManufacturerTheme.swift) and [`AdditionalManufacturerThemes.swift`](https://github.com/Dima-McArrow/SCMFD/blob/HEAD/SCMFD/Theme/AdditionalManufacturerThemes.swift). The file calls them "manufacturer-inspired skins". They are a fan's design choices, not values taken from the game.

### Aegis Dynamics (`AEGS_`)
- **Lore:** A military shipbuilder from the Messer era, based on Cestulus in the Davien system. It later moved into civilian ships. ([wiki](https://starcitizen.tools/Aegis_Dynamics))
- **Shapes:** Angular, aggressive and blade-like. Dark grey hulls. A cold, military-government feel.
- **Interior:** Dark, utilitarian and dense. Flat-faced panels.
- **HUD:** Has its own MFD style since 3.24.2, I believe. I recall cool white/blue with red highlights. `[UNCERTAIN]`
- **SCMFD's choice:** red accent `#FF3B3B` and the Rajdhani font.
- **Ships:** Gladius, Avenger Titan, Sabre, Vanguard, Hammerhead, Reclaimer. The Hammerhead MFD layout was updated in 4.8, according to an unreliable source. ([starcitizenhelp](https://starcitizenhelp.com/updates)) `[UNCERTAIN]`

### Anvil Aerospace (`ANVL_`)
- **Lore:** The main supplier of fighters to the UEE Navy. Its civilian ships are military designs too. ([wiki](https://starcitizen.tools/Anvil_Aerospace))
- **Shapes:** Rugged and boxy. Military greys and greens. Exposed structure.
- **Interior:** Functional, like a military cockpit. Lots of hard switches.
- **HUD:** Probably one of the four 3.24.2 styles. I recall a teal or green tint. `[UNCERTAIN]`
- **SCMFD's choice:** olive `#B5CC4A` and the Black Ops One stencil font.
- **Ships:** Arrow, F7 Hornet Mk II, Carrack, C8 Pisces, Valkyrie, Paladin.

### Argo Astronautics (`ARGO_`)
- **Lore:** Makes industrial utility craft: tugs, haulers and mining ships. ([wiki](https://starcitizen.tools/Argo_Astronautics))
- **Shapes:** Blocky and modular. Yellow/orange safety livery.
- **HUD:** Probably uses the default style. ([Simulation Daily](https://simulationdaily.com/news/star-citizen-zeus-mfds/))
- **SCMFD's choice:** orange `#FF861F`.
- **Ships:** MPUV, RAFT, MOLE, SRV, MOTH. The MOTH gained LAMP in 4.7. ([LAMP](https://starcitizen.tools/LAMP))

### Aopoa (`XIAN_`)
- **Lore:** A Xi'an manufacturer. It sells alien designs, adapted for humans, into the UEE. ([wiki](https://starcitizen.tools/Aopoa))
- **Shapes:** Organic curves and smooth shells. Alien script.
- **HUD:** The Xi'an interface uses alien glyphs. I don't know whether 4.x gives it its own style. `[UNCERTAIN]`
- **SCMFD's choice:** lime `#B6ED68`.
- **Ships:** Nox, Khartu-al, San'tok.yāi.

### Banu (`BANU_`)
- **Lore:** Banu traders who build to order. Each ship is a mix of parts and cultures. ([wiki](https://starcitizen.tools/Banu))
- **Shapes:** Biological and ornate. Warm metals.
- **SCMFD's choice:** gold `#F1CC80`.
- **Ships:** Defender. The Merchantman is not yet flyable. `[UNCERTAIN]`

### Consolidated Outland (`CNOU_`)
- **Lore:** A young, scrappy start-up run by Silas Koerner. It makes cheap starter ships. ([wiki](https://starcitizen.tools/Consolidated_Outland))
- **Shapes:** Sleek but budget-built. Exposed engines.
- **SCMFD's choice:** lavender `#D8BEFF`. This looks invented; I know no lore basis for it.
- **Ships:** Mustang Alpha, Nomad, Pioneer.

### Crusader Industries (`CRUS_`)
- **Lore:** A large-ship and transport maker on Orison in Stanton. It has a clean, corporate-civilian identity. ([wiki](https://starcitizen.tools/Crusader_Industries))
- **Shapes:** Smooth and rounded. White and blue liveries.
- **Interior:** Bright, airy and corporate.
- **HUD:** Has its own MFD style. A player called it "much better than the old... cleaner, sharper" during the 3.24.2 PTU. ([Overclockers, 26 Sep 2024](https://forums.overclockers.co.uk/threads/official-star-citizen-squadron-42-thread.18603648/page-981)) The colour is my estimate.
- **SCMFD's choice:** light blue `#79BFFF`.
- **Ships:** Mercury Star Runner, C2 Hercules, Ares Inferno/Ion, Intrepid, Spirit.

### Drake Interplanetary (`DRAK_`)
- **Lore:** "Robust and geometrical design that uses many low-tech materials". Sold to militias. Pirates love it. Founded 2845 to build the Cutlass. ([wiki](https://starcitizen.tools/Drake_Interplanetary))
- **Shapes:** Slab sides and hard angles. Exposed pipes, hazard stripes and stencil type.
- **Interior:** Cramped, industrial and bare metal.
- **HUD:** Orange/amber, according to FlowKits ("orange and industrial"). This matches my memory of the in-game Drake MFDs. ([FlowKits](https://getflowkits.com/blog/star-citizen-corsair-xeneon-edge/))
- **SCMFD's choice:** yellow `#F2C230`, Saira Stencil One / Saira Condensed fonts, and a "hazard tape" key accent. SCMFD and FlowKits disagree here. For "orange in a Drake", I suggest Tycho uses an orange near `#F5901E`. `[UNCERTAIN]`
- **Ships:** Cutlass Black, Corsair, Vulture, Caterpillar, Buccaneer, Golem.

### Esperia (`ESPR_`)
- **Lore:** Makes faithful replicas of alien ships, such as Tevarin and Vanduul designs. ([wiki](https://starcitizen.tools/Esperia))
- **Shapes:** These follow the original species, so they vary by ship.
- **SCMFD's choice:** violet `#B8A0FF`.
- **Ships:** Prowler, Talon, Glaive, Blade.

### Gatac Manufacture (`GAMA_`)
- **Lore:** A Xi'an maker of large industrial and transport ships. ([wiki](https://starcitizen.tools/Gatac_Manufacture))
- **Shapes:** Xi'an curves at large scale. CIG's art team talks about "Gatac-style spaces". ([Mar 2026 report](https://starcitizen.tools/Comm-Link:Star_Citizen_Monthly_Report_-_March_2026))
- **SCMFD's choice:** cyan `#6DE6F2`.
- **Ships:** Syulen. The Railen was still in development in March 2026. `[UNCERTAIN]`

### Greycat Industrial (`GRIN_`)
- **Lore:** Makes industrial tools and small utility vehicles. ([wiki](https://starcitizen.tools/Greycat_Industrial))
- **SCMFD's choice:** yellow `#F4D35E`.
- **Ships:** ROC mining vehicle, PTV buggy, STV.

### Kruger Intergalactic (`KRIG_`)
- **Lore:** A German-style maker of snub fighters and racers. ([wiki](https://starcitizen.tools/Kruger_Intergalactic))
- **SCMFD's choice:** coral `#FF846A`.
- **Ships:** P-52 Merlin, P-72 Archimedes, L-21 Wolf, L-22 Alpha Wolf.

### MISC (`MISC_`)
- **Lore:** Musashi Industrial and Starflight Concern. Formed in 2805 by a merger, based on Saisei in the Centauri system. Its ships are modular, ergonomic workhorses. ([wiki](https://starcitizen.tools/Musashi_Industrial_and_Starflight_Concern))
- **Shapes:** Rounded and practical. Off-white and green-grey. A partnership with the Xi'an shows in some curves.
- **HUD:** Has its own colour, according to FlowKits. The colour is not named. I guess teal/green. `[UNCERTAIN]` In 4.0.2, the Starfarer's MFDs were moved lower so they don't clash with the Advanced HUD. ([4.0.2 notes](https://starcitizen.tools/Update:Star_Citizen_Alpha_4.0.2))
- **SCMFD's choice:** teal `#58DDD1`.
- **Ships:** Freelancer, Prospector, Reliant, Starfarer, Hull C, Fortune.

### Mirai (`MRAI_`)
- **Lore:** A sporty brand linked to MISC. It makes racers and light fighters. ([wiki](https://starcitizen.tools/Mirai))
- **SCMFD's choice:** pink-red `#FF647C`.
- **Ships:** Fury, Razor, Pulse hoverbike, Guardian. The Guardian gained LAMP in 4.9. ([LAMP](https://starcitizen.tools/LAMP))

### Origin Jumpworks (`ORIG_`)
- **Lore:** A luxury maker. It is often compared to a high-end car brand. ([wiki](https://starcitizen.tools/Origin_Jumpworks))
- **Shapes:** Smooth, white and minimal. Gold and light-wood trim.
- **Interior:** Bright, clean and high-end.
- **HUD:** "Pale, clean", according to FlowKits. `[UNCERTAIN]` in game.
- **SCMFD's choice:** the only light theme. Background `#E7EAEE`, gold accent `#B08D57`, Montserrat font.
- **Ships:** 300 series, 400i, 600i, 890 Jump, X1, 85X, M80. The M80 gained LAMP in 4.8. ([LAMP](https://starcitizen.tools/LAMP))

### Roberts Space Industries (`RSI_`)
- **Lore:** The oldest human shipbuilder. It built the first commercial quantum drive. Its look is classic, mainstream and blue-badged. ([wiki](https://starcitizen.tools/Roberts_Space_Industries))
- **Shapes:** Clean and balanced. Navy blue and white.
- **HUD:** Blue, according to FlowKits. In 4.6 the Aurora got a "Gold Standard" refresh, which included a refreshed HUD. ([search summary of 4.6 notes](https://starcitizen.tools/Update:Star_Citizen_Alpha_4.6.0)) `[UNCERTAIN]` I could not open that page directly.
- **SCMFD's choice:** blue `#4FB3FF`, Exo 2 font. SCMFD also uses the RSI look as its own neutral theme.
- **Ships:** Aurora, Constellation, Zeus Mk II, Polaris, Salvation, Scorpius.

### Tumbril Land Systems (`TMBL_`)
- **Lore:** A maker of ground vehicles. ([wiki](https://starcitizen.tools/Tumbril_Land_Systems))
- **SCMFD's choice:** tan `#DDB582`.
- **Vehicles:** Cyclone, Nova tank, Storm.

### Others with flyable ships
- **Vanduul (`VNCL_`):** SCMFD has a theme for it, with accent `#FF6858`. Players fly Esperia replicas, not true Vanduul ships.
- Treat any manufacturer not listed here as **default**. Use a neutral Tycho colour for it. Don't guess a brand colour.

## General SC UI language

**Core look** (from the 2015 mobiGlas notes, still mostly true today; [source](https://robertsspaceindustries.com/en/comm-link/engineering/14466-Design-Notes-MobiGlas)) `[OUT OF DATE?]`:
- "blue is the general color used throughout the mG screens". The notes call blue "the culturally recognized color for holographic effects".
- They use "just a few colors, with strong complementary accent colors".
- Text is white or a pale tint of the background colour.
- Shapes are "clear... mostly geometric", with "a strong use of straight pixel lines".
- Panels have solid colour backgrounds to separate sections. A selected item gets "a very bright holographic outline".
- In the lore, mobiGlas apps come from in-world companies, so each app carries its own brand. This is why manufacturer styling fits the setting.

**Today's UI (3.23 to 4.x):**
- The 3.24.2 rework "fully replaces all aspects of the previous HUDs". It added annunciator panels, target status, comms, diagnostics, weapons and engineering screens. It also added an in-cockpit **MFD configuration screen**. ([3.24.2](https://starcitizen.tools/Update:Star_Citizen_Alpha_3.24.2))
- The July 2024 report says CIG "replaced the old Flash MFDs". The goal was a "cleaner and less cluttered flight experience". ([report](https://starcitizen.tools/Comm-Link:Star_Citizen_Monthly_Report_-_July_2024))
- Diegetic screens share "shape, outline, iconography, detail, and colour". Each ship has "subtle colour variations". ([HUDS+GUIS, Mar 2023](https://www.hudsandguis.com/home/star-citizen-revisited-part-1))
- The UI is built from modular parts that can be recombined. ([HUDS+GUIS part 2, May 2023](https://www.hudsandguis.com/home/star-citizen-revisited-part-2))

**Visual traits a fan tool can echo without copying.** These are my own observations. No CIG document lists them.
- Dark, semi-transparent panels with a single accent colour.
- Thin 1px strokes. Chamfered (45°) corners instead of rounded ones. Short corner brackets instead of full borders.
- Condensed, uppercase labels with wide letter spacing. Numbers in a monospaced or tabular style.
- Small status glyphs. Thin progress bars. Faint scan lines or a slight glow on the accent.
- Red/amber/green kept for status only. Cyan or blue as the resting colour.

**Suggestions for Tycho to keep a separate identity.** These are my recommendations.
- Use open fonts, not the official fankit fonts. SCMFD bundles OFL fonts that suit the genre: Rajdhani, Saira Condensed, Chakra Petch, Exo 2 and Montserrat. ([SCMFD Fonts](https://github.com/Dima-McArrow/SCMFD/tree/HEAD/SCMFD/Fonts))
- Keep Tycho's own wordmark and icon on the Tile. Use the manufacturer colour as an accent only, never a manufacturer name or logo.
- Make the Tile's shape different from an MFD frame. For example, use one chamfered corner and a Tycho label.

## Player HUD colour customisation

- **Current state: no colour setting.** I found no in-game option, MFD screen or config file that changes HUD or MFD colours, up to Alpha 4.x.
- **What players can configure:**
  - The 3.24.2 **MFD Config panel**. It covers settings that used to be only in the options menu. ([3.24.2](https://starcitizen.tools/Update:Star_Citizen_Alpha_3.24.2))
  - **LAMP** cockpit light amplification, added in 4.6 (live 28 Jan 2026). It is toggled at "MFD Config Panel > Flight or RALT+L". It brightens the view through the cockpit glass. It does not change HUD colour. ([LAMP](https://starcitizen.tools/LAMP); 4.6 date from [Caliber Dynamics](https://caliberdynamics-website.pages.dev/dispatch/alpha-4-6-lifeline-for-levski))
  - Brightness, gamma, contrast and chromatic aberration in Graphics settings. These are global, not HUD settings. ([Game Options](https://starcitizen.tools/Game_Options)) `[OUT OF DATE?]` The page's rows are tagged 2.x to 3.5.
- **Player demand:**
  - A Spectrum thread asks to "adjust MFD and HUD color and brightness". It mentions that some ships have manufacturer-specific colours. ([Spectrum](https://robertsspaceindustries.com/spectrum/community/SC/forum/61894/thread/adjust-mfd-and-hud-color-and-brightness))
  - A 3.24.3 thread says the new HUD colour theme is "almost impossible" for colour-blind players. ([Spectrum](https://robertsspaceindustries.com/spectrum/community/SC/forum/3/thread/new-hud-color-are-a-disaster-for-the-all-the-color))
  - A dev quote is reported in another thread: colour-blind HUD options "will happen". ([Spectrum](https://robertsspaceindustries.com/spectrum/community/SC/forum/61894/thread/colour-blind-mode)) `[UNCERTAIN]` This comes from a search snippet. I could not open the thread. The date is unknown.
- **What this means for Tycho:** a manufacturer-following accent fits the game. Tycho should also offer a manual colour override and a high-contrast option, because the game itself offers neither.

## Fan-content rules

From the **RSI Terms of Service**, effective 30 Oct 2026 ([source](https://robertsspaceindustries.com/en/tos)):
- Fan work must clearly show it is not official, with wording such as "This is an unofficial Fansite."
- You "must not express or imply any relationship between RSI, the RSI Services, or the Game" and your work.
- You must not "use or adopt any trademarks that might be confusingly similar to such RSI Marks".
- You may not copy or download RSI Content unless that is expressly authorised.
- Use in videos and streams "must be non-commercial". Merchandise and paid access are restricted.
- You must include RSI's trademark and copyright notices where they are provided.

From the **Fankit and Fandom FAQ** ([source](https://support.robertsspaceindustries.com/hc/en-us/articles/360006895793-Star-Citizen-Fankit-and-Fandom-FAQ); I could not open it, so these points come from search summaries) `[UNCERTAIN]`:
- Any commercial use needs a signed licence. That includes products, services, events and company names.
- Images that use fankit material must carry an official logo in a corner, at 50% opacity or more, plus the fankit trademark notice.
- Fan sites need a notice saying they are unofficial and not affiliated with the Cloud Imperium group.

From the **Fankit page** ([source](https://robertsspaceindustries.com/Fankit)):
- You must log in and accept the Fankit Agreement before downloading. The kit includes logos, fonts, art and 3D models.

**Notice wording other fan projects use.** This is a secondary source. One project says it copied the Fankit Agreement notice word for word. ([9thFleet PR #14](https://github.com/SWMann/9thFleet/pull/14))
> This site is not endorsed by or affiliated with the Cloud Imperium or Roberts Space Industries group of companies. All game content and materials are copyright Cloud Imperium Rights LLC and Cloud Imperium Rights Ltd.. Star Citizen®, Squadron 42®, Roberts Space Industries®, and Cloud Imperium® are registered trademarks of Cloud Imperium Rights LLC. All rights reserved.

The middle of that quote was cut off in the diff I read. I filled it with the usual wording. Check it against the Fankit Agreement before using it.

**What this means for Tycho:**
- Show "Unofficial fan project" and the not-affiliated notice in the About screen, the README and the installer.
- Do not put manufacturer logos or names-as-logos on the Tile. Do not use fankit fonts in the UI.
- Do not copy MFD layouts, icons or frames. Echo the style, not the assets.
- Tycho is free and open source, which fits the "non-commercial" rule. Donations or paid tiers would need a closer read of the FAQ.

## Open questions

1. **Which four manufacturers got their own MFD style in 3.24.2, and which got one later in 4.x?** Crusader is confirmed. Aegis, Anvil and Drake are my guess. Check the ISC episode from 12 Sep 2024, or check in game.
2. **What are the real in-game colours?** Every hex value here is an estimate. Ben could take one screenshot per manufacturer in game and sample the MFD accent. That would turn Low rows into High.
3. **Does the shared default style use the same cyan as the visor HUD?** I assumed yes. Not confirmed.
4. **Drake: orange or yellow?** FlowKits says orange. SCMFD uses yellow `#F2C230`. My memory says orange/amber.
5. **Did any 4.x patch add a colour-blind or HUD colour option?** I found none. The 4.x patch notes were not searchable in full.
6. **What exactly does the Fankit Agreement say?** It sits behind a login, and the support FAQ blocked automated reads. Ben should read both once while logged in.
7. **Is a manufacturer colour, with no logo or name, safe under the "confusingly similar marks" rule?** I think so, since a colour alone is not a mark. That is my judgment, not legal advice.
8. **How does Tycho detect the manufacturer?** SCMFD maps Game.log entity prefixes to manufacturers:
   - `AEGS_`, `ANVL_`, `ARGO_`, `BANU_`, `CNOU_`, `CRUS_`, `DRAK_`, `ESPR_`, `GAMA_`
   - `GRIN_`, `KRIG_`, `MISC_`, `MRAI_`, `ORIG_`, `RSI_`, `TMBL_`, `VNCL_`, `XIAN_`

   Tycho should verify these against its own Game.log samples.
