---
name: seo-beacon
description: SEO Beacon audits a website for AI visibility — will ChatGPT, Claude, Perplexity, Gemini and AI Overviews find, cite and act on it. Returns one 0-100 Visibility Score built from citation readiness (AEO/GEO), agent readiness and brand presence, a per-engine breakdown across 14 AI systems, a prioritized task board, and optional prompt/competitor tracking; then offers to apply the fixes. Use when the user gives a URL and asks about AEO, GEO, AI search visibility, agent readiness, "is my site AI ready", "will ChatGPT cite us", share of voice vs competitors, llms.txt, AI crawler access, MCP/well-known discovery, or schema/FAQ markup.
---

# SEO Beacon

*Can AI find you, cite you, and act on your site?*

`seo_beacon.py` is bundled next to this file. Stdlib-only Python — no install step.

**Locating the script:** it lives in this skill's own directory. If `seo_beacon.py` is
not in the working directory, use the copy alongside this SKILL.md
(`~/.claude/skills/seo-beacon/seo_beacon.py` for a personal install, or
`<project>/.claude/skills/seo-beacon/seo_beacon.py` for a project install).

**One Visibility Score (0-100)**, built from three tracks. They fail independently,
which is why all three are shown:

| Track | Weight | Question it answers |
|---|---|---|
| **Citation readiness (AEO)** | 45% | Is the page shaped so an engine can quote it? |
| **Agent readiness** | 25% | Can an autonomous agent discover, read and act on the site? |
| **Brand presence** | 30% | Can an engine resolve you as a real entity worth citing? |

The most common failure pattern: strong citation readiness, near-zero brand presence.
Perfect schema does not get you cited if the engine has never heard of you.

## The workflow — follow this order

### 0. Establish which site this is for — before anything else

Never assume a target. On the first run in a project, work it out in this order:

1. **The user named a URL in their message** → use it.
2. **`beacon.config.json` exists in the project** → use its `site`, and say which site
   you are auditing so a wrong saved value is visible immediately.
3. **Otherwise ASK.** Use `AskUserQuestion`: *"Which website should I run this audit
   for?"* Offer any candidates you can genuinely detect as options — a homepage in
   `package.json`, a `CNAME` file, a domain in the README, the git remote's project
   site — and let the user type their own. Do not guess, and do not audit a domain
   just because it appears somewhere in the repo.

Ask for two more things in the same round, because both change what the tool can do:

