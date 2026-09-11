"""Web reach: search and page fetching, with no API key and no third-party SDK.

Both tools are deliberately honest about their limits — a search that returns nothing
says so, and a page Agent could not parse returns the reason rather than an empty string
the model would then narrate as "the page was empty".
"""

from __future__ import annotations

import re
import urllib.parse
from html.parser import HTMLParser

import httpx

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_SKIP_TAGS = {"script", "style", "noscript", "svg", "canvas", "iframe", "form", "nav",
              "footer", "header", "aside"}
_BLOCK_TAGS = {"p", "div", "section", "article", "br", "li", "tr", "h1", "h2", "h3", "h4",
               "h5", "h6", "blockquote", "pre", "table", "ul", "ol", "dl", "dd", "dt"}
_HEADINGS = {"h1": "# ", "h2": "## ", "h3": "### ", "h4": "#### ", "h5": "##### ", "h6": "###### "}


class _Extractor(HTMLParser):
    """HTML to readable text, stdlib only.

    Not a full readability implementation: it drops chrome-ish containers, keeps heading
    levels as markdown so structure survives, and collapses the rest. That is enough for
    a model to read a page, and it adds no dependency to install or keep current.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False
        self._pending_heading = ""

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in _HEADINGS:
            self.parts.append("\n\n")
            self._pending_heading = _HEADINGS[tag]
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")
        elif tag == "a":
            href = dict(attrs).get("href") or ""
            if href.startswith("http") and len(href) < 300:
                self._pending_link = href

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag == "title":
            self._in_title = False
        elif tag in _BLOCK_TAGS or tag in _HEADINGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data.strip()
            return
        if self._skip:
            return
        text = data.strip()
        if not text:
            return
        if self._pending_heading:
            self.parts.append(self._pending_heading)
            self._pending_heading = ""
        self.parts.append(text + " ")

    def text(self) -> str:
        raw = "".join(self.parts)
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r"\n[ \t]+", "\n", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def html_to_text(html: str) -> tuple[str, str]:
    parser = _Extractor()
    try:
        parser.feed(html)
    except Exception:
        pass  # a malformed page still yields whatever was parsed before the break
    return parser.text(), parser.title


async def fetch_url(url: str, *, max_bytes: int, timeout_s: int) -> dict:
    """Fetch one URL and return readable text.

    Non-HTML content is returned as-is when it is textual (JSON, CSV, markdown) and
    described rather than invented when it is binary.
    """
    if not re.match(r"^https?://", url or "", re.I):
        return {"ok": False, "error": f"'{url}' is not an http(s) URL."}
    try:
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True,
                                     headers={"User-Agent": UA}) as client:
            resp = await client.get(url)
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"Could not reach {url}: {type(exc).__name__}: {exc}"}
    if resp.status_code >= 400:
        return {"ok": False, "error": f"HTTP {resp.status_code} from {url}"}

    ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
    raw = resp.content[:max_bytes]
    truncated = len(resp.content) > max_bytes
    if ctype in ("text/html", "application/xhtml+xml") or (not ctype and b"<html" in raw[:2000].lower()):
        text, title = html_to_text(raw.decode(resp.encoding or "utf-8", errors="replace"))
    elif ctype.startswith("text/") or ctype in ("application/json", "application/xml",
                                                "application/javascript", "application/csv"):
        text, title = raw.decode(resp.encoding or "utf-8", errors="replace"), ""
    else:
        return {"ok": True, "url": str(resp.url), "title": "", "content_type": ctype,
                "text": f"[{ctype or 'binary'} content, {len(resp.content)} bytes — not text, "
                        f"so there is nothing to read here]"}
    return {"ok": True, "url": str(resp.url), "title": title, "content_type": ctype,
            "text": text + ("\n\n[truncated]" if truncated else "")}


def _unwrap_ddg(href: str) -> str:
    """DuckDuckGo wraps results in a redirect; the real URL is the `uddg` parameter."""
    if "duckduckgo.com/l/" in href or href.startswith("//duckduckgo.com/l/"):
        query = urllib.parse.urlparse(href if href.startswith("http") else "https:" + href).query
        target = urllib.parse.parse_qs(query).get("uddg")
        if target:
            return target[0]
    return "https:" + href if href.startswith("//") else href


async def web_search(query: str, *, max_results: int = 6, timeout_s: int = 30) -> dict:
    """Search the web through DuckDuckGo's keyless HTML endpoint.

    No API key to manage and nothing to sign up for, which is what makes a freshly
    installed Agent able to look something up. If the endpoint changes shape or blocks
    the request, this reports that plainly instead of returning an empty result set the
    model would read as "nothing exists about this".
    """
    if not (query or "").strip():
        return {"ok": False, "error": "Empty search query."}
    try:
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True,
                                     headers={"User-Agent": UA}) as client:
            resp = await client.post("https://html.duckduckgo.com/html/",
                                     data={"q": query, "kl": "wt-wt"})
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"Search unreachable: {type(exc).__name__}: {exc}"}
    if resp.status_code >= 400:
        return {"ok": False, "error": f"Search endpoint returned HTTP {resp.status_code}."}

    html = resp.text
    results: list[dict] = []
    seen: set[str] = set()
    pattern = re.compile(
        r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S | re.I)
    snippet_pattern = re.compile(
        r'<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>', re.S | re.I)
    snippets = [re.sub(r"<[^>]+>", "", s).strip() for s in snippet_pattern.findall(html)]
    for index, (href, title_html) in enumerate(pattern.findall(html)):
        url = _unwrap_ddg(href)
        if not url.startswith("http") or url in seen:
            continue
        seen.add(url)
        title = re.sub(r"<[^>]+>", "", title_html)
        title = re.sub(r"\s+", " ", title).strip()
        results.append({"title": title, "url": url,
                        "snippet": snippets[index] if index < len(snippets) else ""})
        if len(results) >= max_results:
            break
    if not results:
        blocked = "anomaly" in html.lower() or "captcha" in html.lower()
        return {"ok": False,
                "error": ("DuckDuckGo answered but returned no parsable results"
                          + (" — the request looks rate-limited or challenged." if blocked
                             else " for this query."))}
    return {"ok": True, "query": query, "results": results}
