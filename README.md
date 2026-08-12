# SEO Beacon

*Can AI find you, cite you, and act on your site?*

**One Visibility Score, 0-100**, built from three tracks that fail independently:

- **Citation readiness (AEO)** — 45%. Is the page shaped so an engine can quote it?
  43 checks: crawler access, schema, answer structure, depth, E-E-A-T.
- **Agent readiness** — 25%. Can an autonomous agent discover, read and *act on* the
  site? 24 signals following Cloudflare's
  [isitagentready.com](https://isitagentready.com) category model.
- **Brand presence** — 30%. Can an engine resolve you as a real entity worth citing?
  Wikipedia, Wikidata, social footprint, Reddit, reviews.

The most common failure: strong citation readiness, near-zero brand presence.
Perfect schema does not get you cited if the engine has never heard of you.

Plus a **per-engine breakdown across 14 AI systems** — for each one, can it crawl
you, and how citable is what it finds.

Free, open source, and dependency-free: one Python file, standard library only.
Works as a [Claude Code](https://claude.com/claude-code) skill (`/seo-beacon`) or as
a plain CLI.

```
SEO BEACON — AI VISIBILITY REPORT  https://example.com/
══════════════════════════════════════════════════════════════════════════
  VISIBILITY SCORE    31/100   Level 2 · Bot-Aware
══════════════════════════════════════════════════════════════════════════
  ├─ citation readiness   62/100  45% · will AI engines cite this page
  ├─ agent readiness      23/100  25% · can agents use this site
  └─ brand presence        0/100  30% · can engines resolve you as an entity

AEO — CITATION READINESS
  AI Crawler Access        ████████████████████████ 100%  (15.0/15 pts)
  Structured Data          █████████████████░░░░░░░  70%  (14.0/20 pts)
  Answer Structure         █████████████████████░░░  86%  (17.2/20 pts)
  Content Depth            ██████████░░░░░░░░░░░░░░  41%  (6.2/15 pts)
  Authority / E-E-A-T      ██░░░░░░░░░░░░░░░░░░░░░░   8%  (1.0/12 pts)
  Technical / Meta         ██████████████████░░░░░░  77%  (7.7/10 pts)
  AI Files                 ░░░░░░░░░░░░░░░░░░░░░░░░   0%  (0.0/8 pts)

AGENT READINESS
  Discoverability          █████████████░░░░░░░░░░░  53%  (16.0/30 pts)
  Content Accessibility    ██████░░░░░░░░░░░░░░░░░░  27%  (7.0/26 pts)
  Bot Access Control       ░░░░░░░░░░░░░░░░░░░░░░░░   0%  (0.0/22 pts)
  Protocol Discovery       ░░░░░░░░░░░░░░░░░░░░░░░░   0%  (0.0/22 pts)

PER-ENGINE READINESS
  ChatGPT                        █████████████░░░  83  can crawl and cite
  Claude                         █████████████░░░  83  can crawl and cite
  Perplexity                     ████░░░░░░░░░░░░   0  BLOCKED — cannot crawl this page
  Google AI Overviews / Gemini   █████████████░░░  83  can crawl and cite

TOP FIXES (by score impact)
  1. +9.0 pts — Explicit AI bot rules (Bot Access Control)
  2. +9.0 pts — llms.txt on-ramp (Discoverability)
  3. +3.0 pts — FAQPage / HowTo schema (Structured Data)
     3 question-style headings on the page but no FAQPage schema.
     → Wrap Q&A sections in FAQPage JSON-LD with mainEntity → Question →
       acceptedAnswer.text; AI engines lift these verbatim.
```

## Install

**Requires:** Python 3.8+. Nothing else.

```bash
git clone https://github.com/irastasiuk28/seo-beacon.git
cd seo-beacon
python3 seo_beacon.py https://example.com/
```

### As a Claude Code skill

Then type `/seo-beacon` in any Claude Code session. **On first use it asks which
website to audit**, along with your brand name and competitors, and saves the answers
to `beacon.config.json` in that project. After that, `/seo-beacon` just runs.

The config is per-project, so one install serves any number of different sites:

```json
{
  "site": "https://example.com/",
  "brand": "Example",
  "competitors": ["Competitor A", "Competitor B"]
}
```

```bash
# every project on this machine
./install.sh

# or a single project
mkdir -p /path/to/project/.claude/skills/seo-beacon
cp SKILL.md seo_beacon.py /path/to/project/.claude/skills/seo-beacon/
```

## Usage

```bash
# first run — audit a site and remember it
python3 seo_beacon.py https://example.com/ --save-site

# afterwards, no URL needed
python3 seo_beacon.py --tasks

# one specific page
python3 seo_beacon.py https://example.com/blog/post

# a whole site, ranked worst-first, with site-wide priorities
python3 seo_beacon.py --sitemap https://example.com/sitemap.xml --limit 25

# a draft that isn't published yet
python3 seo_beacon.py --file draft.html --base-url https://example.com/blog/slug

# shareable HTML report + machine-readable JSON
python3 seo_beacon.py https://example.com/ --html report.html --json result.json

# pre-publish gate: exits 1 if the score is below 70
python3 seo_beacon.py https://example.com/ --fail-under 70

# write ready-to-deploy fix files for every failed check
python3 seo_beacon.py https://example.com/ --generate ./beacon-fixes
```

### Auto-generated fixes

`--generate DIR` writes deployable artifacts — but only for checks that actually
failed, so the directory is a to-do list, not boilerplate:

| File | What it is |
|---|---|
| `llms.txt` | Seeded from the site's own internal links and meta description |
| `robots-ai-block.txt` | Named `User-agent` groups for every AI crawler, `Content-Signal`, `Sitemap` |
| `faq-schema.json` | FAQPage JSON-LD built from the page's own question headings and the answers beneath them |
| `article-schema.json` | Article JSON-LD with dates, `Person` author, publisher `sameAs` |
| `AGENTS.md` | Agent usage policy stub |
| `well-known-mcp.json` | MCP server card stub |

Files contain `TODO:` markers wherever a human must supply a real value. Review
before deploying.

| Flag | Effect |
|---|---|
| `--file` / `--base-url` | Audit local HTML instead of fetching |
| `--sitemap` / `--limit` | Crawl mode with a per-page summary table |
| `--html` / `--json` | Write a styled report / structured output |
| `--quiet` | Score and top fixes only |
| `--fail-under N` | Exit code 1 below N, for CI |
| `--generate DIR` | Write deployable fix files for failed checks |
| `--history CSV` | Append the run and print the change since last time |
| `--save-site` | Remember this URL in `beacon.config.json` |
| `--config JSON` | Use a different config path |
| `--tasks [CSV]` | Prioritised task board ranked by opportunity vs effort |
| `--init-prompts JSON` | Starter prompt set for tracking brand mentions |
| `--prompt-report CSV` | Mention rate, share of voice, and cited-source domains |
| `--workers` / `--timeout` | Crawl concurrency and per-request timeout |

## Beyond the score

**Task board** (`--tasks`) ranks every finding by opportunity — headline points the
fix is worth, discounted by effort — and types each as **Technical** (fix it in code),
**Owned** (your content/profiles) or **Earned** (someone else must act). A cheap
2-point fix routinely outranks an expensive 7-point one.

**Prompt tracking** (`--init-prompts`, `--prompt-report`) measures whether you are
*actually* mentioned, not just whether you *could* be: mention rate, share of voice
against competitors, and the domains AI cites most for your category. That last table
is a target list — getting into the pages AI already quotes beats more schema.

## What it checks

68 checks in total — 43 citation-readiness, 19 agent-readiness signals and 6 brand
presence — each individually weighted. "Top fixes" is sorted by how many points each fix is
actually worth, across both scores.

### Citation readiness (AEO) — 43 checks, 7 categories (45% of the score)

| Category | Pts | Covers |
|---|---|---|
| **AI Crawler Access** | 15 | robots.txt rules for GPTBot, OAI-SearchBot, ChatGPT-User, ClaudeBot, Claude-User, PerplexityBot, Google-Extended, Applebot-Extended, Bingbot, CCBot and more; whether the server actually serves an AI user-agent; meta robots; AI opt-out tags |
| **Structured Data** | 20 | JSON-LD present *and parseable*, Article/Product/WebPage typing, Organization, FAQPage/HowTo with populated answers, BreadcrumbList, author, publish + modified dates |
| **Answer Structure** | 20 | one H1, heading hierarchy, question-phrased headings, whether each section answers within its first 35 words, TL;DR block, lists, comparison tables, paragraph and sentence length |
| **Content Depth** | 15 | word count, freshness from `dateModified`, density of concrete numbers and specifics, external citations to authority domains, internal linking |
| **Authority / E-E-A-T** | 12 | named byline + `Person` schema, linkable author/team page, `sameAs` entity links, About & Contact reachable, experience and trust signals |
| **Technical / Meta** | 10 | title, meta description, canonical, Open Graph, `html lang`, image alt text, viewport |
| **AI Files** | 8 | `llms.txt`, `llms-full.txt`, `sitemap.xml`, Sitemap declared in robots.txt |

Grades: **A+** ≥90 · **A** ≥80 · **B** ≥70 · **C** ≥60 · **D** ≥50 · **F** below.

### Brand presence — 6 checks, 3 categories (30% of the score)

| Category | Pts | Signals |
|---|---|---|
| **Knowledge Entity** | 40 | Wikipedia article, Wikidata entity |
| **Social Footprint** | 35 | linked + schema-declared profiles across 7 platforms, video presence |
| **Community & Reviews** | 25 | Reddit / forum footprint, third-party review platforms |

### Agent Readiness — 24 signals, 5 categories (25% of the score)

Modelled on Cloudflare's Agent Readiness Score. Commerce is reported but **not
scored**, matching the reference tool.

| Category | Pts | Signals |
|---|---|---|
| **Discoverability** | 30 | robots.txt (RFC 9309), sitemap.xml, llms.txt, Link headers (RFC 8288), DNS-AID SVCB records |
| **Content Accessibility** | 26 | `Accept: text/markdown` negotiation, `.md` mirror, content present without JavaScript, semantic HTML + JSON-LD |
| **Bot Access Control** | 22 | named AI bot rules, `Content-Signal` directive, Web Bot Auth |
| **Protocol Discovery** | 22 | MCP server card (SEP-1649), API Catalog (RFC 9727), OAuth discovery (RFC 8414 / RFC 9728), Auth.md, Agent Skills index, WebMCP, agents.json / AGENTS.md |
| **Commerce** | — | x402, MPP, UCP (`/.well-known/ucp`), ACP (`/.well-known/acp.json`) |

Overall levels: **Level 5 AI-Native** ≥90 · **Level 4 AI-Optimized** ≥75 ·
**Level 3 AI-Visible** ≥55 · **Level 2 Bot-Aware** ≥35 ·
**Level 1 Basic Web Presence** below.

Agent-track levels: **Level 5 Agent-Native** ≥80 · **Level 4 Agent-Friendly** ≥60 ·
**Level 3 Discoverable** ≥40 · **Level 2 Bot-Aware** ≥20 · **Level 1 Invisible** below.

### Two things most AEO graders miss

- **Server-level crawler blocks.** The page is re-fetched using the GPTBot user-agent.
  A Cloudflare or WAF bot rule can return 403 to AI crawlers even when `robots.txt`
  allows them — robots.txt alone won't tell you that.
- **JSON-LD that doesn't parse.** A malformed block is silently dropped by search and
  answer engines, but counts as "schema present" in most checkers. This one validates
  it, and flags FAQ questions missing an `acceptedAnswer`.

## Notes

- Pages are audited **as served**. If your site is client-side rendered, the checker
  sees what AI crawlers see — which is the point.
- Network-dependent checks are **skipped, not failed**, when running offline or when
  the host is unreachable. Their weight drops out of the denominator and the report
  says so, so an offline score is never silently inflated or deflated.
- Scores are directional, not a guarantee of citation. No public tool can measure
  actual LLM citation rates; this measures the on-page and technical factors that
  make citation possible.
- Cloudflare does not publish per-check weights or level thresholds for its Agent
  Readiness Score, so **the weights and level names here are this tool's own** and
  scores will not match isitagentready.com exactly. The category model and the
  underlying standards checked are the same.
- Agent Readiness reports `n/a` rather than a number when the site is unreachable —
  nearly all of those signals are live network probes.

## License

MIT
