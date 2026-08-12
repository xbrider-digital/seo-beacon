#!/usr/bin/env python3
"""
SEO Beacon — can AI find you, cite you, and act on your site?
=============================================================

Scores a page (or a whole site via sitemap) 0-100 on AI visibility across three
tracks that fail independently: citation readiness (45%), agent readiness (25%)
and brand presence (30%). Covers ChatGPT, Claude, Perplexity, Gemini / AI
Overviews, Copilot and 9 more engines.

Runs 45 checks across 7 weighted categories:

    AI Crawler Access    15   can the bots physically reach the page
    Structured Data      20   JSON-LD schema the engines read
    Answer Structure     20   is the content shaped like an answer
    Content Depth        15   is there anything worth citing
    Authority / E-E-A-T  12   why should it be trusted
    Technical / Meta     10   the basics
    AI Files              8   llms.txt, sitemap

Stdlib only — no pip install needed.

Usage:
    python3 seo_beacon.py https://example.com/                     # audit a page
    python3 seo_beacon.py https://example.com/ --tasks             # + task board
    python3 seo_beacon.py --sitemap https://example.com/sitemap.xml --limit 25
    python3 seo_beacon.py --file draft.html --base-url https://example.com/blog/slug
    python3 seo_beacon.py https://example.com/ --save-site         # remember this site
    python3 seo_beacon.py                                          # re-audit saved site
"""

import argparse
import concurrent.futures
import gzip
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser

VERSION = "1.0"

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
GPTBOT_UA = "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; GPTBot/1.2; +https://openai.com/gptbot"

# The crawlers that actually matter for AI answer visibility.
# (agent token in robots.txt, human label, is it required for citations)
AI_CRAWLERS = [
    ("GPTBot", "OpenAI training/index", True),
    ("OAI-SearchBot", "ChatGPT search index", True),
    ("ChatGPT-User", "ChatGPT live browsing", True),
    ("ClaudeBot", "Anthropic index", True),
    ("Claude-User", "Claude live browsing", True),
    ("PerplexityBot", "Perplexity index", True),
    ("Perplexity-User", "Perplexity live fetch", False),
    ("Google-Extended", "Gemini / AI Overviews grounding", True),
    ("Applebot-Extended", "Apple Intelligence", False),
    ("Bingbot", "Copilot / Bing", True),
    ("meta-externalagent", "Meta AI", False),
    ("CCBot", "Common Crawl (feeds many LLMs)", False),
    ("Amazonbot", "Alexa / Rufus", False),
]

SKIP_TEXT_TAGS = {"script", "style", "noscript", "svg", "template", "iframe"}
BLOCK_BOUNDARY = {"p", "div", "li", "td", "th", "br", "section", "article", "h1",
                  "h2", "h3", "h4", "h5", "h6", "tr", "blockquote"}

QUESTION_RE = re.compile(
    r"^\s*(how|what|why|when|where|who|whom|whose|which|can|could|should|would|"
    r"will|do|does|did|is|are|was|were|am i|shall)\b", re.I)

STAT_RE = re.compile(
    r"(\b\d{1,3}(?:,\d{3})+\b|\b\d+(?:\.\d+)?\s*%|[$£€]\s?\d|\b\d+(?:\.\d+)?\s*"
    r"(?:gsm|oz|cm|mm|inch(?:es)?|kg|lbs?|years?|months?|weeks?|days?|hours?|"
    r"minutes?|degrees?|°[CF]?)\b|\b(?:19|20)\d{2}\b)", re.I)

CITATION_RE = re.compile(
    r"\b(according to|research (?:by|from|shows)|study (?:by|from|found)|"
    r"reported by|data from|survey (?:by|of)|source:|per the)\b", re.I)

AUTHORITY_DOMAINS = re.compile(
    r"\.(gov|edu|ac\.uk)(/|$)|wikipedia\.org|\.who\.int|nih\.gov|iso\.org|"
    r"statista\.com|pubmed|nature\.com|sciencedirect|jstor\.org|reuters\.com|"
    r"bbc\.co(?:m|\.uk)|nytimes\.com|forbes\.com|hbr\.org", re.I)

SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])")

CATEGORIES = [
    ("crawl", "AI Crawler Access"),
    ("schema", "Structured Data"),
    ("answer", "Answer Structure"),
    ("content", "Content Depth"),
    ("authority", "Authority / E-E-A-T"),
    ("tech", "Technical / Meta"),
    ("aifiles", "AI Files"),
]
CATEGORY_LABELS = dict(CATEGORIES)


# ──────────────────────────────────────────────────────────────────────────
#  Fetching
# ──────────────────────────────────────────────────────────────────────────

class Fetched:
    def __init__(self, url, status, headers, body, final_url, error=None, elapsed=0.0):
        self.url = url
        self.status = status
        self.headers = headers or {}
        self.body = body or ""
        self.final_url = final_url or url
        self.error = error
        self.elapsed = elapsed

    @property
    def ok(self):
        return self.status is not None and 200 <= self.status < 300


def fetch(url, ua=BROWSER_UA, timeout=25, max_bytes=4_000_000):
    """GET a URL. Never raises — failures come back on Fetched.error."""
    req = urllib.request.Request(url, headers={
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Encoding": "gzip",
        "Accept-Language": "en-US,en;q=0.9",
    })
    start = datetime.now()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(max_bytes)
            if resp.headers.get("Content-Encoding", "").lower() == "gzip":
                try:
                    raw = gzip.decompress(raw)
                except OSError:
                    pass
            charset = resp.headers.get_content_charset() or "utf-8"
            body = raw.decode(charset, errors="replace")
            elapsed = (datetime.now() - start).total_seconds()
            return Fetched(url, resp.status, dict(resp.headers), body,
                           resp.geturl(), elapsed=elapsed)
    except urllib.error.HTTPError as e:
        try:
            body = e.read(max_bytes).decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return Fetched(url, e.code, dict(e.headers or {}), body, url,
                       error=f"HTTP {e.code}")
    except Exception as e:
        return Fetched(url, None, {}, "", url, error=f"{type(e).__name__}: {e}")


def head_or_get(url, timeout=15):
    """Cheap existence probe for llms.txt / sitemap.xml."""
    return fetch(url, timeout=timeout, max_bytes=400_000)


# ──────────────────────────────────────────────────────────────────────────
#  HTML parsing
# ──────────────────────────────────────────────────────────────────────────