- **Brand name** as it should appear in AI answers (defaults to the Organization name
  in the site's schema).
- **Competitors**, 3-8 of them — required for share-of-voice tracking, and used to
  generate comparison-page tasks.

Then persist it so you never ask twice:

```bash
python3 seo_beacon.py <url> --save-site
```

That writes `beacon.config.json` (`site`, `brand`, `competitors`). Add competitors to
that file directly. After it exists, a bare `python3 seo_beacon.py` re-audits the
saved site.

Re-ask only if the user says they want a different site, or the config is missing.

### 1. Run it

```bash
python3 seo_beacon.py <url> --tasks       # or bare, once the site is saved
```

Never guess at a score or describe findings from reading the page yourself. Run the tool.

Whole site: `--sitemap <sitemap-url> --limit 25`. Unpublished draft:
`--file draft.html --base-url <url-it-will-live-at>`.

### 2. Show the score first

Lead with the headline number. The user wants to see "31/100" before any explanation.

```
VISIBILITY SCORE    31/100   Level 2 · Bot-Aware
  ├─ citation readiness   62/100  45%
  ├─ agent readiness      23/100  25%
  └─ brand presence        0/100  30%
```

When tracks diverge sharply, say why in one sentence — that gap is usually the most
useful thing in the report.

Then the per-category bars and the per-engine table. The engine table covers 14 AI
systems (ChatGPT, Google Gemini / AI Overviews, Claude, Perplexity, Microsoft Copilot,
Apple Intelligence, Meta AI, Amazon Rufus, DeepSeek, Mistral, You.com, Cohere,
ByteDance Doubao, open models via Common Crawl). A `BLOCKED` row means that engine
cannot see the page at all — call it out immediately, it outranks everything else.

### 3. Show the task board

`--tasks` ranks every finding by **opportunity** — headline points the fix is worth,
discounted by effort. A cheap 2-point fix can outrank an expensive 7-point one, which
is the point. Tasks are typed:

- **Technical** — you can fix it in code, right now.
- **Owned** — your content or your profiles; you control it, it takes work.
- **Earned** — someone else must act (a Wikipedia editor, a listicle author, real
  Reddit users). Slowest and usually highest-value. Never fake these.

Report the top 5 with their opportunity scores and types.

### 4. Ask before changing anything

Use `AskUserQuestion`. Sensible options:

- **Fix everything I can** — generate files *and* edit templates/generator
- **Just generate the files** — llms.txt, robots block, FAQ schema, AGENTS.md
- **Only the top 3** — highest-opportunity items
- **Nothing for now** — just the report

Never edit without asking. Never deploy to a live site on your own.

### 5. Track it over time

```bash
python3 seo_beacon.py <url> --history beacon-history.csv
```

Appends the run and prints movement since last time. The weekly GitHub Action
(`.github/workflows/seo_beacon_weekly.yml`) does this automatically. If the score
dropped, lead with that.

### 6. Then fix, for real

```bash
python3 seo_beacon.py <url> --generate ./beacon-fixes
```

Writes deployable artifacts, but *only for checks that failed*:

| File | Deploy to |
|---|---|
| `llms.txt` | site root (seeded from the site's own internal links) |
| `robots-ai-block.txt` | append to robots.txt — named AI agents, Content-Signal, Sitemap |
| `faq-schema.json` | FAQPage JSON-LD built from the page's own question headings |
| `article-schema.json` | Article JSON-LD with dates, Person author, publisher sameAs |
| `AGENTS.md` | site root |
| `well-known-mcp.json` | `/.well-known/mcp.json` — only if there's an API |

Generated files contain `TODO:` markers. **Resolve every one before handing them
over** — ask the user, or fill from the repo. Never ship a file with a TODO in it.

Then do the code-side fixes in whatever generates the site:

- **Find the generator first.** If pages come from a template, CMS theme or script, a
  content finding is a template bug — fix it once at the source, not per page.
- **Typical fixes:** add `datePublished`/`dateModified` to Article JSON-LD; swap an
  Organization author for a named `Person` with an author-page URL; build a `FAQPage`
  node from question headings; add `sameAs`; prepend a "Quick answer" after the H1.
- **Host-side fixes** (robots.txt, llms.txt, `.well-known/`, Link headers) usually
  live in the platform admin on Shopify, Webflow or Squarespace. Give the user the
  exact file, the exact content, and where to paste it.

Re-run afterwards and show before/after. That's the proof the work landed.

## Prompt & competitor tracking (the off-site half)

The on-site score says whether you *could* be cited. This measures whether you
*are*. It is the highest-signal thing in the whole skill when the brand-presence
track is weak.

```bash
python3 seo_beacon.py <url> --init-prompts prompts.json   # starter prompt set
python3 seo_beacon.py <domain> --prompt-report runs.csv   # aggregate results
```

**How to actually run it:** `--init-prompts` writes the prompts and competitor slots.
You then run each prompt and record what came back. Do this with `WebSearch` plus
your own reading of the results — one row per (prompt, model) in a CSV with columns:

```
date,prompt,model,brand_mentioned,position,competitors_mentioned,cited_domains
```

`--prompt-report` aggregates that into mention rate, your-site-cited rate, share of
voice vs competitors, and the domains AI cites most.

**Be honest about the method.** Running prompts through your own web search is not
the same as querying ChatGPT's or Perplexity's live model. Label rows with the model
actually used, and tell the user when a result is your own search rather than a
third-party engine. Never present one as the other.

The payoff is the SOURCES table: the domains AI already cites for your category are
a literal target list. Getting into those pages moves the needle far more than more
schema.

## Scoring reference

**Overall level:** Level 5 AI-Native ≥90 · Level 4 AI-Optimized ≥75 ·
Level 3 AI-Visible ≥55 · Level 2 Bot-Aware ≥35 · Level 1 Basic Web Presence below.

**Citation readiness — 100 pts, 7 categories (45% of headline)**

| Category | Pts | Covers |
|---|---|---|
| AI Crawler Access | 15 | per-bot robots rules, whether the server serves an AI user-agent (WAF blocks), meta robots, AI opt-out |
| Structured Data | 20 | JSON-LD present *and parseable*, Article/Product typing, Organization, FAQPage/HowTo, Breadcrumb, author, dates |
| Answer Structure | 20 | one H1, hierarchy, question headings, answer-in-first-35-words, TL;DR, lists, tables, para/sentence length |
| Content Depth | 15 | word count, freshness, concrete numbers, external citations, internal links |
| Authority / E-E-A-T | 12 | byline + Person schema, author page, sameAs, About/Contact, trust signals |
| Technical / Meta | 10 | title, description, canonical, OG, lang, alt text, viewport |
| AI Files | 8 | llms.txt, llms-full.txt, sitemap.xml, Sitemap in robots.txt |

**Agent readiness — 100 pts, 4 scored categories + commerce (25% of headline)**

| Category | Pts | Signals |
|---|---|---|
| Discoverability | 30 | robots.txt (RFC 9309), sitemap, llms.txt, Link headers (RFC 8288), DNS-AID SVCB |
| Content Accessibility | 26 | `Accept: text/markdown` negotiation, `.md` mirror, content without JS, semantic HTML + JSON-LD |
| Bot Access Control | 22 | named AI bot rules, Content-Signal, Web Bot Auth JWKS |
| Protocol Discovery | 22 | MCP server card (SEP-1649), API Catalog (RFC 9727), OAuth discovery (RFC 8414/9728), Auth.md, Agent Skills index, WebMCP, AGENTS.md |
| Commerce | — | x402, MPP, UCP, ACP — reported, **not scored**, matching the reference tool |

**Brand presence — 100 pts, 3 categories (30% of headline)**

| Category | Pts | Signals |
|---|---|---|
| Knowledge Entity | 40 | Wikipedia article, Wikidata entity |
| Social Footprint | 35 | linked+declared profiles across 7 platforms, video presence |
| Community & Reviews | 25 | Reddit/forum footprint, third-party review platforms |

## Project config

`beacon.config.json` in the project root:

```json
{
  "site": "https://example.com/",
  "brand": "Example",
  "competitors": ["Competitor A", "Competitor B"]
}
```

`site` is used when no URL is passed. `brand` and `competitors` seed
`--init-prompts` and the share-of-voice report. The file is per-project, so one
install of this skill serves any number of different companies' sites.

## Accuracy notes — do not overstate these

- The citation-readiness set follows the consensus across public AEO graders. The
  agent set follows Cloudflare's published category model for isitagentready.com;
  **Cloudflare does not publish per-check weights or level thresholds, so the weights
  and level names here are this tool's own.** Scores will not match theirs exactly.
  Say so if the user compares them.
- Track weights (45/25/30) are a judgement call, not a measured constant. They live
  in `TRACK_WEIGHTS` and are easy to change.
- No score predicts actual citation. Only the prompt tracking measures real outcomes,
  and only for the prompts you actually run.
- A track that could not be measured reports `n/a` and is dropped from the headline
  (the remaining tracks are renormalised). Its tasks are excluded too. Never present
  a partial score as real.
- Pages are audited as served. Client-side-rendered sites score low on
  `Content present without JavaScript` because that is genuinely what agents see.
- Brand presence uses public Wikipedia/Wikidata/Reddit endpoints. Blocked network or
  rate limits show as `n/a`, not as a zero.
