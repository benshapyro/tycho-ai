# Knowledge: sources, terms and tool design (research, 1 November 2026)

Background for [ADR 15](../adr/0015-knowledge.md). "Verified" means seen on a page or in a live response on 1 November 2026; anything else is marked as inference. APIs and terms change, so check again before relying on a detail.

## Data sources

### UEX Corp API 2.0 (trade)

- Base `https://api.uexcorp.uk/2.0/`; docs at <https://uexcorp.space/api/documentation/>. `GET /game_versions` returned live 4.10.2, PTU 4.10.3.
- **No token needed for reads (verified).** `commodities`, `terminals`, `vehicles`, `commodities_prices`, `commodities_routes` all answered without one. A Bearer token (from "My Apps") is for user and write endpoints, and possibly a higher or attributable quota (inference).
- **Rate limit (verified):** 172,800 requests a day, or 120 a minute; past that, status `requests_limit_reached`.
- **Terms (verified, <https://uexcorp.space/about/terms>):** personal, non-commercial use; no attribution required (a "powered by" badge is offered); endpoints may change without notice; access can be cut for misuse; no warranty.
- **Prices:** `GET /commodities_prices` by commodity or terminal (name, code, slug or id). One row per terminal: `price_buy`, `price_sell` (with min, max, avg, week and month variants), `scu_buy`, `scu_sell_stock`, status, `date_modified`, report counts, and the terminal's system, planet, station and city names. Cached server-side for 30 minutes, updated hourly.
- **Routes:** `GET /commodities_routes` needs a commodity or an origin; returns margin, ROI, profit, distance, SCU reachable, and dock and elevator flags, up to 500 rows.
- **Freshness:** `date_modified` is the only per-price timestamp, so Tycho can say "reported 3 hours ago".
- **Static lists** (`commodities`, `terminals`, `star_systems`) load once to turn names into ids.

### Star Citizen Wiki API (ships, items, blueprints)

- `https://api.star-citizen.wiki`, OpenAPI at `/api/openapi`. Public, no key for reads; only search endpoints are rate-limited (60 a minute per IP).
- **Terms (verified, <https://api.star-citizen.wiki/developers>):** "Please credit api.star-citizen.wiki in public projects"; "commercial use is not permitted under the RSI Fandom FAQ". The code is MIT; the data has no stated licence. It comes from game files (scunpacked-data and ScDataDumper, which have no licence either).
- **Per patch (verified):** `/api/v2/game-versions` lists 4.10.2-LIVE as the default; records carry `version` and `updated_at`.
- **Vehicles:** `/api/v2/vehicles`, 310 records: manufacturer, career, role, crew min and max, cargo, size, dimensions, mass, speeds, quantum, shields, health, fuel, insurance, MSRP, loaners and UEX prices.
- **Items:** `/api/v2/items`, 12,617 records, with weapon and vehicle-item views. **Blueprints:** `/api/v2/blueprints`, 1,617 records: output, craft time, ingredients, dismantle returns and unlocking missions.
- **Paging:** 50 records a page in practice (asking for more is ignored), so a full pull is about 300 requests: 7 for vehicles, 33 for blueprints and 253 for items. There is no bulk download.

### starcitizen.tools (prose)

- MediaWiki with TextExtracts, so plain-text summaries are available. Structured tables come through the Bucket extension (there's no Semantic MediaWiki or Cargo, whatever the wiki's own API page says).
- **Licence:** CC BY-SA 4.0. Stored or adapted text must stay CC BY-SA and be credited. It's kept as data, apart from the Apache-2.0 code.
- **Bots:** no bot policy page and no published rate limit. robots.txt blocks AI crawlers and AI training (`ai-train=no`) and says nothing about reading pages to answer a question. Occasional pulls with a clear User-Agent seem acceptable; fetching live for each question is less clearly OK (inference).

### Not used

- **scunpacked-data, ScDataDumper:** no licence, so all rights are reserved; don't redistribute.
- **FleetYards:** GPL-3.0 code; no data terms found.
- **Erkul, SPViewer:** no public API or data licence.

### Credits Tycho must show (credits screen and README)

- Trade data: UEX Corp (uexcorp.space), reported by players.
- Ship, item and blueprint data: api.star-citizen.wiki.
- Wiki text: Star Citizen Wiki (starcitizen.tools), CC BY-SA 4.0.
- Tycho is not affiliated with Cloud Imperium Games.

## Tool and loop design

Sources: Anthropic's [Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents), [Advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use), [Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents), [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents), and the [tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview), [parallel tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use), [prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) and [web search](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool) docs. For voice: [LiveKit function tools](https://docs.livekit.io/agents/build/tools/function-tools/), [Azure voice agent best practice](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/voice-agent-best-practice), [OpenAI realtime prompting](https://developers.openai.com/api/docs/guides/realtime-models-prompting).

What the sources say:

- **Few tools, no overlap.** The description matters most: what the tool does, when to use it, when not to, and its limits. One to five `input_examples` per tool help with tricky inputs.
- **Results:** return only what matters, and give an error that says what to try next. `strict: true` stops malformed calls.
- **Tool search and programmatic tool calling** pay off only with more than about 10 tools or long chains of calls. Both add latency, so Tycho doesn't use them.
- **Caching:**
  - The cached prefix is tools, then system, then messages.
  - Changing the tools invalidates everything.
  - Changing `tool_choice` or the parallel setting invalidates the messages.
  - Adding or removing web search invalidates the system prompt and messages.
- **Parallel calls:** independent read-only calls can run in parallel. All their results go back in one user message, with the tool results first.
- **Untrusted text** belongs in tool results, never in the system prompt or the user's turn, and Tycho's own instructions never go inside a tool result.
- **Voice:** play a recorded hold line while a slow tool runs and cancel it if the answer comes first. Say failures out loud: silence sounds like a dropped connection.

## Rules for Tycho

1. **A look-up never causes a press.** Presses come only from the command list, through the safety gate. After a web result in a turn, the brain may not press at all: the player confirms by voice.
2. **Every request sends the same tools, `tool_choice`, effort and parallel setting.** Web search is always offered, limited to one search per request, and the prompt steers when to use it. Tools are never added or removed between turns, or the cache misses.
3. **Separate tools for separate sources:** ships, items and blueprints, trade, and web search. Each description says when not to use the tool.
4. **Names are free text, resolved in code:** an alias table ("Connie" means Constellation), then fuzzy matching. If several match, return the top three candidates so Claude can choose or ask. If none match, the error suggests close names.
5. **Results are compact JSON**, under about 400 tokens, with `source`, `game_version`, `as_of` and `stale`. Wiki text inside a result is labelled as community text.
6. **Limits for each request:**
   - at most 3 calls to Claude;
   - 4 seconds in all;
   - a timeout on every HTTP call;
   - failures are said out loud.
7. **Parallel look-ups stay on.** "Connie vs Freelancer cargo" is one round trip.
8. **Filler lines are recorded clips** (about 10), started by a timer at 0.7 s and cancelled if the answer comes first. Claude never writes them.
9. **A look-up starts as soon as its tool call is complete**, and likely ship records can be loaded while Claude thinks.
10. **The local copy is one read-only SQLite file per patch**, holding its own patch version and built on the player's PC from the Wiki API (ADR 15). The names are loaded into memory at startup for fuzzy matching.
11. **The briefing tells Claude** to answer settled facts from memory, use the local tools for numbers, and use the web only as a last resort.
12. **A labelled eval checks:**
    - choosing the right tool, including look-ups it didn't need and ones it missed;
    - whether answers match the data;
    - how often web search is used;
    - time to the first word;
    - an injection set, planted wiki text that must cause zero presses.

    It runs again each patch.