class Page(HTMLParser):
    """Pulls out everything the checks need, in document order."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.html_lang = ""
        self.metas = []          # list of dicts of the raw attributes
        self.link_tags = []      # <link> attributes
        self.anchors = []        # (href, text)
        self.headings = []       # (level, text)
        self.images = []         # dicts with src/alt
        self.jsonld_raw = []
        self.blocks = []         # ('h',lvl,text) | ('p',text) | ('li',text) | ('table',None)
        self.table_count = 0
        self.li_count = 0
        self.has_main = False

        self._skip_depth = 0
        self._in_title = False
        self._in_jsonld = False
        self._jsonld_buf = []
        self._cur = None         # ('p'|'li'|'h', level)
        self._buf = []
        self._anchor_buf = None
        self._anchor_href = None
        self._main_depth = 0
        self._main_chars = 0
        self._text = []

    # -- helpers ---------------------------------------------------------
    def _flush(self):
        if self._cur is None:
            return
        kind, lvl = self._cur
        text = normalize_ws("".join(self._buf))
        self._cur, self._buf = None, []
        if not text:
            return
        if kind == "h":
            self.headings.append((lvl, text))
            self.blocks.append(("h", lvl, text))
        else:
            self.blocks.append((kind, 0, text))

    def _open_block(self, kind, lvl=0):
        self._flush()
        self._cur = (kind, lvl)
        self._buf = []

    # -- HTMLParser API --------------------------------------------------
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in SKIP_TEXT_TAGS:
            if tag == "script" and a.get("type", "").strip().lower() == "application/ld+json":
                self._in_jsonld = True
                self._jsonld_buf = []
            self._skip_depth += 1
            return
        if self._skip_depth:
            return

        if tag == "html":
            self.html_lang = a.get("lang", "")
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            self.metas.append(a)
        elif tag == "link":
            self.link_tags.append(a)
        elif tag == "img":
            self.images.append(a)
        elif tag in ("main", "article"):
            self.has_main = True
            self._main_depth += 1
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._open_block("h", int(tag[1]))
        elif tag == "p":
            self._open_block("p")
        elif tag == "li":
            self.li_count += 1
            self._open_block("li")
        elif tag == "table":
            self.table_count += 1
            self.blocks.append(("table", 0, ""))
        elif tag == "a":
            self._anchor_href = a.get("href", "")
            self._anchor_buf = []
        elif tag == "br":
            if self._cur:
                self._buf.append(" ")

    def handle_startendtag(self, tag, attrs):
        if self._skip_depth:
            return
        a = dict(attrs)
        if tag == "meta":
            self.metas.append(a)
        elif tag == "link":
            self.link_tags.append(a)
        elif tag == "img":
            self.images.append(a)

    def handle_endtag(self, tag):
        if tag in SKIP_TEXT_TAGS:
            if tag == "script" and self._in_jsonld:
                self.jsonld_raw.append("".join(self._jsonld_buf))
                self._in_jsonld = False
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return

        if tag == "title":
            self._in_title = False
        elif tag in ("main", "article"):
            self._main_depth = max(0, self._main_depth - 1)
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6", "p", "li"):
            self._flush()
        elif tag == "a":
            if self._anchor_buf is not None:
                self.anchors.append((self._anchor_href or "",
                                     normalize_ws("".join(self._anchor_buf))))
            self._anchor_buf, self._anchor_href = None, None

    def handle_data(self, data):
        if self._in_jsonld:
            self._jsonld_buf.append(data)
            return
        if self._skip_depth:
            return
        if self._in_title:
            self.title += data
            return
        self._text.append(data)
        if self._main_depth:
            self._main_chars += len(data.strip())
        if self._cur is not None:
            self._buf.append(data)
        if self._anchor_buf is not None:
            self._anchor_buf.append(data)

    def close(self):
        super().close()
        self._flush()
        self.title = normalize_ws(self.title)

    # -- derived ---------------------------------------------------------
    @property
    def text(self):
        return normalize_ws(" ".join(self._text))

    def meta(self, name):
        name = name.lower()
        for m in self.metas:
            key = (m.get("name") or m.get("property") or m.get("http-equiv") or "").lower()
            if key == name:
                return (m.get("content") or "").strip()
        return ""

    def link_rel(self, rel):
        rel = rel.lower()
        for l in self.link_tags:
            rels = (l.get("rel") or "")
            if isinstance(rels, str) and rel in rels.lower().split():
                return (l.get("href") or "").strip()
        return ""

    def body_text(self):
        """Prose from paragraphs/list items — closer to what an LLM reads."""
        parts = [t for k, _, t in self.blocks if k in ("p", "li")]
        return normalize_ws(" ".join(parts))


def normalize_ws(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


def parse_html(html):
    p = Page()
    try:
        p.feed(html)
    except Exception:
        pass
    p.close()
    return p


def words(text):
    return re.findall(r"[A-Za-z0-9''’-]+", text or "")


def sentences(text):
    text = normalize_ws(text)
    return [s for s in SENT_SPLIT_RE.split(text) if s.strip()]


# ──────────────────────────────────────────────────────────────────────────
#  JSON-LD
# ──────────────────────────────────────────────────────────────────────────

def parse_jsonld(raw_blocks):
    """Return (nodes, n_valid, n_invalid). Flattens @graph and arrays."""
    nodes, valid, invalid = [], 0, 0
    for raw in raw_blocks:
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
            valid += 1
        except Exception:
            # tolerate trailing commas / stray control chars before giving up
            cleaned = re.sub(r",\s*([}\]])", r"\1", raw)
            try:
                data = json.loads(cleaned)
                valid += 1
            except Exception:
                invalid += 1
                continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                nodes.append(node)
                if "@graph" in node:
                    stack.append(node["@graph"])
                for v in node.values():
                    if isinstance(v, (dict, list)):
                        stack.append(v)
    return nodes, valid, invalid


def node_types(node):
    t = node.get("@type", "")
    if isinstance(t, list):
        return [str(x).lower() for x in t]
    return [str(t).lower()] if t else []


def has_type(nodes, *types):
    wanted = {t.lower() for t in types}
    return [n for n in nodes if wanted & set(node_types(n))]


def first_value(node, *keys):
    for k in keys:
        v = node.get(k)
        if isinstance(v, dict):
            v = v.get("name") or v.get("@id") or v.get("url")
        if isinstance(v, list) and v:
            v = v[0]
            if isinstance(v, dict):
                v = v.get("name") or v.get("@id") or v.get("url")
        if v:
            return str(v)
    return ""


# ──────────────────────────────────────────────────────────────────────────
#  robots.txt
# ──────────────────────────────────────────────────────────────────────────

def parse_robots(text):
    """-> {agent_lower: {'allow': [...], 'disallow': [...]}} plus sitemaps."""
    groups, sitemaps = {}, []
    current = []
    last_was_agent = False
    for line in (text or "").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, _, value = line.partition(":")
        field, value = field.strip().lower(), value.strip()
        if field == "user-agent":
            if not last_was_agent:
                current = []
            current.append(value.lower())
            groups.setdefault(value.lower(), {"allow": [], "disallow": []})
            last_was_agent = True
        elif field == "sitemap":
            sitemaps.append(value)
            last_was_agent = False
        elif field in ("allow", "disallow"):
            last_was_agent = False
            for ag in current:
                groups.setdefault(ag, {"allow": [], "disallow": []})[field].append(value)
    return groups, sitemaps


def robots_allows(groups, agent, path="/"):
    """Google-style longest-match. Returns (allowed: bool, matched_group: str)."""
    agent = agent.lower()
    group = None
    if agent in groups:
        group = groups[agent]
        matched = agent
    else:
        for name in groups:
            if name != "*" and name and agent.startswith(name):
                group, matched = groups[name], name
                break
    if group is None:
        if "*" not in groups:
            return True, "(no rule)"
        group, matched = groups["*"], "*"

    best_len, best_allow = -1, True
    for kind in ("allow", "disallow"):
        for rule in group[kind]:
            if rule == "":
                continue
            pattern = rule.rstrip("$")
            if path.startswith(pattern.replace("*", "")) or pattern == "/":
                if len(rule) > best_len:
                    best_len, best_allow = len(rule), (kind == "allow")
    return best_allow, matched


# ──────────────────────────────────────────────────────────────────────────
#  Check results
# ──────────────────────────────────────────────────────────────────────────

class Result:
    __slots__ = ("id", "category", "weight", "score", "title", "detail", "fix", "skipped")

    def __init__(self, id, category, weight, score, title, detail, fix="", skipped=False):
        self.id = id
        self.category = category
        self.weight = weight
        self.score = max(0.0, min(1.0, float(score)))
        self.title = title
        self.detail = detail
        self.fix = fix
        self.skipped = skipped

    @property
    def status(self):
        if self.skipped:
            return "skip"
        if self.score >= 0.999:
            return "pass"
        if self.score <= 0.0:
            return "fail"
        return "warn"

    def as_dict(self):
        return {"id": self.id, "category": self.category,
                "category_label": (CATEGORY_LABELS.get(self.category)
                                   or AGENT_CATEGORY_LABELS.get(self.category)
                                   or BRAND_CATEGORY_LABELS.get(self.category)
                                   or self.category),
                "weight": self.weight, "score": round(self.score, 3),
                "status": self.status, "title": self.title,
                "detail": self.detail, "fix": self.fix}


class Context:
    """Everything the checks look at."""

    def __init__(self, url, page, html, fetched=None, offline=False):
        self.url = url
        self.page = page
        self.html = html
        self.fetched = fetched
        self.offline = offline
        parts = urllib.parse.urlsplit(url)
        self.scheme = parts.scheme or "https"
        self.host = parts.netloc
        self.path = parts.path or "/"
        self.origin = f"{self.scheme}://{self.host}" if self.host else ""
        self.nodes, self.jsonld_valid, self.jsonld_invalid = parse_jsonld(page.jsonld_raw)
        self.robots_text = None
        self.robots_groups = {}
        self.robots_sitemaps = []
        self.robots_status = None
        self.gptbot_status = None
        self.llms_txt = None
        self.llms_full = None
        self.sitemap_ok = None
        self.network_ok = True
        self.agent_available = False
        self.agent_probes = {}
        self.markdown_negotiated = False
        self.markdown_alternate = False
        self.dns_aid = None
        self.markdown_claimed = False

    def load_network_extras(self, timeout=15):
        """robots.txt, llms.txt, sitemap.xml, and a GPTBot-UA fetch of the page."""
        if not self.origin:
            return
        jobs = {
            "robots": f"{self.origin}/robots.txt",
            "llms": f"{self.origin}/llms.txt",
            "llms_full": f"{self.origin}/llms-full.txt",
            "sitemap": f"{self.origin}/sitemap.xml",
        }
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
            futs = {k: ex.submit(head_or_get, u, timeout) for k, u in jobs.items()}
            futs["gptbot"] = ex.submit(fetch, self.url, GPTBOT_UA, timeout, 200_000)
            out = {k: f.result() for k, f in futs.items()}

        r = out["robots"]
        self.robots_status = r.status
        if r.ok and "<html" not in r.body[:400].lower():
            self.robots_text = r.body
            self.robots_groups, self.robots_sitemaps = parse_robots(r.body)

        def txt_ok(f):
            return f.ok and "<html" not in f.body[:400].lower() and len(f.body.strip()) > 20

        self.llms_txt = out["llms"].body if txt_ok(out["llms"]) else None
        self.llms_full = out["llms_full"].body if txt_ok(out["llms_full"]) else None
        sm = out["sitemap"]
        self.sitemap_ok = bool(sm.ok and ("<urlset" in sm.body or "<sitemapindex" in sm.body))
        self.gptbot_status = out["gptbot"].status
        # If every probe died without an HTTP status, the network is unreachable —
        # the network checks below would otherwise read as genuine site failures.
        self.network_ok = any(f.status is not None for f in out.values())


# ──────────────────────────────────────────────────────────────────────────
#  CATEGORY 1 — AI crawler access (15)
# ──────────────────────────────────────────────────────────────────────────

def skip_note(ctx):
    return ("Skipped in offline mode." if ctx.offline
            else "Skipped — no network route to the site from this machine.")


def check_crawl(ctx):
    out = []
    net = not ctx.offline and ctx.network_ok

    # 1. robots.txt reachable
    if net:
        found = ctx.robots_text is not None
        out.append(Result("robots_exists", "crawl", 2, 1.0 if found else 0.0,
                          "robots.txt is reachable",
                          f"{ctx.origin}/robots.txt returned {ctx.robots_status}"
                          if found else
                          f"No parseable robots.txt (status {ctx.robots_status}). "
                          "Crawlers assume allow-all, but you lose explicit control.",
                          "Publish a robots.txt that explicitly allows AI crawlers."))
    else:
        out.append(Result("robots_exists", "crawl", 2, 0, "robots.txt is reachable",
                          skip_note(ctx), skipped=True))

    # 2. AI crawlers allowed
    if net and ctx.robots_text is not None:
        blocked, allowed = [], []
        for agent, label, _important in AI_CRAWLERS:
            ok, _ = robots_allows(ctx.robots_groups, agent, ctx.path)
            (allowed if ok else blocked).append(f"{agent} ({label})")
        critical_blocked = [b for b in blocked
                            if any(b.startswith(a) for a, _, imp in AI_CRAWLERS if imp)]
        if not blocked:
            score, detail = 1.0, f"All {len(AI_CRAWLERS)} AI crawlers may fetch {ctx.path}"
        elif critical_blocked:
            score = max(0.0, 1 - 0.34 * len(critical_blocked))
            detail = "BLOCKED: " + "; ".join(blocked)
        else:
            score = 0.75
            detail = "Minor crawlers blocked: " + "; ".join(blocked)
        out.append(Result("robots_ai_allowed", "crawl", 5, score,
                          "AI crawlers allowed in robots.txt", detail,
                          "Add explicit `User-agent: GPTBot / OAI-SearchBot / ClaudeBot / "
                          "PerplexityBot / Google-Extended` groups with `Allow: /`."))
    elif net:
        out.append(Result("robots_ai_allowed", "crawl", 5, 0.7,
                          "AI crawlers allowed in robots.txt",
                          "No robots.txt found — crawlers default to allowed, but nothing is explicit.",
                          "Publish robots.txt with explicit Allow rules for AI crawlers."))
    else:
        out.append(Result("robots_ai_allowed", "crawl", 5, 0,
                          "AI crawlers allowed in robots.txt",
                          skip_note(ctx), skipped=True))

    # 3. server does not block the GPTBot UA
    if net:
        gs = ctx.gptbot_status
        if gs is None:
            score, detail = 0.5, "Could not fetch the page as GPTBot (network error)."
        elif gs in (403, 401, 429):
            score, detail = 0.0, f"Server returned {gs} to the GPTBot user-agent — WAF/bot rule is blocking it."
        elif 200 <= gs < 300:
            score, detail = 1.0, f"Server serves the page to GPTBot (HTTP {gs})."
        else:
            score, detail = 0.4, f"GPTBot user-agent got HTTP {gs}."
        out.append(Result("server_ai_ua", "crawl", 3, score,
                          "Server serves AI user-agents", detail,
                          "Allowlist AI crawler user-agents in Cloudflare / WAF / bot-fight rules."))
    else:
        out.append(Result("server_ai_ua", "crawl", 3, 0, "Server serves AI user-agents",
                          skip_note(ctx), skipped=True))

    # 4. meta robots
    mr = (ctx.page.meta("robots") + " " + ctx.page.meta("googlebot")).lower()
    bad = [d for d in ("noindex", "none", "nosnippet", "noarchive", "max-snippet:0") if d in mr]
    if "noindex" in mr or "none" in mr:
        score, detail = 0.0, f"Page is set to `{mr.strip()}` — it will never be cited."
    elif bad:
        score, detail = 0.3, f"Snippet-limiting directives present: {', '.join(bad)}"
    else:
        score, detail = 1.0, f"No blocking robots directives ({mr.strip() or 'none set'})."
    out.append(Result("meta_robots", "crawl", 3, score, "No blocking meta robots directives",
                      detail, "Remove noindex/nosnippet/noarchive; use `max-snippet:-1, "
                              "max-image-preview:large` to let engines quote you fully."))

    # 5. no AI opt-out meta
    optout = [m for m in ctx.page.metas
              if (m.get("name") or "").lower() in ("noai", "noimageai", "ai-content-declaration")
              or "noai" in (m.get("content") or "").lower()]
    out.append(Result("noai_meta", "crawl", 2, 0.0 if optout else 1.0,
                      "No AI opt-out signals",
                      "AI opt-out meta tag present — remove it." if optout
                      else "No noai/noimageai opt-out tags.",
                      "Delete <meta name=\"noai\"> style tags."))
    return out


# ──────────────────────────────────────────────────────────────────────────
#  CATEGORY 2 — Structured data (20)
# ──────────────────────────────────────────────────────────────────────────

def check_schema(ctx):
    out, nodes = [], ctx.nodes
    n_blocks = len(ctx.page.jsonld_raw)

    out.append(Result("jsonld_present", "schema", 4, 1.0 if nodes else 0.0,
                      "JSON-LD structured data present",
                      f"{n_blocks} JSON-LD block(s), {len(nodes)} node(s)." if nodes
                      else "No JSON-LD found. Answer engines rely on it to identify entities.",
                      "Add schema.org JSON-LD for the page's primary type."))

    if n_blocks:
        score = 1.0 if ctx.jsonld_invalid == 0 else max(0.0, 1 - ctx.jsonld_invalid / n_blocks)
        detail = ("All JSON-LD blocks parse as valid JSON."
                  if ctx.jsonld_invalid == 0
                  else f"{ctx.jsonld_invalid} of {n_blocks} JSON-LD block(s) failed to parse — "
                       "engines silently drop these.")
    else:
        score, detail = 0.0, "Nothing to validate."
    out.append(Result("jsonld_valid", "schema", 3, score, "JSON-LD parses cleanly", detail,
                      "Validate at validator.schema.org; watch for unescaped quotes in "
                      "titles injected into JSON."))

    articles = has_type(nodes, "article", "blogposting", "newsarticle", "techarticle")
    products = has_type(nodes, "product")
    others = has_type(nodes, "webpage", "collectionpage", "itemlist", "localbusiness", "service")
    primary = articles or products or others
    out.append(Result("primary_schema", "schema", 3, 1.0 if primary else 0.0,
                      "Primary content type is typed",
                      (f"Found: {', '.join(sorted({t for n in primary for t in node_types(n)}))}"
                       if primary else
                       "No Article/BlogPosting/Product/WebPage node — the engine cannot tell "
                       "what this page is."),
                      "Add @type Article or BlogPosting for editorial pages, Product for PDPs."))

    orgs = has_type(nodes, "organization", "localbusiness", "onlinestore", "corporation")
    out.append(Result("org_schema", "schema", 2, 1.0 if orgs else 0.0,
                      "Organization / publisher entity declared",
                      f"Organization node present ({first_value(orgs[0], 'name') or 'unnamed'})."
                      if orgs else "No Organization node — brand is not established as an entity.",
                      "Add an Organization node with name, url, logo and sameAs profiles."))

    faqs = has_type(nodes, "faqpage", "qapage")
    howto = has_type(nodes, "howto")
    q_headings = [t for lvl, t in ctx.page.headings if is_question(t)]
    if faqs or howto:
        qs = []
        for f in faqs:
            me = f.get("mainEntity") or []
            qs.extend(me if isinstance(me, list) else [me])
        answered = [q for q in qs if isinstance(q, dict) and
                    (q.get("acceptedAnswer") or q.get("suggestedAnswer"))]
        if faqs and qs and len(answered) < len(qs):
            score = 0.5
            detail = f"FAQPage present but {len(qs) - len(answered)} question(s) lack acceptedAnswer."
        else:
            score = 1.0
            detail = (f"FAQPage with {len(qs)} Q&A pair(s)." if faqs else "HowTo schema present.")
    elif len(q_headings) >= 2:
        score = 0.0
        detail = (f"{len(q_headings)} question-style headings on the page but no FAQPage schema — "
                  "this is the single highest-leverage AEO fix here.")
    else:
        score = 0.25
        detail = "No FAQPage/HowTo schema and no question headings to mark up."
    out.append(Result("faq_schema", "schema", 3, score, "FAQPage / HowTo schema",
                      detail,
                      "Wrap Q&A sections in FAQPage JSON-LD with mainEntity → Question → "
                      "acceptedAnswer.text; AI engines lift these verbatim."))

    crumbs = has_type(nodes, "breadcrumblist")
    out.append(Result("breadcrumb_schema", "schema", 1, 1.0 if crumbs else 0.0,
                      "BreadcrumbList schema", "Present." if crumbs else "Missing.",
                      "Add BreadcrumbList so engines understand site hierarchy."))

    author = ""
    for n in (articles or nodes):
        author = first_value(n, "author", "creator")
        if author:
            break
    out.append(Result("schema_author", "schema", 2, 1.0 if author else 0.0,
                      "Author declared in schema",
                      f"author = {author}" if author
                      else "No author/creator in schema — weakens E-E-A-T attribution.",
                      "Set author to a Person (preferred) or Organization node with a url."))

    pub = mod = ""
    for n in (articles or nodes):
        pub = pub or first_value(n, "datePublished", "dateCreated")
        mod = mod or first_value(n, "dateModified")
    if pub and mod:
        score, detail = 1.0, f"datePublished={pub[:10]}, dateModified={mod[:10]}"
    elif pub or mod:
        score, detail = 0.5, f"Only one date present ({(pub or mod)[:10]})."
    else:
        score, detail = 0.0, "No datePublished/dateModified — engines discount undated content."
    out.append(Result("schema_dates", "schema", 2, score, "Publish & modified dates in schema",
                      detail, "Emit ISO-8601 datePublished and dateModified on every article."))
    return out


def is_question(text):
    t = (text or "").strip()
    return t.endswith("?") or bool(QUESTION_RE.match(t))


# ──────────────────────────────────────────────────────────────────────────
#  CATEGORY 3 — Answer structure (20)
# ──────────────────────────────────────────────────────────────────────────

def check_answer(ctx):
    out = []
    page = ctx.page
    h1s = [t for lvl, t in page.headings if lvl == 1]
    subs = [(lvl, t) for lvl, t in page.headings if lvl in (2, 3, 4)]

    if len(h1s) == 1:
        score, detail = 1.0, f"Single H1: “{trunc(h1s[0], 70)}”"
    elif not h1s:
        score, detail = 0.0, "No H1 on the page."
    else:
        score, detail = 0.4, f"{len(h1s)} H1 tags — ambiguous topic signal."
    out.append(Result("h1_single", "answer", 3, score, "Exactly one H1", detail,
                      "Use one H1 stating the page's topic in natural language."))

    levels = [lvl for lvl, _ in page.headings]
    jumps = sum(1 for a, b in zip(levels, levels[1:]) if b - a > 1)
    if len(page.headings) < 2:
        score, detail = 0.0, "Fewer than 2 headings — no scannable structure for extraction."
    elif jumps == 0:
        score, detail = 1.0, f"{len(page.headings)} headings, no skipped levels."
    else:
        score, detail = max(0.0, 1 - jumps * 0.25), f"{jumps} skipped heading level(s)."
    out.append(Result("heading_hierarchy", "answer", 2, score, "Clean heading hierarchy", detail,
                      "Keep H1 → H2 → H3 sequential; never jump levels."))

    q_subs = [t for _, t in subs if is_question(t)]
    ratio = len(q_subs) / len(subs) if subs else 0
    if len(q_subs) >= 3 or ratio >= 0.4:
        score = 1.0
    elif q_subs:
        score = 0.5 + min(0.4, ratio)
    else:
        score = 0.0
    out.append(Result("question_headings", "answer", 3, score,
                      "Headings phrased as questions",
                      f"{len(q_subs)} of {len(subs)} sub-headings are questions."
                      + (f" e.g. “{trunc(q_subs[0], 60)}”" if q_subs else
                         " AI engines match user prompts against question headings."),
                      "Rewrite section headings as the questions buyers actually type "
                      "(\"What is X?\", \"How do I Y?\")."))

    # direct answer immediately after each heading
    scored, good = 0, 0
    for i, (kind, lvl, text) in enumerate(page.blocks):
        if kind != "h" or lvl not in (2, 3):
            continue
        nxt = next((b for b in page.blocks[i + 1:i + 3] if b[0] in ("p", "li", "table")), None)
        if not nxt:
            scored += 1
            continue
        scored += 1
        if nxt[0] in ("li", "table"):
            good += 0.8
            continue
        first = sentences(nxt[2])[0] if sentences(nxt[2]) else nxt[2]
        n = len(words(first))
        if 4 <= n <= 35:
            good += 1
        elif n <= 55:
            good += 0.5
    if scored == 0:
        score, detail = 0.0, "No H2/H3 sections to evaluate."
    else:
        score = good / scored
        detail = (f"{score * 100:.0f}% of {scored} H2/H3 sections open with a self-contained "
                  f"answer (≤35 words) instead of a wind-up.")
    out.append(Result("direct_answers", "answer", 4, score, "Sections answer immediately", detail,
                      "Put a 25-40 word standalone answer directly under each heading, "
                      "then expand. That block is what gets quoted."))

    head_text = " ".join(t for k, _, t in page.blocks[:8] if k in ("p", "li")).lower()
    first_para = next((t for k, _, t in page.blocks if k == "p" and len(words(t)) > 12), "")
    has_tldr = bool(re.search(r"\b(tl;?dr|in short|quick answer|key takeaways?|summary|"
                              r"the short answer|at a glance|bottom line)\b", head_text))
    if has_tldr:
        score, detail = 1.0, "Summary / TL;DR block near the top."
    elif first_para and len(words(sentences(first_para)[0])) <= 40:
        score, detail = 0.6, "Opens with a tight first sentence but no explicit summary block."
    else:
        score, detail = 0.0, "No TL;DR / key-takeaways block in the opening."
    out.append(Result("tldr_block", "answer", 2, score, "Summary / TL;DR up top", detail,
                      "Add a 2-3 sentence \"Quick answer\" or \"Key takeaways\" list "
                      "immediately after the H1."))

    li = page.li_count
    score = 1.0 if li >= 8 else (0.6 if li >= 3 else 0.0)
    out.append(Result("lists_used", "answer", 2, score, "Lists used for enumerable facts",
                      f"{li} list items.", "Convert enumerable content to <ul>/<ol> — "
                                           "extraction-friendly and quoted more often."))

    tables = page.table_count
    score = 1.0 if tables >= 1 else 0.0
    out.append(Result("tables_used", "answer", 1, score, "Comparison table present",
                      f"{tables} table(s)." if tables else
                      "No tables. Comparison tables are disproportionately cited.",
                      "Add a comparison/spec table where the content compares options."))

    paras = [t for k, _, t in page.blocks if k == "p" and len(words(t)) > 5]
    if paras:
        long_paras = sum(1 for p in paras if len(words(p)) > 120)
        frac = long_paras / len(paras)
        score = 1.0 if frac == 0 else max(0.0, 1 - frac * 2.5)
        detail = f"{long_paras}/{len(paras)} paragraphs exceed 120 words."
    else:
        score, detail = 0.0, "No paragraphs found."
    out.append(Result("para_length", "answer", 2, score, "Paragraphs are chunk-sized", detail,
                      "Keep paragraphs under ~80 words so each is a clean retrieval chunk."))

    body = page.body_text()
    sents = sentences(body)
    if sents:
        avg = sum(len(words(s)) for s in sents) / len(sents)
        score = 1.0 if avg <= 22 else (0.6 if avg <= 28 else 0.2)
        detail = f"Average sentence length {avg:.1f} words."
    else:
        score, detail = 0.0, "No sentences parsed."
    out.append(Result("sentence_length", "answer", 1, score, "Readable sentence length", detail,
                      "Target 15-20 word average; long sentences dilute extractable claims."))
    return out


# ──────────────────────────────────────────────────────────────────────────
#  CATEGORY 4 — Content depth (15)
# ──────────────────────────────────────────────────────────────────────────

def check_content(ctx):
    out = []
    page = ctx.page
    body = page.body_text()
    wc = len(words(body))

    if wc >= 900:
        score = 1.0
    elif wc >= 600:
        score = 0.8
    elif wc >= 350:
        score = 0.5
    elif wc >= 150:
        score = 0.25
    else:
        score = 0.0
    out.append(Result("word_count", "content", 4, score, "Sufficient content depth",
                      f"{wc} words of body prose.",
                      "Aim for 900+ words of substantive, non-boilerplate content on "
                      "pages you want cited."))

    # freshness
    date_str = ""
    for n in ctx.nodes:
        date_str = first_value(n, "dateModified") or first_value(n, "datePublished")
        if date_str:
            break
    date_str = date_str or page.meta("article:modified_time") or page.meta("article:published_time")
    age_days = parse_age_days(date_str)
    if age_days is None:
        score, detail = 0.0, "No machine-readable date found on the page."
    elif age_days <= 180:
        score, detail = 1.0, f"Last dated {int(age_days)} days ago ({date_str[:10]})."
    elif age_days <= 365:
        score, detail = 0.7, f"{int(age_days)} days old ({date_str[:10]})."
    elif age_days <= 730:
        score, detail = 0.35, f"{int(age_days)} days old — engines favour fresher sources."
    else:
        score, detail = 0.0, f"{int(age_days)} days old ({date_str[:10]}) — stale."
    out.append(Result("freshness", "content", 3, score, "Content is fresh", detail,
                      "Refresh and bump dateModified at least every 6 months; include the "
                      "current year in the copy where it's genuinely relevant."))

    stats = STAT_RE.findall(body)
    per_1k = len(stats) / max(1, wc / 1000)
    if per_1k >= 8:
        score = 1.0
    elif per_1k >= 4:
        score = 0.7
    elif per_1k >= 1:
        score = 0.4
    else:
        score = 0.0
    out.append(Result("stat_density", "content", 3, score, "Concrete facts, numbers, specifics",
                      f"{len(stats)} numeric/specific facts ({per_1k:.1f} per 1,000 words).",
                      "Add measurable specifics (weights, prices, timings, percentages) — "
                      "LLMs preferentially cite quantified claims."))

    ext = [(h, t) for h, t in page.anchors if h.startswith("http") and ctx.host not in h]
    auth = [h for h, _ in ext if AUTHORITY_DOMAINS.search(h)]
    cites = CITATION_RE.findall(body)
    if auth or len(cites) >= 2:
        score = 1.0
    elif ext or cites:
        score = 0.5
    else:
        score = 0.0
    out.append(Result("citations", "content", 2, score, "Cites external sources",
                      f"{len(ext)} outbound links ({len(auth)} to authority domains), "
                      f"{len(cites)} in-text attributions.",
                      "Cite 2-3 authoritative external sources; corroborated pages are "
                      "reused more readily by answer engines."))

    internal = [h for h, _ in page.anchors
                if h.startswith("/") or (ctx.host and ctx.host in h)]
    uniq = len({h.split("?")[0].rstrip("/") for h in internal})
    if uniq >= 8:
        score = 1.0
    elif uniq >= 4:
        score = 0.7
    elif uniq >= 1:
        score = 0.35
    else:
        score = 0.0
    out.append(Result("internal_links", "content", 3, score, "Internal linking / topical cluster",
                      f"{uniq} unique internal links.",
                      "Link to 8+ related pages with descriptive anchor text to build a "
                      "topical cluster the engines can traverse."))
    return out


def parse_age_days(s):
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(s[:len(datetime.now().strftime(fmt)) + 6][:32], fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - dt).days
        except ValueError:
            continue
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            dt = datetime(int(m[1]), int(m[2]), int(m[3]), tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - dt).days
        except ValueError:
            return None
    return None


# ──────────────────────────────────────────────────────────────────────────
#  CATEGORY 5 — Authority / E-E-A-T (12)
# ──────────────────────────────────────────────────────────────────────────

def check_authority(ctx):
    out = []
    page, text = ctx.page, ctx.page.text
    nodes = ctx.nodes

    byline = bool(re.search(r"\b(by|written by|author(?:ed by)?|reviewed by)\s+[A-Z][a-z]+\s+[A-Z]",
                            text)) or bool(page.meta("author"))
    schema_person = bool([n for n in nodes if "person" in node_types(n)])
    if byline and schema_person:
        score, detail = 1.0, "Visible byline and a Person entity in schema."
    elif byline or schema_person:
        score, detail = 0.6, "Author signalled, but not both on-page and in schema."
    else:
        score, detail = 0.0, "No author byline — anonymous content is discounted for E-E-A-T."
    out.append(Result("author_byline", "authority", 3, score, "Named author / byline", detail,
                      "Add a real named author with a byline and a matching Person node "
                      "(name, jobTitle, url)."))

    bio = any(re.search(r"/(author|authors|team|about-us|about)/", h, re.I) for h, _ in page.anchors)
    out.append(Result("author_bio_link", "authority", 2, 1.0 if bio else 0.0,
                      "Author/team bio is linkable",
                      "Links to an author or team page." if bio
                      else "No author/team page linked — nothing for the engine to resolve "
                           "the author entity against.",
                      "Publish author pages with credentials and link every article to them."))

    sameas = []
    for n in nodes:
        v = n.get("sameAs")
        if isinstance(v, str):
            sameas.append(v)
        elif isinstance(v, list):
            sameas.extend(str(x) for x in v)
    if len(sameas) >= 3:
        score, detail = 1.0, f"{len(sameas)} sameAs profiles declared."
    elif sameas:
        score, detail = 0.5, f"Only {len(sameas)} sameAs link(s)."
    else:
        score, detail = 0.0, "No sameAs — the brand is not linked to any known entity."
    out.append(Result("org_sameas", "authority", 2, score, "Entity disambiguation (sameAs)", detail,
                      "Add sameAs to your Instagram, LinkedIn, Facebook, X, Wikidata and "
                      "Google Business Profile."))

    nav = " ".join(h.lower() for h, _ in page.anchors)
    has_about = "/about" in nav or "/pages/about" in nav
    has_contact = "/contact" in nav or "/pages/contact" in nav
    score = 1.0 if (has_about and has_contact) else (0.5 if (has_about or has_contact) else 0.0)
    out.append(Result("about_contact", "authority", 2, score, "About & Contact reachable",
                      f"about={has_about}, contact={has_contact}",
                      "Link About and Contact from every page — a baseline trust signal "
                      "engines check."))

    signals = {
        "experience claim": r"\b(we (?:have|'ve)|our team|years of experience|since (?:19|20)\d{2}|"
                            r"in our experience|we tested|hand[- ]?made by)\b",
        "reviews/ratings": r"\b(reviews?|rated|rating|testimonial|customers? say|★|stars)\b",
        "guarantee/policy": r"\b(guarantee|warranty|returns? policy|refund|free (?:shipping|returns))\b",
        "credential": r"\b(certified|accredited|award|featured in|as seen in|member of)\b",
    }
    hits = [k for k, pat in signals.items() if re.search(pat, text, re.I)]
    agg = has_type(nodes, "aggregaterating", "review")
    score = min(1.0, (len(hits) / 3) + (0.25 if agg else 0))
    out.append(Result("eeat_signals", "authority", 3, score, "Experience & trust signals",
                      f"Found: {', '.join(hits) or 'none'}"
                      + (" + AggregateRating schema" if agg else ""),
                      "Surface first-hand experience, review counts, guarantees and "
                      "credentials in the copy — these are what LLMs paraphrase as trust."))
    return out


# ──────────────────────────────────────────────────────────────────────────
#  CATEGORY 6 — Technical / meta (10)
# ──────────────────────────────────────────────────────────────────────────

def check_tech(ctx):
    out = []
    page = ctx.page
    t = page.title
    n = len(t)
    if 30 <= n <= 65:
        score, detail = 1.0, f"{n} chars: “{trunc(t, 70)}”"
    elif t and 15 <= n <= 80:
        score, detail = 0.6, f"{n} chars (target 30-65): “{trunc(t, 70)}”"
    elif t:
        score, detail = 0.3, f"{n} chars — too {'short' if n < 15 else 'long'}."
    else:
        score, detail = 0.0, "No <title>."
    out.append(Result("title_tag", "tech", 2, score, "Title tag length & clarity", detail,
                      "Write a 30-65 char title that states the answer, not a slogan."))

    d = page.meta("description")
    n = len(d)
    if 70 <= n <= 165:
        score, detail = 1.0, f"{n} chars."
    elif d:
        score, detail = 0.5, f"{n} chars (target 70-165)."
    else:
        score, detail = 0.0, "No meta description."
    out.append(Result("meta_description", "tech", 2, score, "Meta description", detail,
                      "Write a 120-155 char description that directly answers the page's "
                      "core question."))

    canon = page.link_rel("canonical")
    out.append(Result("canonical", "tech", 1, 1.0 if canon else 0.0, "Canonical URL",
                      canon or "No rel=canonical.",
                      "Set a self-referencing canonical to consolidate signals."))

    og = [k for k in ("og:title", "og:description", "og:image", "og:type") if page.meta(k)]
    score = len(og) / 4
    out.append(Result("open_graph", "tech", 1, score, "Open Graph metadata",
                      f"{len(og)}/4 core OG tags present.",
                      "Add og:title, og:description, og:image, og:type."))

    lang = page.html_lang
    out.append(Result("html_lang", "tech", 1, 1.0 if lang else 0.0, "HTML lang attribute",
                      f"lang=\"{lang}\"" if lang else "Missing lang attribute on <html>.",
                      "Set <html lang=\"en\"> (or the correct locale)."))

    imgs = [i for i in page.images if not (i.get("src", "").startswith("data:"))]
    if imgs:
        with_alt = sum(1 for i in imgs if (i.get("alt") or "").strip())
        score = with_alt / len(imgs)
        detail = f"{with_alt}/{len(imgs)} images have alt text."
    else:
        score, detail = 0.5, "No images found (alt text N/A)."
    out.append(Result("image_alt", "tech", 2, score, "Descriptive image alt text", detail,
                      "Give every content image descriptive alt text — it's parsed as content."))

    vp = page.meta("viewport")
    out.append(Result("viewport", "tech", 1, 1.0 if vp else 0.0, "Mobile viewport",
                      vp or "No viewport meta tag.",
                      "Add <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">."))
    return out


# ──────────────────────────────────────────────────────────────────────────
#  CATEGORY 7 — AI files (8)
# ──────────────────────────────────────────────────────────────────────────

def check_aifiles(ctx):
    out = []
    if ctx.offline or not ctx.network_ok:
        for cid, w, title in (("llms_txt", 4, "llms.txt published"),
                              ("llms_full", 1, "llms-full.txt published"),
                              ("sitemap_xml", 2, "XML sitemap reachable"),
                              ("robots_sitemap", 1, "Sitemap declared in robots.txt")):
            out.append(Result(cid, "aifiles", w, 0, title, skip_note(ctx), skipped=True))
        return out

    if ctx.llms_txt:
        n = len(ctx.llms_txt.split())
        score = 1.0 if n >= 40 else 0.6
        detail = f"/llms.txt present ({n} words)."
    else:
        score, detail = 0.0, "No /llms.txt — the emerging standard for telling LLMs what your site is."
    out.append(Result("llms_txt", "aifiles", 4, score, "llms.txt published", detail,
                      "Publish /llms.txt: an H1 with the brand, a blockquote summary, then "
                      "markdown link lists of your key pages."))

    out.append(Result("llms_full", "aifiles", 1, 1.0 if ctx.llms_full else 0.0,
                      "llms-full.txt published",
                      "/llms-full.txt present." if ctx.llms_full else "Not published (optional).",
                      "Optionally publish /llms-full.txt with the full text of key pages."))

    out.append(Result("sitemap_xml", "aifiles", 2, 1.0 if ctx.sitemap_ok else 0.0,
                      "XML sitemap reachable",
                      "/sitemap.xml is a valid urlset/sitemapindex." if ctx.sitemap_ok
                      else "No valid /sitemap.xml at the root.",
                      "Publish and keep an XML sitemap current."))

    has = bool(ctx.robots_sitemaps)
    out.append(Result("robots_sitemap", "aifiles", 1, 1.0 if has else 0.0,
                      "Sitemap declared in robots.txt",
                      f"{len(ctx.robots_sitemaps)} Sitemap: line(s)." if has
                      else "robots.txt does not declare a Sitemap.",
                      "Add `Sitemap: https://example.com/sitemap.xml` to robots.txt."))
    return out


# ──────────────────────────────────────────────────────────────────────────
#  Orchestration
# ──────────────────────────────────────────────────────────────────────────

_last_ctx = [None]

ALL_CHECKS = [check_crawl, check_schema, check_answer, check_content,
              check_authority, check_tech, check_aifiles]


# ──────────────────────────────────────────────────────────────────────────
#  AGENT READINESS — 16 signals, 5 categories
#  Mirrors Cloudflare's Agent Readiness Score (isitagentready.com): can an
#  autonomous agent discover, read and *act on* this site, not just cite it.
# ──────────────────────────────────────────────────────────────────────────

BRAND_CATEGORIES = [
    ("entity", "Knowledge Entity"),
    ("social", "Social Footprint"),
    ("community", "Community & Reviews"),
]
BRAND_CATEGORY_LABELS = dict(BRAND_CATEGORIES)

# Platforms an answer engine can resolve a brand against. Each entry is
# (label, weight, regex that matches a profile URL on that platform).
SOCIAL_PLATFORMS = [
    ("Instagram", 5, r"instagram\.com/[A-Za-z0-9._]{2,}"),
    ("YouTube", 7, r"youtube\.com/(@[\w.-]{2,}|c/[\w.-]+|channel/[\w-]+|user/[\w.-]+)"),
    ("X / Twitter", 6, r"(twitter|x)\.com/[A-Za-z0-9_]{2,}"),
    ("LinkedIn", 6, r"linkedin\.com/(company|in)/[\w.-]{2,}"),
    ("Facebook", 4, r"facebook\.com/[A-Za-z0-9.\-]{3,}"),
    ("TikTok", 4, r"tiktok\.com/@[\w.-]{2,}"),
    ("Pinterest", 3, r"pinterest\.[a-z.]{2,6}/[\w.-]{2,}"),
]

AGENT_CATEGORIES = [
    ("discover", "Discoverability"),
    ("content_acc", "Content Accessibility"),
    ("botctl", "Bot Access Control"),
    ("protocol", "Protocol Discovery"),
    ("commerce", "Commerce"),
]
AGENT_CATEGORY_LABELS = dict(AGENT_CATEGORIES)

WELL_KNOWN = {
    # MCP server card — SEP-1649 path, plus the two older conventions scanners
    # still accept.
    "mcp": "/.well-known/mcp/server-card.json",
    "mcp_flat": "/.well-known/mcp-server.json",
    "mcp_legacy": "/.well-known/mcp.json",
    "api_catalog": "/.well-known/api-catalog",
    "oauth_as": "/.well-known/oauth-authorization-server",
    "oauth_prm": "/.well-known/oauth-protected-resource",
    "auth_md_wk": "/.well-known/auth.md",
    "auth_md": "/auth.md",
    "agent_skills": "/.well-known/agent-skills/index.json",
    "web_bot_auth": "/.well-known/http-message-signatures-directory",
    "ucp": "/.well-known/ucp",
    "acp": "/.well-known/acp.json",
    "mpp": "/.well-known/mpp",
    "agents_json": "/agents.json",
    "agents_md": "/AGENTS.md",
    "agents_md_lower": "/agents.md",
}

AGENT_LEVELS = [
    (80, "Level 5 · Agent-Native"),
    (60, "Level 4 · Agent-Friendly"),
    (40, "Level 3 · Discoverable"),
    (20, "Level 2 · Bot-Aware"),
    (0, "Level 1 · Invisible"),
]

# Share of the headline score contributed by each track. Citation readiness
# leads because it affects visibility today; brand presence is weighted heavily
# because an engine that cannot resolve you as an entity will not cite you at
# all, however clean the HTML is.
TRACK_WEIGHTS = {"aeo": 0.45, "agent": 0.25, "brand": 0.30}
AEO_WEIGHT = TRACK_WEIGHTS["aeo"]

AI_LEVELS = [
    (90, "Level 5 · AI-Native"),
    (75, "Level 4 · AI-Optimized"),
    (55, "Level 3 · AI-Visible"),
    (35, "Level 2 · Bot-Aware"),
    (0, "Level 1 · Basic Web Presence"),
]


def ai_level(score):
    for cut, label in AI_LEVELS:
        if score >= cut:
            return label
    return AI_LEVELS[-1][1]


def agent_level(score):
    for cut, label in AGENT_LEVELS:
        if score >= cut:
            return label
    return AGENT_LEVELS[-1][1]


def load_agent_extras(ctx, timeout=15):
    """Probe the well-known endpoints and content-negotiation behaviour."""
    ctx.agent_probes = {}
    ctx.markdown_negotiated = False
    ctx.markdown_alternate = False
    if ctx.offline or not ctx.origin:
        ctx.agent_available = False
        return
    ctx.agent_available = True

    def probe_md_accept():
        req = urllib.request.Request(ctx.url, headers={
            "User-Agent": BROWSER_UA, "Accept": "text/markdown, text/plain;q=0.9"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                ctype = (r.headers.get("Content-Type") or "").lower()
                body = r.read(120_000).decode("utf-8", errors="replace")
                return ctype, body
        except Exception:
            return "", ""

    def probe_dns_aid():
        """DNS for AI Discovery: SVCB records under _index._agents.<domain>."""
        host = ctx.host.split(":")[0]
        if not host:
            return False
        q = urllib.parse.urlencode({"name": f"_index._agents.{host}", "type": "SVCB"})
        req = urllib.request.Request(
            f"https://cloudflare-dns.com/dns-query?{q}",
            headers={"Accept": "application/dns-json", "User-Agent": BROWSER_UA})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read(60_000).decode("utf-8", errors="replace"))
                return bool(data.get("Answer"))
        except Exception:
            return None  # could not resolve — report as unknown, not as a failure

    md_url = re.sub(r"/$", "", ctx.url) + ".md"
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futs = {k: ex.submit(head_or_get, ctx.origin + p, timeout)
                for k, p in WELL_KNOWN.items()}
        futs["_md_accept"] = ex.submit(probe_md_accept)
        futs["_md_alt"] = ex.submit(head_or_get, md_url, timeout)
        futs["_dns_aid"] = ex.submit(probe_dns_aid)
        out = {}
        for k, f in futs.items():
            try:
                out[k] = f.result()
            except Exception:
                out[k] = None

    for k in WELL_KNOWN:
        f = out.get(k)
        ctx.agent_probes[k] = bool(
            f and f.ok and f.body.strip() and "<html" not in f.body[:400].lower())

    ctx.dns_aid = out.get("_dns_aid")
    ctype, body = out.get("_md_accept") or ("", "")
    body_is_html = bool(body) and "<html" in body[:400].lower()
    # A site can advertise Content-Type: text/markdown and still return HTML —
    # the Cloudflare scanner flags exactly this, so don't credit the header alone.
    ctx.markdown_claimed = "markdown" in ctype and body_is_html
    ctx.markdown_negotiated = bool(
        body and not body_is_html and
        ("markdown" in ctype or re.search(r"^#{1,3} ", body, re.M)))

    alt = out.get("_md_alt")
    ctx.markdown_alternate = bool(
        alt and alt.ok and "<html" not in alt.body[:400].lower()
        and re.search(r"^#{1,3} ", alt.body, re.M))


def load_brand_extras(ctx, timeout=15):
    """Look the brand up in the places answer engines resolve entities."""
    ctx.brand_name = ""
    ctx.wikipedia = None
    ctx.wikidata = None
    ctx.reddit_mentions = None
    ctx.social_found = []
    ctx.brand_available = False

    for n in has_type(ctx.nodes, "organization", "onlinestore", "localbusiness",
                      "corporation", "brand"):
        ctx.brand_name = first_value(n, "name")
        if ctx.brand_name:
            break
    if not ctx.brand_name and ctx.host:
        ctx.brand_name = ctx.host.split(":")[0].replace("www.", "").split(".")[0]

    # Social profiles: take them from schema sameAs and from every on-page link,
    # so a brand that links its profiles in the footer still gets credit.
    haystack = " ".join([h for h, _ in ctx.page.anchors])
    for n in ctx.nodes:
        v = n.get("sameAs")
        if isinstance(v, str):
            haystack += " " + v
        elif isinstance(v, list):
            haystack += " " + " ".join(str(x) for x in v)
    for label, weight, pattern in SOCIAL_PLATFORMS:
        if re.search(pattern, haystack, re.I):
            ctx.social_found.append((label, weight))

    if ctx.offline or not ctx.brand_name:
        return

    def wikipedia():
        q = urllib.parse.urlencode({"action": "query", "list": "search",
                                    "srsearch": ctx.brand_name, "format": "json",
                                    "srlimit": "3"})
        f = fetch(f"https://en.wikipedia.org/w/api.php?{q}", timeout=timeout)
        if not f.ok:
            return None
        try:
            hits = json.loads(f.body)["query"]["search"]
        except Exception:
            return None
        target = ctx.brand_name.lower()
        return any(h.get("title", "").lower() == target for h in hits)

    def wikidata():
        q = urllib.parse.urlencode({"action": "wbsearchentities", "format": "json",
                                    "language": "en", "search": ctx.brand_name})
        f = fetch(f"https://www.wikidata.org/w/api.php?{q}", timeout=timeout)
        if not f.ok:
            return None
        try:
            return bool(json.loads(f.body).get("search"))
        except Exception:
            return None

    def reddit():
        q = urllib.parse.urlencode({"q": ctx.brand_name, "limit": "25", "sort": "new"})
        f = fetch(f"https://www.reddit.com/search.json?{q}", timeout=timeout)
        if not f.ok:
            return None
        try:
            return len(json.loads(f.body)["data"]["children"])
        except Exception:
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        fw, fd, fr = ex.submit(wikipedia), ex.submit(wikidata), ex.submit(reddit)
        ctx.wikipedia, ctx.wikidata, ctx.reddit_mentions = fw.result(), fd.result(), fr.result()
    ctx.brand_available = any(v is not None for v in
                              (ctx.wikipedia, ctx.wikidata, ctx.reddit_mentions))


def check_brand(ctx):
    """Off-site presence: can an engine resolve this brand as a real entity?"""
    out = []
    note = skip_note(ctx)
    name = getattr(ctx, "brand_name", "") or "this brand"

    # ---- Knowledge Entity (40) ----
    if ctx.wikipedia is None:
        out.append(Result("brand_wikipedia", "entity", 22, 0, "Wikipedia article",
                          note, "", skipped=True))
    else:
        out.append(Result("brand_wikipedia", "entity", 22,
                          1.0 if ctx.wikipedia else 0.0, "Wikipedia article",
                          f"Wikipedia article found for {name}." if ctx.wikipedia else
                          f"No Wikipedia article for {name} — the strongest entity signal "
                          "an answer engine can check, and it is missing.",
                          "Wikipedia requires independent notability; you cannot write your "
                          "own. Earn press coverage first, then it becomes possible."))

    if ctx.wikidata is None:
        out.append(Result("brand_wikidata", "entity", 18, 0, "Wikidata entity",
                          note, "", skipped=True))
    else:
        out.append(Result("brand_wikidata", "entity", 18,
                          1.0 if ctx.wikidata else 0.0, "Wikidata entity",
                          f"Wikidata entity found for {name}." if ctx.wikidata else
                          f"No Wikidata entity for {name} — engines have no canonical ID "
                          "to attach facts about you to.",
                          "Create a Wikidata item with official website, logo, industry and "
                          "founding date, then reference it in your Organization sameAs."))

    # ---- Social Footprint (35) ----
    found = {lbl for lbl, _ in ctx.social_found}
    total_w = sum(w for _, w, _ in SOCIAL_PLATFORMS)
    got_w = sum(w for _, w in ctx.social_found)
    missing = [lbl for lbl, _, _ in SOCIAL_PLATFORMS if lbl not in found]
    out.append(Result("brand_social", "social", 25, got_w / total_w,
                      "Linked social profiles",
                      (f"Found {len(found)}/{len(SOCIAL_PLATFORMS)}: "
                       f"{', '.join(sorted(found)) or 'none'}."
                       + (f" Missing: {', '.join(missing)}." if missing else "")),
                      "Link every official profile from the site footer AND list them in "
                      "the Organization sameAs — that is how engines tie the accounts to you."))

    yt = "YouTube" in found
    out.append(Result("brand_video", "social", 10, 1.0 if yt else 0.0,
                      "Video presence",
                      "YouTube channel linked." if yt else
                      "No YouTube channel linked — video is disproportionately surfaced "
                      "in AI answers and Google's AI Overviews.",
                      "Publish and link a YouTube channel; even a small one creates an "
                      "entity engines can resolve."))

    # ---- Community & Reviews (25) ----
    if ctx.reddit_mentions is None:
        out.append(Result("brand_reddit", "community", 15, 0, "Reddit / forum footprint",
                          note, "", skipped=True))
    else:
        n = ctx.reddit_mentions
        score = 1.0 if n >= 10 else (0.6 if n >= 3 else (0.25 if n else 0.0))
        out.append(Result("brand_reddit", "community", 15, score,
                          "Reddit / forum footprint",
                          f"{n} recent Reddit results for {name}."
                          + ("" if n else " Reddit is one of the most-cited domains in AI "
                                          "answers — absence here is costly."),
                          "Earn genuine community discussion. Do not astroturf; engines and "
                          "moderators both punish it. Answer questions where your expertise "
                          "is real."))

    agg = has_type(ctx.nodes, "aggregaterating", "review")
    reviews = agg or re.search(r"trustpilot|yotpo|judge\.me|reviews\.io|okendo",
                               ctx.html, re.I)
    out.append(Result("brand_reviews", "community", 10, 1.0 if reviews else 0.0,
                      "Third-party reviews",
                      "Review platform or AggregateRating present." if reviews else
                      "No review-platform signals — engines have no independent quality "
                      "evidence to cite.",
                      "Collect reviews on an independent platform (Trustpilot, Google "
                      "Business) and expose AggregateRating schema."))
    return out


def check_agent(ctx):
    """The 16 agent-readiness signals. Commerce is informational (weight 0)."""
    out = []
    live = getattr(ctx, "agent_available", False)
    probes = getattr(ctx, "agent_probes", {})
    note = skip_note(ctx)

    def net(cid, cat, weight, ok, title, yes, no, fix):
        if not live:
            return Result(cid, cat, weight, 0, title, note, fix, skipped=True)
        return Result(cid, cat, weight, 1.0 if ok else 0.0, title, yes if ok else no, fix)

    # ---- Discoverability (30) ----
    out.append(net("agent_robots", "discover", 7, ctx.robots_text is not None,
                   "robots.txt present (RFC 9309)",
                   "robots.txt served and parseable.",
                   "No robots.txt — agents have no crawl contract to read.",
                   "Publish /robots.txt with explicit agent rules and a Sitemap line."))

    out.append(net("agent_sitemap", "discover", 7, bool(ctx.sitemap_ok),
                   "XML sitemap reachable",
                   "/sitemap.xml is a valid urlset/sitemapindex.",
                   "No valid /sitemap.xml — agents must guess your URL space.",
                   "Publish /sitemap.xml and declare it in robots.txt."))

    out.append(net("agent_llms", "discover", 9, bool(ctx.llms_txt),
                   "llms.txt on-ramp",
                   "/llms.txt present — agents get a curated map of the site.",
                   "No /llms.txt — the single highest-value agent discovery file.",
                   "Publish /llms.txt: H1 brand name, blockquote summary, then "
                   "markdown link lists of key pages."))

    link_hdr = ""
    if ctx.fetched:
        link_hdr = ctx.fetched.headers.get("Link", "") or ""
    has_link = bool(re.search(r'rel\s*=\s*"?(alternate|describedby|service-desc|'
                              r'service-doc|api-catalog)', link_hdr, re.I))
    out.append(Result("agent_link_headers", "discover", 4,
                      1.0 if has_link else 0.0, "Link headers (RFC 8288)",
                      f"Link: {trunc(link_hdr, 90)}" if has_link else
                      ("No Link header advertising alternates or service descriptions."
                       if ctx.fetched else note),
                      "Emit `Link: </page.md>; rel=\"alternate\"; type=\"text/markdown\"` "
                      "and rel=\"service-desc\" for your OpenAPI spec.",
                      skipped=not ctx.fetched))

    dns = getattr(ctx, "dns_aid", None)
    out.append(Result("agent_dns_aid", "discover", 3,
                      1.0 if dns else 0.0, "DNS for AI Discovery (DNS-AID)",
                      ("SVCB records found under _index._agents." if dns else
                       "No _index._agents SVCB records — agents cannot discover your "
                       "services via DNS.") if dns is not None else
                      "DNS-AID lookup could not be completed.",
                      "Publish SVCB/HTTPS records at _index._agents.<domain> with alpn and "
                      "endpoint params, signed with DNSSEC.",
                      skipped=(dns is None)))

    # ---- Content Accessibility (26) ----
    md_claimed = getattr(ctx, "markdown_claimed", False)
    if not live:
        out.append(Result("agent_md_negotiation", "content_acc", 9, 0,
                          "Markdown content negotiation", note, "", skipped=True))
    else:
        ok = ctx.markdown_negotiated
        out.append(Result("agent_md_negotiation", "content_acc", 9, 1.0 if ok else 0.0,
                          "Markdown content negotiation",
                          "Returns real markdown for `Accept: text/markdown`." if ok else
                          ("Responds with Content-Type: text/markdown but the body is still "
                           "HTML — the header is lying to agents." if md_claimed else
                           "`Accept: text/markdown` still returns HTML — agents must strip markup."),
                          "Serve a genuine markdown representation when the Accept header "
                          "requests it, with Content-Type: text/markdown."))

    out.append(net("agent_md_alternate", "content_acc", 7, ctx.markdown_alternate,
                   "Markdown mirror (.md URL)",
                   "A .md mirror of this URL is served.",
                   "No .md mirror at this URL.",
                   "Publish `<url>.md` alongside each page and link it with "
                   "rel=\"alternate\" type=\"text/markdown\"."))

    body_words = len(words(ctx.page.body_text()))
    script_count = ctx.html.lower().count("<script")
    if body_words >= 250:
        score, detail = 1.0, f"{body_words} words present in the served HTML."
    elif body_words >= 80:
        score, detail = 0.5, f"Only {body_words} words in the served HTML."
    else:
        score, detail = 0.0, (f"Only {body_words} words in the served HTML with "
                              f"{script_count} script tags — looks client-side rendered. "
                              "Agents that don't run JS see nothing.")
    out.append(Result("agent_static_render", "content_acc", 6, score,
                      "Content present without JavaScript", detail,
                      "Server-render or pre-render the main content; agents rarely execute JS."))

    has_sd = bool(ctx.nodes)
    semantic = ctx.page.has_main and len(ctx.page.headings) >= 2
    score = (0.6 if has_sd else 0) + (0.4 if semantic else 0)
    out.append(Result("agent_structured", "content_acc", 4, score,
                      "Semantic HTML + structured data",
                      f"JSON-LD: {'yes' if has_sd else 'no'}; "
                      f"<main>/<article> + headings: {'yes' if semantic else 'no'}.",
                      "Wrap content in <main>/<article> with a heading outline and JSON-LD."))

    # ---- Bot Access Control (22) ----
    if live and ctx.robots_text is not None:
        named = [a for a, _, _ in AI_CRAWLERS
                 if re.search(rf"user-agent:\s*{re.escape(a)}\b", ctx.robots_text, re.I)]
        if len(named) >= 4:
            score, detail = 1.0, f"{len(named)} AI agents named explicitly: {', '.join(named[:6])}"
        elif named:
            score, detail = 0.5, f"Only {len(named)} AI agent(s) named: {', '.join(named)}"
        else:
            score, detail = 0.0, ("No AI crawler is named in robots.txt — you have no stated "
                                  "policy, only the wildcard default.")
        out.append(Result("agent_ai_rules", "botctl", 9, score, "Explicit AI bot rules", detail,
                          "Add named User-agent groups for GPTBot, OAI-SearchBot, ClaudeBot, "
                          "PerplexityBot and Google-Extended with explicit Allow/Disallow."))
    else:
        out.append(Result("agent_ai_rules", "botctl", 9, 0, "Explicit AI bot rules",
                          note, "", skipped=True))

    if live and ctx.robots_text is not None:
        cs = re.search(r"content-signal:\s*(.+)", ctx.robots_text, re.I)
        out.append(Result("agent_content_signals", "botctl", 7, 1.0 if cs else 0.0,
                          "Content Signals policy",
                          f"Content-Signal: {trunc(cs.group(1), 70)}" if cs else
                          "No Content-Signal directive — usage intent (search / ai-input / "
                          "ai-train) is unstated.",
                          "Add `Content-Signal: search=yes, ai-input=yes, ai-train=no` "
                          "(or your preference) to robots.txt."))
    else:
        out.append(Result("agent_content_signals", "botctl", 7, 0, "Content Signals policy",
                          note, "", skipped=True))

    if live:
        sigagent = bool(ctx.fetched and (
            ctx.fetched.headers.get("Signature-Agent") or
            ctx.fetched.headers.get("Accept-Signature")))
        wba = sigagent or probes.get("web_bot_auth", False)
        out.append(Result("agent_web_bot_auth", "botctl", 6, 1.0 if wba else 0.0,
                          "Web Bot Auth support",
                          "JWKS directory published for signed agent requests." if wba else
                          "No /.well-known/http-message-signatures-directory — you cannot "
                          "distinguish a verified agent from a scraper.",
                          "Publish a JWKS at /.well-known/http-message-signatures-directory "
                          "(Web Bot Auth) so signed agent requests can be verified."))
    else:
        out.append(Result("agent_web_bot_auth", "botctl", 6, 0, "Web Bot Auth support",
                          note, "", skipped=True))

    # ---- Protocol Discovery (22) ----
    mcp_ok = any(probes.get(k, False) for k in ("mcp", "mcp_flat", "mcp_legacy"))
    out.append(net("agent_mcp_card", "protocol", 6, mcp_ok,
                   "MCP Server Card",
                   "MCP server card published — agents can call your tools.",
                   "No MCP server card — agents cannot discover any callable capability.",
                   "Publish /.well-known/mcp/server-card.json (SEP-1649) describing your "
                   "endpoint and tools."))

    out.append(net("agent_api_catalog", "protocol", 3, probes.get("api_catalog", False),
                   "API Catalog (RFC 9727)",
                   "/.well-known/api-catalog served.",
                   "No /.well-known/api-catalog.",
                   "Publish an api-catalog linking to your OpenAPI descriptions."))

    out.append(net("agent_oauth_as", "protocol", 3, probes.get("oauth_as", False),
                   "OAuth server discovery (RFC 8414)",
                   "/.well-known/oauth-authorization-server served.",
                   "No OAuth authorization-server metadata.",
                   "Expose RFC 8414 metadata so agents can authenticate without hardcoding."))

    out.append(net("agent_oauth_prm", "protocol", 2, probes.get("oauth_prm", False),
                   "OAuth Protected Resource (RFC 9728)",
                   "/.well-known/oauth-protected-resource served.",
                   "No protected-resource metadata.",
                   "Publish RFC 9728 metadata pointing at your authorization server."))

    out.append(net("agent_auth_md", "protocol", 2,
                   probes.get("auth_md_wk", False) or probes.get("auth_md", False),
                   "Auth.md (human-readable auth guide)",
                   "Auth.md published.",
                   "No Auth.md — agents have no prose description of how to authenticate.",
                   "Publish /.well-known/auth.md explaining auth flows in plain language."))

    out.append(net("agent_skills", "protocol", 3, probes.get("agent_skills", False),
                   "Agent Skills index",
                   "/.well-known/agent-skills/index.json published.",
                   "No Agent Skills index — agents cannot discover documented capabilities.",
                   "Publish /.well-known/agent-skills/index.json listing skill documents."))

    webmcp = bool(re.search(r"navigator\.modelContext|window\.mcp\b|webmcp", ctx.html, re.I))
    out.append(Result("agent_webmcp", "protocol", 2, 1.0 if webmcp else 0.0,
                      "WebMCP in-page tools",
                      "WebMCP tool registration detected." if webmcp else
                      "No WebMCP — the page exposes no in-browser tools to agents.",
                      "Register in-page tools via navigator.modelContext (WebMCP) so "
                      "browser agents can act on the page."))

    manifest = (probes.get("agents_json", False) or probes.get("agents_md", False)
                or probes.get("agents_md_lower", False))
    out.append(net("agent_manifest", "protocol", 1, manifest,
                   "Agent manifest (agents.json / AGENTS.md)",
                   "Agent manifest published.",
                   "No /agents.json or /AGENTS.md describing how agents should behave here.",
                   "Publish AGENTS.md with usage rules, rate limits and contact."))

    # ---- Commerce (informational — weight 0, mirrors the reference tool) ----
    is_shop = bool(has_type(ctx.nodes, "product", "offer", "onlinestore")) or \
        bool(re.search(r"/(cart|checkout|collections|products)/", ctx.html, re.I))
    for cid, key, title, fix in (
            ("commerce_x402", None, "x402 agent payments",
             "Support HTTP 402 Payment Required for agent-native payment flows."),
            ("commerce_mpp", "mpp", "MPP machine payments (/.well-known/mpp)",
             "Publish an MPP manifest for machine-to-machine payment rails."),
            ("commerce_ucp", "ucp", "UCP profile (/.well-known/ucp)",
             "Publish a Universal Commerce Protocol profile with merchant metadata."),
            ("commerce_acp", "acp", "ACP discovery (/.well-known/acp.json)",
             "Publish an Agentic Commerce Protocol discovery document.")):
        if not live:
            out.append(Result(cid, "commerce", 0, 0, title, note, fix, skipped=True))
            continue
        ok = probes.get(key, False) if key else False
        out.append(Result(cid, "commerce", 0, 1.0 if ok else 0.0, title,
                          ("Published." if ok else
                           ("Not published — relevant, this looks like a commerce site."
                            if is_shop else
                            "Not published (informational; no commerce signals detected).")),
                          fix))
    return out


# ──────────────────────────────────────────────────────────────────────────
#  Per-engine readiness matrix
# ──────────────────────────────────────────────────────────────────────────

ENGINES = [
    ("ChatGPT (OpenAI)", ["GPTBot", "OAI-SearchBot", "ChatGPT-User"]),
    ("Google Gemini / AI Overviews", ["Google-Extended", "Googlebot"]),
    ("Claude (Anthropic)", ["ClaudeBot", "Claude-User", "Claude-SearchBot"]),
    ("Perplexity", ["PerplexityBot", "Perplexity-User"]),
    ("Microsoft Copilot", ["bingbot", "msnbot"]),
    ("Apple Intelligence", ["Applebot", "Applebot-Extended"]),
    ("Meta AI (Llama)", ["meta-externalagent", "FacebookBot"]),
    ("Amazon Rufus / Alexa", ["Amazonbot"]),
    ("DeepSeek", ["DeepSeekBot", "CCBot"]),
    ("Mistral Le Chat", ["MistralAI-User", "CCBot"]),
    ("You.com", ["YouBot"]),
    ("Cohere", ["cohere-ai"]),
    ("ByteDance Doubao", ["Bytespider"]),
    ("Open models (Qwen, Kimi, HF)", ["CCBot", "AI2Bot"]),
]


def engine_matrix(ctx, results):
    """Per-engine score: 50% can-it-access, 50% is-the-content-citable."""
    by_id = {r.id: r for r in results}
    content_ids = ["jsonld_present", "primary_schema", "faq_schema", "question_headings",
                   "direct_answers", "word_count", "freshness", "author_byline",
                   "llms_txt", "agent_static_render"]
    got = [by_id[i] for i in content_ids if i in by_id and not by_id[i].skipped]
    content_pct = (sum(r.weight * r.score for r in got) / sum(r.weight for r in got) * 100
                   if got else 0)

    rows = []
    for name, bots in ENGINES:
        if ctx.offline or not ctx.network_ok or ctx.robots_text is None:
            access, blocked = None, []
        else:
            blocked = [b for b in bots if not robots_allows(ctx.robots_groups, b, ctx.path)[0]]
            access = 100.0 * (1 - len(blocked) / len(bots))
            if ctx.gptbot_status in (401, 403, 429) and name == "ChatGPT":
                access = min(access, 0.0)
        if access is None:
            score, verdict = round(content_pct), "content only (crawler access unknown)"
        elif access == 0:
            score, verdict = 0, "BLOCKED — cannot crawl this page"
        else:
            score = round(0.5 * access + 0.5 * content_pct)
            verdict = ("can crawl and cite" if not blocked
                       else f"partial — {', '.join(blocked)} blocked")
        rows.append({"engine": name, "bots": bots, "score": score,
                     "access": None if access is None else round(access),
                     "content": round(content_pct), "verdict": verdict})
    return rows


def audit(url, html=None, offline=False, timeout=25, generate_dir=None):
    """Run every check. Returns a report dict."""
    fetched = None
    if html is None:
        fetched = fetch(url, timeout=timeout)
        if not fetched.ok:
            return {"url": url, "error": fetched.error or f"HTTP {fetched.status}",
                    "status": fetched.status, "score": 0, "grade": "F",
                    "categories": [], "checks": []}
        html = fetched.body
        url = fetched.final_url

    page = parse_html(html)
    ctx = Context(url, page, html, fetched, offline=offline)
    if not offline:
        ctx.load_network_extras(timeout=min(timeout, 20))

    results = []
    for fn in ALL_CHECKS:
        results.extend(fn(ctx))

    if not offline:
        load_agent_extras(ctx, timeout=min(timeout, 20))
    else:
        ctx.agent_available = False
        ctx.agent_probes = {}
    agent_results = check_agent(ctx)
    if not offline:
        load_brand_extras(ctx, timeout=min(timeout, 20))
    else:
        ctx.brand_name = ""
        ctx.wikipedia = ctx.wikidata = ctx.reddit_mentions = None
        ctx.social_found = []
        ctx.brand_available = False
    brand_results = check_brand(ctx)

    cats = []
    total_w = total_s = 0.0
    for key, label in CATEGORIES:
        rs = [r for r in results if r.category == key and not r.skipped]
        w = sum(r.weight for r in rs)
        s = sum(r.weight * r.score for r in rs)
        if w:
            cats.append({"key": key, "label": label, "weight": w,
                         "earned": round(s, 2), "pct": round(100 * s / w)})
            total_w += w
            total_s += s
    score = round(100 * total_s / total_w) if total_w else 0

    acats = []
    aw = as_ = 0.0
    for key, label in AGENT_CATEGORIES:
        rs = [r for r in agent_results if r.category == key and not r.skipped]
        w = sum(r.weight for r in rs)
        s = sum(r.weight * r.score for r in rs)
        if w:
            acats.append({"key": key, "label": label, "weight": w,
                          "earned": round(s, 2), "pct": round(100 * s / w)})
            aw += w
            as_ += s
    # The agent-readiness signals are almost all network probes. If they were
    # skipped, the handful of on-page checks left would score near-perfect and
    # badly overstate readiness — report it as not measured instead.
    agent_measured = bool(ctx.agent_available and aw >= 50)
    ascore = round(100 * as_ / aw) if (aw and agent_measured) else None
    if not agent_measured:
        acats = []

    # One headline number. Citation readiness is weighted higher than agent
    # readiness because it affects visibility today, while most agent protocols
    # are still emerging. When the agent probes could not run, the AEO track
    # carries the whole score rather than being diluted by unmeasured signals.
    bcats = []
    bw = bs = 0.0
    for key, label in BRAND_CATEGORIES:
        rs = [r for r in brand_results if r.category == key and not r.skipped]
        w = sum(r.weight for r in rs)
        sv = sum(r.weight * r.score for r in rs)
        if w:
            bcats.append({"key": key, "label": label, "weight": w,
                          "earned": round(sv, 2), "pct": round(100 * sv / w)})
            bw += w
            bs += sv
    brand_measured = bool(getattr(ctx, "brand_available", False) and bw >= 50)
    bscore = round(100 * bs / bw) if (bw and brand_measured) else None
    if not brand_measured:
        bcats = []

    # Renormalise over the tracks that were actually measured, so an unmeasured
    # track never silently drags the headline number down.
    parts = [("aeo", score)]
    if agent_measured:
        parts.append(("agent", ascore))
    if brand_measured:
        parts.append(("brand", bscore))
    wsum = sum(TRACK_WEIGHTS[k] for k, _ in parts)
    combined = round(sum(TRACK_WEIGHTS[k] * v for k, v in parts) / wsum)

    fixes = sorted([r for r in results + agent_results + brand_results
                    if not r.skipped and r.score < 1.0 and r.weight > 0],
                   key=lambda r: (-(r.weight * (1 - r.score)), r.category))

    report = {
        "ai_score": combined,
        "ai_level": ai_level(combined),
        "url": url,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "http_status": fetched.status if fetched else None,
        "load_seconds": round(fetched.elapsed, 2) if fetched else None,
        "offline": offline,
        "network_ok": ctx.network_ok,
        "score": score,
        "grade": grade(score),
        "max_possible": round(total_w),
        "title": page.title,
        "word_count": len(words(page.body_text())),
        "categories": cats,
        "checks": [r.as_dict() for r in results],
        "agent_score": ascore,
        "agent_measured": agent_measured,
        "agent_level": agent_level(ascore) if agent_measured else "not measured (needs network)",
        "agent_categories": acats,
        "agent_checks": [dict(r.as_dict(),
                              category_label=AGENT_CATEGORY_LABELS[r.category])
                         for r in agent_results],
        "brand_score": bscore,
        "brand_measured": brand_measured,
        "brand_name": getattr(ctx, "brand_name", ""),
        "brand_categories": bcats,
        "brand_checks": [dict(r.as_dict(),
                              category_label=BRAND_CATEGORY_LABELS[r.category])
                         for r in brand_results],
        "track_weights": TRACK_WEIGHTS,
        "engines": engine_matrix(ctx, results + agent_results),
        "priority_fixes": [
            {"title": r.title,
             "category": (CATEGORY_LABELS.get(r.category)
                          or AGENT_CATEGORY_LABELS.get(r.category)
                          or BRAND_CATEGORY_LABELS.get(r.category, r.category)),
             "track": ("AEO" if r.category in CATEGORY_LABELS else
                       "Agent" if r.category in AGENT_CATEGORY_LABELS else "Brand"),
             "id": r.id,
             "impact": round(r.weight * (1 - r.score), 1),
             "detail": r.detail, "fix": r.fix}
            for r in fixes[:15]],
    }
    if generate_dir:
        report["generated"] = generate_fixes(ctx, report, generate_dir)
    _last_ctx[0] = ctx
    return report


# Effort and ownership per finding, mirroring the reference dashboard's task board.
# category: Owned (you control it), Earned (someone else must act), Technical.
TASK_META = {
    "brand_wikipedia":   ("High", "Earned"),
    "brand_wikidata":    ("Low", "Owned"),
    "brand_reddit":      ("High", "Earned"),
    "brand_reviews":     ("Medium", "Earned"),
    "brand_social":      ("Medium", "Owned"),
    "brand_video":       ("High", "Owned"),
    "faq_schema":        ("Low", "Technical"),
    "schema_dates":      ("Low", "Technical"),
    "schema_author":     ("Low", "Technical"),
    "llms_txt":          ("Low", "Technical"),
    "agent_llms":        ("Low", "Technical"),
    "robots_ai_allowed": ("Low", "Technical"),
    "agent_ai_rules":    ("Low", "Technical"),
    "agent_content_signals": ("Low", "Technical"),
    "agent_md_negotiation":  ("High", "Technical"),
    "agent_mcp_card":    ("High", "Technical"),
    "agent_dns_aid":     ("Medium", "Technical"),
    "word_count":        ("Medium", "Owned"),
    "citations":         ("Low", "Owned"),
    "author_byline":     ("Medium", "Owned"),
    "eeat_signals":      ("Medium", "Owned"),
    "freshness":         ("Medium", "Owned"),
    "tldr_block":        ("Low", "Owned"),
}
EFFORT_MULT = {"Low": 1.0, "Medium": 0.85, "High": 0.65}


def build_tasks(rep):
    """Turn findings into a prioritised task board.

    Opportunity blends how many points the fix is worth with how cheap it is,
    so a 2-point one-liner can outrank a 9-point rewrite.
    """
    rows = (rep["checks"] + rep.get("agent_checks", []) + rep.get("brand_checks", []))
    tracks = {"AEO": CATEGORY_LABELS, "Agent": AGENT_CATEGORY_LABELS,
              "Brand": BRAND_CATEGORY_LABELS}
    # A track that could not be measured contributes nothing to the headline
    # score, so its findings must not advertise headline points they cannot add.
    measured = {"AEO": True, "Agent": rep.get("agent_measured", False),
                "Brand": rep.get("brand_measured", False)}
    skipped_tracks = [t for t, m in measured.items() if not m]
    tasks = []
    for c in rows:
        if c["status"] in ("pass", "skip") or not c["weight"]:
            continue
        gap = c["weight"] * (1 - c["score"])
        effort, category = TASK_META.get(c["id"], ("Medium", "Owned"))
        track = next((t for t, labels in tracks.items() if c["category"] in labels), "AEO")
        if not measured[track]:
            continue
        # gap * track share = the points this fix adds to the headline score, so
        # findings from different tracks become directly comparable.
        share = rep.get("track_weights", TRACK_WEIGHTS).get(track.lower(), 0.33)
        headline_pts = gap * share
        opportunity = min(99, round(headline_pts * EFFORT_MULT[effort] * 15))
        impact = ("High" if headline_pts >= 4 else
                  "Medium" if headline_pts >= 1.5 else "Low")
        tasks.append({
            "opportunity": opportunity, "impact": impact, "effort": effort,
            "category": category, "track": track, "id": c["id"],
            "title": c["title"], "evidence": c["detail"], "action": c["fix"],
            "points": round(gap, 1), "headline_points": round(headline_pts, 1),
        })
    tasks.sort(key=lambda t: (-t["opportunity"], -t["points"]))
    return tasks, skipped_tracks


def write_tasks(tasks, path):
    import csv
    fields = ["opportunity", "impact", "effort", "category", "track", "title",
              "points", "headline_points", "evidence", "action", "id"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for t in tasks:
            w.writerow({k: t[k] for k in fields})
    return path


def print_tasks(tasks, skipped_tracks=(), limit=12):
    print()
    print(f"{C['bold']}TASK BOARD{C['reset']}  {C['dim']}ranked by opportunity "
          f"(impact vs effort){C['reset']}")
    print("─" * 92)
    print(f"  {'OPP':<5}{'IMPACT':<8}{'EFFORT':<8}{'TYPE':<11}{'TASK':<42}PTS")
    print("─" * 92)
    for t in tasks[:limit]:
        col = C["green"] if t["opportunity"] >= 60 else C["yellow"] if t["opportunity"] >= 35 else C["dim"]
        print(f"  {col}{t['opportunity']:<5}{C['reset']}{t['impact']:<8}{t['effort']:<8}"
              f"{t['category']:<11}{trunc(t['title'], 40):<42}"
              f"{C['dim']}+{t['headline_points']}{C['reset']}")
    if len(tasks) > limit:
        print(f"  {C['dim']}… {len(tasks) - limit} more{C['reset']}")
    print("─" * 92)
    owned = sum(1 for t in tasks if t["category"] == "Owned")
    earned = sum(1 for t in tasks if t["category"] == "Earned")
    tech = sum(1 for t in tasks if t["category"] == "Technical")
    print(f"  {C['dim']}{len(tasks)} open tasks — {tech} technical, {owned} owned content, "
          f"{earned} earned (needs someone else to act){C['reset']}")
    if skipped_tracks:
        print(f"  {C['yellow']}! {', '.join(skipped_tracks)} track(s) could not be measured "
              f"— their tasks are not listed.{C['reset']}")


PROMPT_TEMPLATES = [
    "What are the best {category} brands?",
    "Best {category} for {use_case}",
    "Top rated {category} companies in the US",
    "{competitor_a} vs {competitor_b} — which is better?",
    "Is {brand} any good?",
    "Where can I buy {category} online?",
    "Cheapest reliable {category}",
    "{category} recommendations from real customers",
]


def init_prompts(ctx, rep, path):
    """Write a starter prompt set for tracking brand mentions in AI answers."""
    brand = rep.get("brand_name") or "your brand"
    headings = [t for lvl, t in ctx.page.headings if lvl in (1, 2)][:8]
    # Pull the category from the H1/title only, and cap it at three words —
    # matching across concatenated headings produces nonsense phrases.
    category = ""
    source = (headings[0] if headings else "") or ctx.page.title
    m = re.search(r"\b(?:best|top|guide to|choose an?|choosing an?|buy an?|buying an?)\s+"
                  r"((?:[A-Za-z]+[ -]){0,2}[A-Za-z]+)", source, re.I)
    if m:
        category = normalize_ws(m.group(1)).rstrip(".,:;")
    if not category:
        # fall back to the last two words of the title before any separator
        head = re.split(r"[—–|:]", ctx.page.title)[0]
        words_ = words(head)[-2:]
        category = " ".join(words_) if words_ else ""
    category = (category or "your product category").lower()

    prompts = []
    for tpl in PROMPT_TEMPLATES:
        q = (tpl.replace("{category}", category).replace("{brand}", brand)
                .replace("{use_case}", "weddings").replace("{competitor_a}", "COMPETITOR_A")
                .replace("{competitor_b}", "COMPETITOR_B"))
        prompts.append(q)
    for h in headings:
        if is_question(h):
            prompts.append(h)

    saved = load_site_config()
    competitors = saved.get("competitors") or ["TODO: add 3-8 competitor brand names"]
    cfg = {
        "brand": saved.get("brand") or brand,
        "domain": ctx.host,
        "category": category,
        "competitors": competitors,
        "models": ["ChatGPT", "Claude", "Perplexity", "Google AI Overviews"],
        "prompts": prompts,
        "_how_to_use": (
            "Run each prompt against each model, then append one row per "
            "(prompt, model) to your runs CSV with columns: date, prompt, model, "
            "brand_mentioned (yes/no), position (1-N or blank), "
            "competitors_mentioned (semicolon-separated), cited_domains "
            "(semicolon-separated). Then: seo_beacon.py --prompt-report runs.csv"),
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)
    return path


def prompt_report(path, brand_domain=""):
    """Aggregate prompt-run results into visibility, share-of-voice and sources."""
    import csv
    from collections import Counter
    with open(path, newline="", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r.get("prompt")]
    if not rows:
        print(f"{C['red']}No rows in {path}{C['reset']}")
        return

    def yes(v):
        return str(v).strip().lower() in ("yes", "y", "true", "1")

    total = len(rows)
    mentioned = [r for r in rows if yes(r.get("brand_mentioned"))]
    rate = 100 * len(mentioned) / total

    comp = Counter()
    for r in rows:
        for c in (r.get("competitors_mentioned") or "").split(";"):
            c = c.strip()
            if c:
                comp[c] += 1

    domains, own_cited = Counter(), 0
    for r in rows:
        ds = [d.strip().lower() for d in (r.get("cited_domains") or "").split(";") if d.strip()]
        for d in ds:
            domains[d] += 1
        if brand_domain and any(brand_domain.lower().replace("www.", "") in d for d in ds):
            own_cited += 1

    col = C["green"] if rate >= 40 else C["yellow"] if rate >= 10 else C["red"]
    print()
    print(f"{C['bold']}PROMPT VISIBILITY{C['reset']}  {C['dim']}{total} runs across "
          f"{len({r['prompt'] for r in rows})} prompts / "
          f"{len({r.get('model', '') for r in rows})} models{C['reset']}")
    print("─" * 74)
    print(f"  {col}{C['bold']}BRAND MENTIONED IN {rate:.0f}% OF ANSWERS{C['reset']}"
          f"  {C['dim']}({len(mentioned)}/{total}){C['reset']}")
    if brand_domain:
        oc = 100 * own_cited / total
        ocol = C["green"] if oc >= 20 else C["yellow"] if oc > 0 else C["red"]
        print(f"  {ocol}YOUR SITE CITED IN {oc:.0f}% OF ANSWERS{C['reset']}"
              f"  {C['dim']}({own_cited}/{total}){C['reset']}")
    print("─" * 74)

    if comp:
        print(f"\n{C['bold']}SHARE OF VOICE{C['reset']}")
        ranked = [("YOUR BRAND", len(mentioned))] + comp.most_common(12)
        ranked.sort(key=lambda kv: -kv[1])
        for i, (name, n) in enumerate(ranked, 1):
            pct = 100 * n / total
            mark = C["bold"] if name == "YOUR BRAND" else ""
            cc = sc(pct * 2)
            print(f"  {i:>2}. {mark}{name:<28}{C['reset']} {cc}{bar(min(100, pct * 2), 18)}"
                  f"{C['reset']} {n:>3} ({pct:.0f}%)")

    if domains:
        print(f"\n{C['bold']}SOURCES AI CITED MOST{C['reset']}")
        tot_d = sum(domains.values())
        for d, n in domains.most_common(12):
            own = brand_domain and brand_domain.lower().replace("www.", "") in d
            tag = f" {C['green']}← you{C['reset']}" if own else ""
            print(f"  {n:>3} ({100 * n / tot_d:>4.1f}%)  {d}{tag}")
        print(f"  {C['dim']}{len(domains)} unique domains across {tot_d} citations{C['reset']}")

    if rate < 20:
        print(f"\n{C['yellow']}Low mention rate is an off-site problem. The fix is not more "
              f"schema — it is getting into the pages AI already cites (see SOURCES above) "
              f"and building entity presence.{C['reset']}")


def generate_fixes(ctx, rep, outdir):
    """Write ready-to-deploy artifacts for the file-shaped findings.

    Only emits a file when the corresponding check actually failed, so the
    output directory is a to-do list you can deploy, not boilerplate.
    """
    os.makedirs(outdir, exist_ok=True)
    by_id = {c["id"]: c for c in rep["checks"] + rep.get("agent_checks", [])
             + rep.get("brand_checks", [])}
    failed = lambda cid: by_id.get(cid, {}).get("status") in ("fail", "warn")
    made = []
    brand = ""
    for n in has_type(ctx.nodes, "organization", "onlinestore", "localbusiness"):
        brand = first_value(n, "name")
        if brand:
            break
    brand = brand or (ctx.host.split(".")[0].title() if ctx.host else "Your Brand")
    origin = ctx.origin or "https://example.com"

    def write(name, content):
        path = os.path.join(outdir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        made.append(path)

    # 1. FAQPage schema built from the page's own question headings + answers
    if failed("faq_schema"):
        qas = []
        for i, (kind, lvl, text) in enumerate(ctx.page.blocks):
            if kind != "h" or lvl not in (2, 3) or not is_question(text):
                continue
            # Stop at the next heading so an answer never bleeds into the
            # following section.
            parts = []
            for k, _, t in ctx.page.blocks[i + 1:]:
                if k == "h":
                    break
                if k in ("p", "li"):
                    parts.append(t)
            answer = " ".join(parts)[:900].strip()
            if len(words(answer)) >= 10:
                qas.append((text, answer))
        if qas:
            write("faq-schema.json", json.dumps({
                "@context": "https://schema.org",
                "@type": "FAQPage",
                "mainEntity": [{
                    "@type": "Question", "name": q,
                    "acceptedAnswer": {"@type": "Answer", "text": a},
                } for q, a in qas],
            }, indent=2, ensure_ascii=False) + "\n")

    # 2. Article schema with the dates/author the page is missing
    if failed("schema_dates") or failed("schema_author") or failed("jsonld_present"):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        write("article-schema.json", json.dumps({
            "@context": "https://schema.org",
            "@type": "Article",
            "headline": ctx.page.title or "TODO: page title",
            "datePublished": f"TODO-original-publish-date (example {today})",
            "dateModified": today,
            "author": {"@type": "Person", "name": "TODO: author name",
                       "url": f"{origin}/pages/about"},
            "publisher": {"@type": "Organization", "name": brand, "url": origin,
                          "logo": {"@type": "ImageObject", "url": f"{origin}/logo.png"},
                          "sameAs": ["TODO: instagram", "TODO: linkedin", "TODO: facebook"]},
            "mainEntityOfPage": ctx.url,
        }, indent=2, ensure_ascii=False) + "\n")

    # 3. llms.txt seeded from the site's own internal links
    if failed("llms_txt") or failed("agent_llms"):
        seen, links = set(), []
        for href, text in ctx.page.anchors:
            if not text or len(text) < 3:
                continue
            full = urllib.parse.urljoin(ctx.url, href)
            if ctx.host and ctx.host not in full:
                continue
            key = full.split("?")[0].rstrip("/")
            if key in seen or key == origin:
                continue
            seen.add(key)
            links.append(f"- [{trunc(text, 60)}]({key})")
            if len(links) >= 40:
                break
        desc = ctx.page.meta("description") or f"{brand} website."
        write("llms.txt",
              f"# {brand}\n\n> {desc}\n\n"
              f"## Key pages\n\n" + "\n".join(links) +
              "\n\n## Notes\n\n- Content is published at "
              f"{origin} and may be quoted with attribution.\n"
              "- TODO: replace this list with your genuinely most important pages.\n")

    # 4. robots.txt block: explicit AI rules + content signals + sitemap
    if failed("robots_ai_allowed") or failed("agent_ai_rules") or \
            failed("agent_content_signals") or failed("robots_sitemap"):
        lines = ["# AI crawlers — explicit allow (append to your existing robots.txt)", ""]
        for agent, label, _ in AI_CRAWLERS:
            lines += [f"# {label}", f"User-agent: {agent}", "Allow: /", ""]
        lines += ["# Usage policy for AI systems",
                  "Content-Signal: search=yes, ai-input=yes, ai-train=no", "",
                  f"Sitemap: {origin}/sitemap.xml", ""]
        write("robots-ai-block.txt", "\n".join(lines))

    # 5. AGENTS.md
    if failed("agent_manifest"):
        write("AGENTS.md",
              f"# Agent guidance for {brand}\n\n"
              f"This file tells autonomous agents how to interact with {origin}.\n\n"
              "## Allowed\n\n- Reading and quoting published content with attribution.\n"
              "- Following links listed in /llms.txt and /sitemap.xml.\n\n"
              "## Rate limits\n\n- TODO: state your limit, e.g. 1 request/second.\n\n"
              "## Contact\n\n- TODO: email for agent operators.\n")

    # 6. MCP server card — stub, only meaningful if there is an API to expose
    if failed("agent_mcp_card"):
        write("well-known-mcp.json", json.dumps({
            "name": brand,
            "description": f"TODO: what agents can do with {brand}.",
            "version": "1.0.0",
            "endpoint": f"TODO: {origin}/mcp",
            "transport": "http",
            "tools": [{"name": "TODO_tool_name",
                       "description": "TODO: describe a callable capability."}],
        }, indent=2, ensure_ascii=False) + "\n")

    if made:
        write("README-GENERATED.md",
              "# Generated fixes\n\n"
              "Each file here corresponds to a failed check. Review the TODOs before "
              "deploying — nothing here should go live unedited.\n\n"
              "| File | Deploy to |\n|---|---|\n"
              "| `llms.txt` | site root, served as text/plain |\n"
              "| `robots-ai-block.txt` | append into your existing robots.txt |\n"
              "| `faq-schema.json` | inline in a `<script type=\"application/ld+json\">` "
              "tag on the audited page |\n"
              "| `article-schema.json` | same, replacing the page's Article JSON-LD |\n"
              "| `AGENTS.md` | site root |\n"
              "| `well-known-mcp.json` | `/.well-known/mcp.json` — only if you have an API |\n")
    return made


CONFIG_NAME = "beacon.config.json"


def load_site_config(path=None):
    """Read the project's saved site config, if there is one."""
    path = path or CONFIG_NAME
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def save_site_config(url, path=None, **extra):
    """Remember the site so later runs need no URL."""
    path = path or CONFIG_NAME
    cfg = load_site_config(path)
    cfg["site"] = url
    cfg.setdefault("brand", "")
    cfg.setdefault("competitors", [])
    for k, v in extra.items():
        if v:
            cfg[k] = v
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)
    return path


