# Web search services compared (research, 22 November 2026)

For [#365](https://github.com/benshapyro/tycho-ai/issues/365) and the timing questions folded in from [#361](https://github.com/benshapyro/tycho-ai/issues/361). Background: [ADR 15](../adr/0015-knowledge.md). Measured with [`tools/bench/search_compare.py`](../../tools/bench/search_compare.py) on the 40 questions of `web_trial.py`, 38 of which have reference answers. Raw answers and grades are in the project's files (`search-compare/`). Prices and terms change: check again before relying on them.

## What was compared

Everything ran through one OpenRouter key, with no new accounts:

- **Tycho today**: `WebSearch` as players have it (the game's sites first, then the open web with up to two searches and two page reads), unchanged, on Haiku 5.5. OpenRouter passes Anthropic's own search and fetch tools through its Anthropic-shaped endpoint, so these are the same requests Tycho sends to Anthropic.
- **Exa, Parallel and Perplexity through OpenRouter's web plugin**: one Haiku 5.5 request. OpenRouter searches once with the question, puts five results in the request, and Haiku answers with Tycho's open-web brief. No page reads.

A Sonnet 5.5 judge graded each question's answers side by side, shuffled, against the reference answers. Tycho's search ran one round; the three plugins ran two.

## Results

| | Right | Partial | Wrong | None | Median | 9 in 10 within | Slowest | Cost per question |
|---|---|---|---|---|---|---|---|---|
| Tycho today (1 round, 38 graded) | 14 | 14 | 6 | 4 | 10.1 s | 12.7 s | 14.9 s | 3.1¢ |
| Perplexity (2 rounds, 75 graded) | 37 | 26 | 12 | 0 | 1.5 s | 3.2 s | 4.1 s | 0.57¢ |
| Parallel (2 rounds, 75 graded) | 40 | 20 | 15 | 0 | 3.3 s | 4.6 s | 7.2 s | 0.61¢ |
| Exa (2 rounds, 75 graded) | 31 | 31 | 12 | 1 | 2.6 s | 4.3 s | 6.0 s | 0.75¢ |

Times are from the request to the spoken answer's text. Costs are what OpenRouter billed (the whole first run, judge included, was $2.19; the second $0.98).

- **Speed and cost are clear wins.** The plugins answer in 1.5 to 3.3 s instead of about 10 s, at a fifth of the price.
- **Quality is at least as good.** Per round, Tycho's search had 14 right and 6 wrong; Perplexity averaged 18.5 right and 6 wrong, Parallel 20 right and 7.5 wrong. Within one service, the same question got the same grade in both rounds only 22 to 25 times out of 38, so small gaps are noise. The same handful of questions were wrong for every service (decoupled mode's key, "nine" refining methods, ship claims, the freight elevators): those are old community claims, or reference answers that need checking in the game.
- **Tycho's search hardly reads pages.** It went to the open web on 35 of 40 questions and read a page only twice, so most of its 10 s is two rounds of searching, not reading.
- **Answers came back clean.** OpenRouter's default lead-in asks for markdown links, which would be read aloud; the bench replaces it with a line asking for none (`SEARCH_PROMPT`), and no answer had a link after that.

## Privacy and terms

- Through OpenRouter, the query goes to OpenRouter and the search engine, and the results to the model. Ben's OpenRouter account has zero data retention on, and Tycho asks for no data collection (#488). Under those rules OpenRouter allowed Exa, Parallel and Perplexity, and **refused Firecrawl**.
- Players would need no account with the search company: OpenRouter's terms cover it, and the cost comes off the player's OpenRouter credit.
- Today's search sends the query to Anthropic, under the player's Anthropic key and terms.

## Services that need their own account (not measured)

- **Google Custom Search JSON API**: closed to new customers, and switched off on 1 January 2027. Its replacement (Vertex AI Search) only searches up to 50 chosen sites. Not an option.
- **Brave Search API**: about $5 per 1,000 searches with $5 a month free; zero data retention only on enterprise plans; plans differ in the right to use results with an AI model.
- **Tavily**: 1,000 free credits a month, then about $0.008 a search.
- **Exa directly**: $10 a month free, then about $7 per 1,000 searches. The same engine as through OpenRouter.
- **Jina Reader** (page reading without a key, rate-limited) and **Firecrawl**: page readers, useful only if page reads mattered; they barely did here.

Each would mean a second key for every player and a new set of terms, for no gain over what OpenRouter already offers.

## The #361 timing questions

- **How long the open-web search takes:** median 10.1 s, 9 in 10 within 12.7 s, slowest 14.9 s here, and 16.4 s in #372's run. The search's own limit is now 18 s (it was 25 s), and the brain gets 23 s from the search's start (it was 27 s), so it always has 5 s to answer after the slowest search allowed. Before, a search that ran to its limit left the brain 2 s.
- **Searches per question:** at most 3 (one on the game's sites, two on the open web), in 2 requests. No search paused and went on, so the continued request never ran and nothing went over. No change needed.
- **Web fetch on Haiku without a beta header:** it worked (two pages read) through OpenRouter. Not checked on Anthropic's own API from the cloud, because no Anthropic key was available there; players have had it on since 13 November with no reported error.

## Recommendation

**Move Tycho's web search to OpenRouter's web plugin with Perplexity as the engine**, as an ADR 15 amendment (Ben's call). It answers in about 1.5 s instead of 10, costs about half a cent instead of 3, does at least as well on the questions, had the fewest wrong answers of the three alternatives along with Exa, and works for players who only have an OpenRouter key, who have no web search today (#112). Parallel is the close second: a few more right answers, but more wrong ones too.

What it costs: players with only an Anthropic key would need an OpenRouter key for web search, or keep today's search. Keeping today's search for them is the gentler change.

A smaller step, whichever way Ben goes: since OpenRouter passes Anthropic's own search tools through, players with only an OpenRouter key could get today's search through OpenRouter, with no new service at all.