def grade(score):
    for cut, g in ((90, "A+"), (80, "A"), (70, "B"), (60, "C"), (50, "D"), (0, "F")):
        if score >= cut:
            return g
    return "F"


def trunc(s, n):
    s = normalize_ws(s)
    return s if len(s) <= n else s[:n - 1] + "…"


# ──────────────────────────────────────────────────────────────────────────
#  Sitemap crawl
# ──────────────────────────────────────────────────────────────────────────

def sitemap_urls(sitemap_url, limit=25, timeout=25, _depth=0):
    f = fetch(sitemap_url, timeout=timeout)
    if not f.ok:
        return []
    body = f.body
    urls = re.findall(r"<loc>\s*(.*?)\s*</loc>", body, re.I | re.S)
    if "<sitemapindex" in body.lower() and _depth < 2:
        collected = []
        for child in urls:
            if len(collected) >= limit:
                break
            collected.extend(sitemap_urls(child, limit - len(collected), timeout, _depth + 1))
        return collected[:limit]
    return [u for u in urls if not re.search(r"\.(jpg|jpeg|png|webp|gif|pdf|svg)$", u, re.I)][:limit]


# ──────────────────────────────────────────────────────────────────────────
#  Output
# ──────────────────────────────────────────────────────────────────────────

C = {"reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m",
     "green": "\033[32m", "yellow": "\033[33m", "red": "\033[31m",
     "cyan": "\033[36m", "grey": "\033[90m"}
if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
    C = {k: "" for k in C}

ICON = {"pass": "PASS", "warn": "WARN", "fail": "FAIL", "skip": "SKIP"}
ICON_COLOR = {"pass": C["green"], "warn": C["yellow"], "fail": C["red"], "skip": C["grey"]}


def sc(v):
    """Colour for a 0-100 score."""
    return C["green"] if v >= 80 else C["yellow"] if v >= 50 else C["red"]


def bar(pct, width=24):
    filled = round(width * pct / 100)
    return "█" * filled + "░" * (width - filled)


def print_report(rep, verbose=True):
    if rep.get("error"):
        print(f"{C['red']}✗ {rep['url']}: {rep['error']}{C['reset']}")
        return
    color = sc(rep["score"])
    ameasured = rep.get("agent_measured", rep.get("agent_score") is not None)
    acolor = sc(rep["agent_score"]) if ameasured else C["grey"]
    total = rep.get("ai_score", rep["score"])
    tcolor = sc(total)
    print()
    print(f"{C['bold']}SEO BEACON — AI VISIBILITY REPORT{C['reset']}  "
          f"{C['dim']}{rep['url']}{C['reset']}")
    print(f"{C['dim']}{trunc(rep['title'], 90)}{C['reset']}")
    print("═" * 74)
    print(f"  {tcolor}{C['bold']}VISIBILITY SCORE   {total:>3}/100{C['reset']}   "
          f"{tcolor}{rep.get('ai_level', '')}{C['reset']}")
    print("═" * 74)
    tw = rep.get("track_weights", TRACK_WEIGHTS)
    bmeasured = rep.get("brand_measured", False)
    bcolor = sc(rep["brand_score"]) if bmeasured else C["grey"]
    aval = f"{rep['agent_score']:>3}/100" if ameasured else "    n/a"
    bval = f"{rep['brand_score']:>3}/100" if bmeasured else "    n/a"
    print(f"  {C['dim']}├─ citation readiness{C['reset']}  {color}{rep['score']:>3}/100{C['reset']}"
          f"  {C['dim']}{int(tw['aeo'] * 100)}% · will AI engines cite this page{C['reset']}")
    print(f"  {C['dim']}├─ agent readiness{C['reset']}     {acolor}{aval}{C['reset']}"
          f"  {C['dim']}{int(tw['agent'] * 100)}% · can agents use this site{C['reset']}")
    print(f"  {C['dim']}└─ brand presence{C['reset']}      {bcolor}{bval}{C['reset']}"
          f"  {C['dim']}{int(tw['brand'] * 100)}% · can engines resolve you as an entity"
          f"{C['reset']}")
    print(f"  {C['dim']}{rep['word_count']} words"
          + (f" · {rep['load_seconds']}s" if rep.get("load_seconds") else "")
          + (" · offline mode" if rep["offline"] else "") + C["reset"])
    if not rep.get("network_ok", True) and not rep["offline"]:
        print(f"  {C['yellow']}! No network route to the site — crawler-access and AI-file "
              f"checks were skipped, so this score covers on-page factors only.{C['reset']}")
        print("─" * 74)
    print()
    print(f"{C['bold']}CITATION READINESS (AEO) — {int(TRACK_WEIGHTS['aeo'] * 100)}% "
          f"of score{C['reset']}")
    for c in rep["categories"]:
        print(f"  {c['label']:<24} {sc(c['pct'])}{bar(c['pct'])}{C['reset']} {c['pct']:>3}%  "
              f"{C['dim']}({c['earned']:.1f}/{c['weight']} pts){C['reset']}")

    if rep.get("agent_categories"):
        print()
        print(f"{C['bold']}AGENT READINESS — {int(TRACK_WEIGHTS['agent'] * 100)}% "
              f"of score{C['reset']}")
        for c in rep["agent_categories"]:
            print(f"  {c['label']:<24} {sc(c['pct'])}{bar(c['pct'])}{C['reset']} {c['pct']:>3}%  "
                  f"{C['dim']}({c['earned']:.1f}/{c['weight']} pts){C['reset']}")

    if rep.get("brand_categories"):
        print()
        print(f"{C['bold']}BRAND PRESENCE — {int(TRACK_WEIGHTS['brand'] * 100)}% of score"
              f"{C['reset']}")
        for c in rep["brand_categories"]:
            print(f"  {c['label']:<24} {sc(c['pct'])}{bar(c['pct'])}{C['reset']} {c['pct']:>3}%  "
                  f"{C['dim']}({c['earned']:.1f}/{c['weight']} pts){C['reset']}")

    if rep.get("engines"):
        print()
        print(f"{C['bold']}PER-ENGINE READINESS{C['reset']}")
        for e in rep["engines"]:
            print(f"  {e['engine']:<30} {sc(e['score'])}{bar(e['score'], 16)}{C['reset']} "
                  f"{e['score']:>3}  {C['dim']}{e['verdict']}{C['reset']}")

    if verbose:
        print()
        allrows = (rep["checks"] + rep.get("agent_checks", [])
                   + rep.get("brand_checks", []))
        for key, label in CATEGORIES + AGENT_CATEGORIES + BRAND_CATEGORIES:
            rows = [c for c in allrows if c["category"] == key]
            if not rows:
                continue
            print(f"{C['bold']}{C['cyan']}▸ {label}{C['reset']}")
            for c in rows:
                col = ICON_COLOR[c["status"]]
                wl = f"{c['weight']}pt" if c["weight"] else "info"
                print(f"   {col}{ICON[c['status']]:<4}{C['reset']} {c['title']} "
                      f"{C['dim']}[{wl}]{C['reset']}")
                print(f"        {C['dim']}{c['detail']}{C['reset']}")
            print()

    print(f"{C['bold']}TOP FIXES (by score impact){C['reset']}")
    if not rep["priority_fixes"]:
        print(f"  {C['green']}Nothing to fix — every check passed.{C['reset']}")
    for i, f in enumerate(rep["priority_fixes"][:8], 1):
        print(f"  {C['bold']}{i}. +{f['impact']} pts{C['reset']} — {f['title']} "
              f"{C['dim']}({f['category']}){C['reset']}")
        print(f"     {f['detail']}")
        if f["fix"]:
            print(f"     {C['cyan']}→ {f['fix']}{C['reset']}")
    print()


def print_history(path, reps):
    """Append this run to a CSV and report movement since the previous run."""
    import csv
    rows = []
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    new = []
    for r in reps:
        if r.get("error"):
            continue
        new.append({"date": stamp, "url": r["url"],
                    "ai_score": r.get("ai_score", r.get("score", 0)),
                    "aeo_score": r.get("score", 0),
                    "agent_score": r.get("agent_score") if r.get("agent_measured") else "",
                    "brand_score": r.get("brand_score") if r.get("brand_measured") else "",
                    "grade": r.get("grade", "")})
    if not new:
        return

    fields = ["date", "url", "ai_score", "aeo_score", "agent_score", "brand_score", "grade"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for row in rows + new:
            w.writerow({k: row.get(k, "") for k in fields})

    print()
    print(f"{C['bold']}SCORE HISTORY{C['reset']}  {C['dim']}{path}{C['reset']}")
    for row in new:
        prev = [x for x in rows if x.get("url") == row["url"]]
        if not prev:
            print(f"  {row['ai_score']:>3}/100  {C['dim']}first recorded run for "
                  f"{trunc(row['url'], 55)}{C['reset']}")
            continue
        last = prev[-1]
        try:
            delta = int(row["ai_score"]) - int(last["ai_score"])
        except (ValueError, TypeError):
            continue
        if delta > 0:
            mark = f"{C['green']}+{delta} since {last['date']}{C['reset']}"
        elif delta < 0:
            mark = f"{C['red']}{delta} since {last['date']}{C['reset']}"
        else:
            mark = f"{C['dim']}no change since {last['date']}{C['reset']}"
        print(f"  {row['ai_score']:>3}/100  {mark}  {C['dim']}{trunc(row['url'], 45)}{C['reset']}")
    print(f"  {C['dim']}{len(rows) + len(new)} runs recorded{C['reset']}")


def print_summary_table(reps):
    print()
    print(f"{C['bold']}SITE AEO SUMMARY{C['reset']}")
    print("─" * 96)
    print(f"  {'SCORE':<7}{'GRADE':<7}{'URL'}")
    print("─" * 96)
    ok = [r for r in reps if not r.get("error")]
    for r in reps:
        r.setdefault("ai_score", r.get("score", 0))
    for r in sorted(reps, key=lambda x: x.get("ai_score", -1)):
        if r.get("error"):
            print(f"  {C['red']}{'ERR':<7}{'-':<7}{trunc(r['url'], 78)}  ({r['error']}){C['reset']}")
            continue
        v = r["ai_score"]
        col = C["green"] if v >= 80 else C["yellow"] if v >= 50 else C["red"]
        print(f"  {col}{v:<7}{r['grade']:<7}{C['reset']}{trunc(r['url'], 78)}")
    print("─" * 96)
    if ok:
        avg = sum(r["ai_score"] for r in ok) / len(ok)
        print(f"  {C['bold']}Average {avg:.1f}/100 across {len(ok)} pages{C['reset']}")
        agg = {}
        for r in ok:
            for c in r["checks"]:
                if c["status"] in ("fail", "warn"):
                    k = (c["title"], c["category_label"])
                    agg[k] = agg.get(k, 0) + c["weight"] * (1 - c["score"])
        print(f"\n{C['bold']}SITE-WIDE PRIORITIES{C['reset']}")
        for (title, cat), impact in sorted(agg.items(), key=lambda kv: -kv[1])[:10]:
            print(f"  {C['bold']}{impact / len(ok):>5.1f} pts/page{C['reset']}  {title} "
                  f"{C['dim']}({cat}){C['reset']}")
    print()


HTML_TMPL = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SEO Beacon — {host}</title><style>
*{{box-sizing:border-box}}body{{margin:0;padding:40px 20px;font:15px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',Inter,sans-serif;background:#0d1117;color:#e6edf3}}
.wrap{{max-width:900px;margin:0 auto}}h1{{font-size:22px;margin:0 0 4px}}
a{{color:#58a6ff}}.sub{{color:#8b949e;font-size:13px;margin-bottom:28px;word-break:break-all}}
.hero{{display:flex;gap:28px;align-items:center;background:#161b22;border:1px solid #30363d;border-radius:14px;padding:24px;margin-bottom:28px}}
.ring{{flex:0 0 132px}}.big{{font-size:44px;font-weight:700;fill:{color}}}.gsm{{font-size:13px;fill:#8b949e}}
.rlab{{text-align:center;font-size:11px;color:#8b949e;margin-top:-6px}}
.eng{{display:flex;align-items:center;gap:12px;padding:8px 0;border-bottom:1px solid #21262d;font-size:13px}}
.eng .n{{flex:0 0 200px;font-weight:600}}.eng .t{{flex:1;height:8px;background:#21262d;border-radius:5px;overflow:hidden}}
.eng .s{{flex:0 0 34px;text-align:right;font-weight:700}}.eng .v{{flex:0 0 210px;color:#8b949e}}
.meta{{color:#8b949e;font-size:13px}}.meta b{{color:#e6edf3;font-weight:600}}
.cat{{margin-bottom:12px}}.catrow{{display:flex;justify-content:space-between;font-size:13px;margin-bottom:5px}}
.track{{height:8px;background:#21262d;border-radius:5px;overflow:hidden}}.fill{{height:100%;border-radius:5px}}
h2{{font-size:14px;text-transform:uppercase;letter-spacing:.08em;color:#8b949e;margin:34px 0 12px;border-bottom:1px solid #30363d;padding-bottom:8px}}
.chk{{display:flex;gap:12px;padding:11px 0;border-bottom:1px solid #21262d}}
.tag{{flex:0 0 46px;font-size:10px;font-weight:700;letter-spacing:.05em;padding:3px 0;text-align:center;border-radius:4px;height:20px}}
.pass{{background:#12331f;color:#3fb950}}.warn{{background:#3a2d10;color:#d29922}}.fail{{background:#3d1418;color:#f85149}}.skip{{background:#21262d;color:#8b949e}}
.ct{{font-weight:600;font-size:14px}}.cd{{color:#8b949e;font-size:13px;margin-top:2px}}
.cf{{color:#58a6ff;font-size:13px;margin-top:4px}}.pt{{color:#6e7681;font-size:11px;margin-left:6px}}
.fix{{background:#161b22;border:1px solid #30363d;border-left:3px solid #58a6ff;border-radius:8px;padding:14px 16px;margin-bottom:10px}}
.imp{{color:#3fb950;font-weight:700;font-size:13px}}
footer{{color:#6e7681;font-size:12px;margin-top:40px;text-align:center}}
@media(prefers-color-scheme:light){{body{{background:#fff;color:#1f2328}}.hero,.fix{{background:#f6f8fa;border-color:#d1d9e0}}.track{{background:#eaeef2}}.chk{{border-color:#eaeef2}}.gsm,.meta,.cd{{color:#59636e}}}}
</style></head><body><div class="wrap">
<h1>SEO Beacon — {host}</h1>
<div class="sub"><a href="{url}">{url}</a> · {fetched}</div>
<div class="hero">
<div class="ring"><svg viewBox="0 0 120 120" width="132" height="132">
<circle cx="60" cy="60" r="52" fill="none" stroke="#21262d" stroke-width="11"/>
<circle cx="60" cy="60" r="52" fill="none" stroke="{color}" stroke-width="11" stroke-linecap="round"
 stroke-dasharray="{dash} 999" transform="rotate(-90 60 60)"/>
<text x="60" y="64" text-anchor="middle" class="big">{aiscore}</text>
<text x="60" y="84" text-anchor="middle" class="gsm">/100 · AEO {score} ({grade})</text></svg>
<div class="rlab">AI Readiness · {ailevel}</div></div>
<div style="flex:1">{catbars}</div></div>
<div class="hero">
<div class="ring"><svg viewBox="0 0 120 120" width="132" height="132">
<circle cx="60" cy="60" r="52" fill="none" stroke="#21262d" stroke-width="11"/>
<circle cx="60" cy="60" r="52" fill="none" stroke="{acolor}" stroke-width="11" stroke-linecap="round"
 stroke-dasharray="{adash} 999" transform="rotate(-90 60 60)"/>
<text x="60" y="64" text-anchor="middle" class="big" style="fill:{acolor}">{ascore}</text>
<text x="60" y="84" text-anchor="middle" class="gsm">{alevel}</text></svg>
<div class="rlab">Agent readiness</div></div>
<div style="flex:1">{acatbars}</div></div>
<div class="hero">
<div class="ring"><svg viewBox="0 0 120 120" width="132" height="132">
<circle cx="60" cy="60" r="52" fill="none" stroke="#21262d" stroke-width="11"/>
<circle cx="60" cy="60" r="52" fill="none" stroke="{bcolor}" stroke-width="11" stroke-linecap="round"
 stroke-dasharray="{bdash} 999" transform="rotate(-90 60 60)"/>
<text x="60" y="64" text-anchor="middle" class="big" style="fill:{bcolor}">{bscore}</text>
<text x="60" y="84" text-anchor="middle" class="gsm">brand</text></svg>
<div class="rlab">Brand presence</div></div>
<div style="flex:1">{bcatbars}</div></div>
<h2>Per-engine readiness</h2>{engines}
<div class="meta"><b>{words}</b> words · title: {title}</div>
<h2>Top fixes</h2>{fixes}
{sections}
<footer>Generated by seo_beacon.py v{version}</footer>
</div></body></html>"""


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def write_html(rep, path):
    _t = rep.get("ai_score", rep["score"])
    color = "#3fb950" if _t >= 80 else "#d29922" if _t >= 50 else "#f85149"
    catbars = ""
    for c in rep["categories"]:
        cc = "#3fb950" if c["pct"] >= 80 else "#d29922" if c["pct"] >= 50 else "#f85149"
        catbars += (f'<div class="cat"><div class="catrow"><span>{esc(c["label"])}</span>'
                    f'<span>{c["pct"]}% <span class="pt">{c["earned"]:.1f}/{c["weight"]}pt</span>'
                    f'</span></div><div class="track"><div class="fill" style="width:{c["pct"]}%;'
                    f'background:{cc}"></div></div></div>')
    fixes = "".join(
        f'<div class="fix"><span class="imp">+{f["impact"]} pts</span> &nbsp;<b>{esc(f["title"])}</b>'
        f'<div class="cd">{esc(f["detail"])}</div>'
        + (f'<div class="cf">&rarr; {esc(f["fix"])}</div>' if f["fix"] else "")
        + "</div>" for f in rep["priority_fixes"]) or "<p>Everything passed.</p>"

    measured = rep.get("agent_measured", False)
    ascore_v = rep.get("agent_score") or 0
    acolor = ("#3fb950" if ascore_v >= 80 else "#d29922" if ascore_v >= 50 else "#f85149") \
        if measured else "#6e7681"
    acatbars = ""
    for c in rep.get("agent_categories", []):
        cc = "#3fb950" if c["pct"] >= 80 else "#d29922" if c["pct"] >= 50 else "#f85149"
        acatbars += (f'<div class="cat"><div class="catrow"><span>{esc(c["label"])}</span>'
                     f'<span>{c["pct"]}% <span class="pt">{c["earned"]:.1f}/{c["weight"]}pt</span>'
                     f'</span></div><div class="track"><div class="fill" style="width:{c["pct"]}%;'
                     f'background:{cc}"></div></div></div>')
    if not measured:
        acatbars = ('<p class="cd">Agent-readiness probes need network access to the site; '
                    'they were skipped for this run.</p>')
    bmeasured = rep.get("brand_measured", False)
    bscore_v = rep.get("brand_score") or 0
    bcolor = ("#3fb950" if bscore_v >= 80 else "#d29922" if bscore_v >= 50 else "#f85149") \
        if bmeasured else "#6e7681"
    bcatbars = ""
    for c in rep.get("brand_categories", []):
        cc = "#3fb950" if c["pct"] >= 80 else "#d29922" if c["pct"] >= 50 else "#f85149"
        bcatbars += (f'<div class="cat"><div class="catrow"><span>{esc(c["label"])}</span>'
                     f'<span>{c["pct"]}% <span class="pt">{c["earned"]:.1f}/{c["weight"]}pt</span>'
                     f'</span></div><div class="track"><div class="fill" style="width:{c["pct"]}%;'
                     f'background:{cc}"></div></div></div>')
    if not bmeasured:
        bcatbars = ('<p class="cd">Brand-presence lookups need network access to '
                    'Wikipedia, Wikidata and Reddit; they were skipped for this run.</p>')

    engines = ""
    for e in rep.get("engines", []):
        ec = "#3fb950" if e["score"] >= 80 else "#d29922" if e["score"] >= 50 else "#f85149"
        engines += (f'<div class="eng"><div class="n">{esc(e["engine"])}</div>'
                    f'<div class="t"><div class="fill" style="width:{e["score"]}%;height:100%;'
                    f'background:{ec};border-radius:5px"></div></div>'
                    f'<div class="s" style="color:{ec}">{e["score"]}</div>'
                    f'<div class="v">{esc(e["verdict"])}</div></div>')

    sections = ""
    for key, label in CATEGORIES + AGENT_CATEGORIES + BRAND_CATEGORIES:
        rows = [c for c in rep["checks"] + rep.get("agent_checks", [])
                + rep.get("brand_checks", []) if c["category"] == key]
        if not rows:
            continue
        sections += f"<h2>{esc(label)}</h2>"
        for c in rows:
            sections += (f'<div class="chk"><div class="tag {c["status"]}">{c["status"].upper()}</div>'
                         f'<div><div class="ct">{esc(c["title"])}'
                         f'<span class="pt">{c["weight"]}pt</span></div>'
                         f'<div class="cd">{esc(c["detail"])}</div>'
                         + (f'<div class="cf">&rarr; {esc(c["fix"])}</div>'
                            if c["status"] not in ("pass", "skip") and c["fix"] else "")
                         + "</div></div>")

    html = HTML_TMPL.format(
        host=esc(urllib.parse.urlsplit(rep["url"]).netloc or rep["url"]),
        url=esc(rep["url"]), fetched=esc(rep["fetched_at"]), color=color,
        dash=round(2 * 3.14159 * 52 * rep.get("ai_score", rep["score"]) / 100, 1),
        score=rep["score"], aiscore=rep.get("ai_score", rep["score"]),
        ailevel=esc(rep.get("ai_level", "")),
        bcolor=bcolor, bdash=round(2 * 3.14159 * 52 * bscore_v / 100, 1),
        bscore=(bscore_v if bmeasured else "n/a"), bcatbars=bcatbars,
        grade=esc(rep["grade"]), catbars=catbars, fixes=fixes, sections=sections,
        acolor=acolor, adash=round(2 * 3.14159 * 52 * ascore_v / 100, 1),
        ascore=(ascore_v if measured else "n/a"),
        alevel=esc(rep.get("agent_level", "")), acatbars=acatbars, engines=engines,
        words=rep["word_count"], title=esc(trunc(rep["title"], 100)), version=VERSION)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return path


# ──────────────────────────────────────────────────────────────────────────
#  CLI
# ──────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="SEO Beacon — can AI find you, cite you, and act on your site?",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("url", nargs="?", help="page URL to audit")
    ap.add_argument("--file", help="audit a local HTML file instead of fetching")
    ap.add_argument("--base-url", default="", help="URL the local file represents")
    ap.add_argument("--sitemap", help="audit pages listed in this sitemap.xml")
    ap.add_argument("--limit", type=int, default=15, help="max pages in sitemap mode (default 15)")
    ap.add_argument("--workers", type=int, default=4, help="parallel page fetches (default 4)")
    ap.add_argument("--timeout", type=int, default=25)
    ap.add_argument("--json", dest="json_out", help="write results to a JSON file")
    ap.add_argument("--html", dest="html_out", help="write an HTML report")
    ap.add_argument("--quiet", action="store_true", help="score + top fixes only")
    ap.add_argument("--save-site", action="store_true",
                    help=f"remember this URL in {CONFIG_NAME} for future runs")
    ap.add_argument("--config", metavar="JSON",
                    help=f"path to a site config (default: ./{CONFIG_NAME})")
    ap.add_argument("--init-prompts", metavar="JSON",
                    help="write a starter prompt-tracking config for this site")
    ap.add_argument("--prompt-report", metavar="CSV",
                    help="aggregate prompt-run results: mention rate, share of voice, sources")
    ap.add_argument("--tasks", nargs="?", const=True, metavar="CSV",
                    help="print a prioritised task board; give a path to also write CSV")
    ap.add_argument("--history", metavar="CSV",
                    help="append this run to a CSV and print the change since last time")
    ap.add_argument("--generate", metavar="DIR",
                    help="write ready-to-deploy fix files (llms.txt, FAQ schema, "
                         "robots block, AGENTS.md) for every failed check")
    ap.add_argument("--fail-under", type=int, default=0,
                    help="exit 1 if score is below this (for CI)")
    args = ap.parse_args()

    # No target given? Fall back to the site this project has already been
    # pointed at, so repeat runs are a bare `seo_beacon.py`.
    cfg = load_site_config(args.config)
    if not (args.url or args.file or args.sitemap or args.prompt_report):
        if cfg.get("site"):
            args.url = cfg["site"]
            print(f"{C['dim']}Auditing saved site: {args.url}  "
                  f"({args.config or CONFIG_NAME}){C['reset']}")
        else:
            ap.print_help()
            print(f"\n{C['yellow']}No URL given and no {CONFIG_NAME} found. "
                  f"Which website should this audit run for?{C['reset']}")
            print(f"{C['dim']}  seo_beacon.py https://your-site.com/ --save-site"
                  f"{C['reset']}")
            return 2

    if args.prompt_report:
        prompt_report(args.prompt_report, args.url or cfg.get("site", ""))
        return 0

    if args.file:
        with open(args.file, encoding="utf-8", errors="replace") as fh:
            html = fh.read()
        rep = audit(args.base_url or f"file://{os.path.abspath(args.file)}",
                    html=html, offline=not args.base_url, generate_dir=args.generate)
        reports = [rep]
        print_report(rep, verbose=not args.quiet)

    elif args.sitemap:
        urls = sitemap_urls(args.sitemap, args.limit, args.timeout)
        if not urls:
            print(f"{C['red']}No URLs found in {args.sitemap}{C['reset']}")
            return 2
        print(f"{C['dim']}Auditing {len(urls)} pages from {args.sitemap}…{C['reset']}")
        reports = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(audit, u, timeout=args.timeout): u for u in urls}
            for fu in concurrent.futures.as_completed(futs):
                try:
                    reports.append(fu.result())
                except Exception as e:
                    reports.append({"url": futs[fu], "error": str(e), "score": 0})
                print(f"{C['dim']}  {len(reports)}/{len(urls)} done{C['reset']}", end="\r")
        print_summary_table(reports)
        if not args.quiet and reports:
            worst = min((r for r in reports if not r.get("error")),
                        key=lambda r: r["score"], default=None)
            if worst:
                print(f"{C['bold']}Detail for lowest-scoring page:{C['reset']}")
                print_report(worst, verbose=True)
        rep = reports[0]

    elif args.url:
        rep = audit(args.url, timeout=args.timeout, generate_dir=args.generate)
        reports = [rep]
        print_report(rep, verbose=not args.quiet)

    else:
        ap.print_help()
        return 2

    if args.save_site and args.url and not rep.get("error"):
        path = save_site_config(args.url, args.config, brand=rep.get("brand_name", ""))
        print(f"{C['dim']}Site saved → {path}{C['reset']}")

    if args.init_prompts and not rep.get("error"):
        try:
            init_prompts(_last_ctx[0], rep, args.init_prompts)
            print(f"{C['dim']}Prompt config → {args.init_prompts}{C['reset']}")
        except Exception as e:
            print(f"{C['red']}Could not write prompt config: {e}{C['reset']}")

    if args.tasks and not rep.get("error"):
        tasks, skipped_tracks = build_tasks(rep)
        print_tasks(tasks, skipped_tracks)
        if isinstance(args.tasks, str):
            write_tasks(tasks, args.tasks)
            print(f"{C['dim']}Tasks → {args.tasks}{C['reset']}")

    if args.history and not rep.get("error"):
        print_history(args.history, reports if len(reports) > 1 else [rep])

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(reports if len(reports) > 1 else rep, fh, indent=2)
        print(f"{C['dim']}JSON → {args.json_out}{C['reset']}")

    if rep.get("generated"):
        print(f"{C['bold']}GENERATED FIXES{C['reset']}  {C['dim']}({len(rep['generated'])} "
              f"files in {args.generate}/){C['reset']}")
        for path in rep["generated"]:
            print(f"  {C['green']}+{C['reset']} {path}")
        print(f"  {C['dim']}Review the TODOs before deploying.{C['reset']}")

    if args.html_out and not rep.get("error"):
        write_html(rep, args.html_out)
        print(f"{C['dim']}HTML → {args.html_out}{C['reset']}")

    if args.fail_under and rep.get("ai_score", rep.get("score", 0)) < args.fail_under:
        print(f"{C['red']}Score {rep.get('ai_score')} is below --fail-under "
              f"{args.fail_under}{C['reset']}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
