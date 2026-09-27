"""A PDF the reader can forward: findings first, charts and tables inline, sources at the end.

The report is assembled from things that already exist — charts drawn in the conversation,
rows returned by calls — never from numbers retyped into it. Each chart is rendered from its
stored Vega-Lite spec by the same engine that drew it on screen, so what the reader
approved in the app is what goes on paper. Citations like [#4] become numbered sources in
an appendix that says which query produced which figure: a report that cannot say where
its numbers came from is a report nobody should act on.
"""

from __future__ import annotations

import datetime as dt
import io
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import (Image, KeepTogether, ListFlowable, ListItem, Paragraph,
                                SimpleDocTemplate, Spacer, Table, TableStyle)

INK = colors.HexColor("#18181B")
BODY = colors.HexColor("#3F3F46")
MUTED = colors.HexColor("#71717A")
RULE = colors.HexColor("#E4E4E7")
ZEBRA = colors.HexColor("#F7F8F8")
HEAD_FILL = colors.HexColor("#EEF2F0")
BRAND = colors.HexColor("#00A383")

MAX_TABLE_ROWS = 40

_FONT_SETS = [
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
     "/System/Library/Fonts/Supplemental/Arial Italic.ttf", "/System/Library/Fonts/Supplemental/Arial Bold Italic.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf"),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf",
     "C:/Windows/Fonts/ariali.ttf", "C:/Windows/Fonts/arialbi.ttf"),
]
_FONTS: dict[str, str] | None = None

# What a missing glyph becomes, rather than a black box in someone's board pack.
_FALLBACK = {"≈": "~", "→": "->", "←": "<-", "∈": "in", "≤": "<=", "≥": ">=", "✓": "v",
             "✗": "x", "×": "x", "−": "-", "…": "...", "\u202f": " ", "\u00a0": " ", "‑": "-"}


def fonts() -> dict[str, str]:
    global _FONTS
    if _FONTS is not None:
        return _FONTS
    for regular, bold, italic, bold_italic in _FONT_SETS:
        if all(Path(p).exists() for p in (regular, bold, italic, bold_italic)):
            try:
                pdfmetrics.registerFont(TTFont("Body", regular))
                pdfmetrics.registerFont(TTFont("Body-Bold", bold))
                pdfmetrics.registerFont(TTFont("Body-Italic", italic))
                pdfmetrics.registerFont(TTFont("Body-BoldItalic", bold_italic))
                pdfmetrics.registerFontFamily("Body", normal="Body", bold="Body-Bold",
                                              italic="Body-Italic", boldItalic="Body-BoldItalic")
                _FONTS = {"regular": "Body", "bold": "Body-Bold", "italic": "Body-Italic"}
                return _FONTS
            except Exception:  # noqa: BLE001 - a broken font file falls through to the next
                continue
    _FONTS = {"regular": "Helvetica", "bold": "Helvetica-Bold", "italic": "Helvetica-Oblique"}
    return _FONTS


def _printable(text: str) -> str:
    font = fonts()["regular"]
    face = getattr(pdfmetrics.getFont(font), "face", None)
    glyphs = getattr(face, "charToGlyph", None)
    out = []
    for char in text:
        if glyphs is not None and ord(char) not in glyphs and char not in "\n\t":
            out.append(_FALLBACK.get(char, "?" if ord(char) > 0x2000 else char))
        elif glyphs is None and ord(char) > 0xFF and char not in "€—–‘’“”•…":
            out.append(_FALLBACK.get(char, "?"))
        else:
            out.append(char)
    return "".join(out)


def _styles() -> dict[str, ParagraphStyle]:
    f = fonts()
    return {
        "brand": ParagraphStyle("brand", fontName=f["bold"], fontSize=8, textColor=BRAND,
                                leading=10, spaceAfter=0),
        "date": ParagraphStyle("date", fontName=f["regular"], fontSize=8, textColor=MUTED,
                               leading=10, alignment=TA_RIGHT),
        "title": ParagraphStyle("title", fontName=f["bold"], fontSize=21, textColor=INK,
                                leading=26, spaceBefore=6, spaceAfter=4),
        "subtitle": ParagraphStyle("subtitle", fontName=f["regular"], fontSize=11.5,
                                   textColor=MUTED, leading=15, spaceAfter=10),
        "h2": ParagraphStyle("h2", fontName=f["bold"], fontSize=13, textColor=INK, leading=17,
                             spaceBefore=14, spaceAfter=6),
        "h3": ParagraphStyle("h3", fontName=f["bold"], fontSize=11, textColor=INK, leading=15,
                             spaceBefore=10, spaceAfter=4),
        "body": ParagraphStyle("body", fontName=f["regular"], fontSize=10, textColor=BODY,
                               leading=14.6, spaceAfter=6),
        "bullet": ParagraphStyle("bullet", fontName=f["regular"], fontSize=10, textColor=BODY,
                                 leading=14.2),
        "caption": ParagraphStyle("caption", fontName=f["regular"], fontSize=8.2, textColor=MUTED,
                                  leading=11, spaceBefore=3, spaceAfter=10),
        "cell": ParagraphStyle("cell", fontName=f["regular"], fontSize=8.6, textColor=BODY,
                               leading=11),
        "cell_num": ParagraphStyle("cell_num", fontName=f["regular"], fontSize=8.6,
                                   textColor=BODY, leading=11, alignment=TA_RIGHT),
        "cell_head": ParagraphStyle("cell_head", fontName=f["bold"], fontSize=8.6,
                                    textColor=INK, leading=11),
        "cell_head_num": ParagraphStyle("cell_head_num", fontName=f["bold"], fontSize=8.6,
                                        textColor=INK, leading=11, alignment=TA_RIGHT),
        "source": ParagraphStyle("source", fontName=f["regular"], fontSize=8.4, textColor=BODY,
                                 leading=11.5, spaceAfter=3),
    }


# ------------------------------------------------------------------ markdown

_CITE = re.compile(r"[\[【]#(\d{1,3})[\]】]")


def _inline(text: str, cited: list[int]) -> str:
    """Markdown inline spans to reportlab's mini-markup, citations to superscripts."""
    text = _printable(text)
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def cite(match: re.Match) -> str:
        number = int(match.group(1))
        if number not in cited:
            cited.append(number)
        return f'<super><font color="#71717A" size="6.5">{number}</font></super>'

    text = re.sub(r"`\s*" + _CITE.pattern + r"\s*`", cite, text)
    text = _CITE.sub(cite, text)
    text = re.sub(r"`([^`]+)`", r'<font face="Courier" size="8.8">\1</font>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__([^_]+)__", r"<b>\1</b>", text)
    text = re.sub(r"(?<![*\w])\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
                  r'<link href="\2" color="#00A383">\1</link>', text)
    return text


def markdown_flowables(text: str, styles: dict, cited: list[int]) -> list:
    out: list = []
    lines = (text or "").replace("\r", "").split("\n")
    paragraph: list[str] = []
    bullets: list[str] = []
    ordered = False
    i = 0

    def flush_paragraph() -> None:
        if paragraph:
            out.append(Paragraph(_inline(" ".join(paragraph), cited), styles["body"]))
            paragraph.clear()

    def flush_bullets() -> None:
        nonlocal ordered
        if bullets:
            items = [ListItem(Paragraph(_inline(b, cited), styles["bullet"]), leftIndent=12)
                     for b in bullets]
            out.append(ListFlowable(items, bulletType="1" if ordered else "bullet",
                                    start="1" if ordered else "•", leftIndent=14,
                                    bulletFontSize=8 if not ordered else 9.5,
                                    bulletColor=MUTED, spaceAfter=6))
            bullets.clear()
            ordered = False

    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()
        if stripped.startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1]):
            flush_paragraph(); flush_bullets()
            block = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append(lines[i]); i += 1
            split = lambda l: [c.strip() for c in l.strip().strip("|").split("|")]
            header = split(block[0])
            rows = [dict(zip(header, split(l))) for l in block[2:]]
            out.extend(table_flowables(rows, styles, cited=cited))
            continue
        heading = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        bullet = re.match(r"^[-*•]\s+(.*)$", stripped)
        number = re.match(r"^\d+[.)]\s+(.*)$", stripped)
        if heading:
            flush_paragraph(); flush_bullets()
            level = "h2" if len(heading.group(1)) <= 2 else "h3"
            out.append(Paragraph(_inline(heading.group(2), cited), styles[level]))
        elif bullet or number:
            flush_paragraph()
            if number and not bullets:
                ordered = True
            bullets.append((bullet or number).group(1))
        elif not stripped:
            flush_paragraph(); flush_bullets()
        elif stripped.startswith("---"):
            flush_paragraph(); flush_bullets()
        else:
            if bullets:
                flush_bullets()
            paragraph.append(stripped)
        i += 1
    flush_paragraph(); flush_bullets()
    return out


# -------------------------------------------------------------------- tables

def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _format(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:,.2f}"
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value:,}" if abs(value) >= 10000 else str(value)
    return str(value)


def _header(column: str) -> str:
    """`revenue_eur` → `Revenue eur`: a column name as a reader would say it."""
    text = str(column).replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else text


def table_flowables(rows: list[dict], styles: dict, *, title: str = "", source: str = "",
                    columns: list[str] | None = None, cited: list[int] | None = None,
                    width: float = 174 * mm) -> list:
    if not rows:
        return [Paragraph("<i>No rows.</i>", styles["caption"])]
    columns = columns or list(dict.fromkeys(k for r in rows[:200] for k in r))
    shown = rows[:MAX_TABLE_ROWS]
    numeric = {c: all(_is_number(r.get(c)) or r.get(c) in (None, "") for r in shown)
               and any(_is_number(r.get(c)) for r in shown) for c in columns}
    head = [Paragraph(_inline(_header(c), cited or []),
                      styles["cell_head_num" if numeric[c] else "cell_head"]) for c in columns]
    body = [[Paragraph(_inline(_format(r.get(c)), cited or []),
                       styles["cell_num" if numeric[c] else "cell"]) for c in columns]
            for r in shown]
    # Width by content, bounded: a name column gets room, a two-digit count does not.
    weights = []
    for c in columns:
        longest = max([len(str(c))] + [len(_format(r.get(c))) for r in shown])
        weights.append(min(max(longest, 4), 38))
    total = sum(weights)
    widths = [width * w / total for w in weights]
    table = Table([head, *body], colWidths=widths, repeatRows=1, hAlign="LEFT")
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), HEAD_FILL),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, RULE),
        ("LINEBELOW", (0, -1), (-1, -1), 0.6, RULE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]
    for row in range(2, len(shown) + 1, 2):
        style.append(("BACKGROUND", (0, row), (-1, row), ZEBRA))
    table.setStyle(TableStyle(style))
    out = []
    if title:
        out.append(Paragraph(_inline(title, cited or []), styles["h3"]))
    out.append(table)
    note = []
    if len(rows) > len(shown):
        note.append(f"First {len(shown)} of {len(rows):,} rows — the full set is in the export.")
    if source:
        note.append(f"Source: {source}")
    if note:
        out.append(Paragraph(_inline(" ".join(note), cited or []), styles["caption"]))
    else:
        out.append(Spacer(1, 8))
    return out


# -------------------------------------------------------------------- charts

def chart_flowables(chart: dict, styles: dict, width: float = 174 * mm) -> list:
    from app.data.charts import render_png
    try:
        from app.deps import container
        locale = str(container.get("chart_locale") or "fr-FR")
    except Exception:  # noqa: BLE001 - outside the app (tests, scripts)
        locale = "fr-FR"
    png = render_png(chart["spec"], width=900, scale=2, locale=locale)
    image = Image(io.BytesIO(png))
    ratio = image.imageHeight / float(image.imageWidth)
    image.drawWidth, image.drawHeight = width, width * ratio
    if image.drawHeight > 150 * mm:
        image.drawHeight, image.drawWidth = 150 * mm, 150 * mm / ratio
    caption = f"Chart {chart['id']}"
    if chart.get("source"):
        caption += f" — data: {chart['source']}"
    return [KeepTogether([image, Paragraph(_inline(caption, []), styles["caption"])])]


# ---------------------------------------------------------------------- page

class _NumberedCanvas(pdfcanvas.Canvas):
    """'Page 3 of 7' needs the page count before the first page is drawn: defer drawing."""

    footer = ""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._saved: list[dict] = []

    def showPage(self) -> None:  # noqa: N802 - reportlab's name
        self._saved.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        total = len(self._saved)
        for state in self._saved:
            self.__dict__.update(state)
            self._decorate(total)
            super().showPage()
        super().save()

    def _decorate(self, total: int) -> None:
        f = fonts()
        width, _ = A4
        self.setStrokeColor(RULE)
        self.setLineWidth(0.5)
        self.line(18 * mm, 14 * mm, width - 18 * mm, 14 * mm)
        self.setFont(f["regular"], 7.5)
        self.setFillColor(MUTED)
        self.drawString(18 * mm, 9.5 * mm, _printable(self.footer))
        self.drawRightString(width - 18 * mm, 9.5 * mm, f"Page {self._pageNumber} of {total}")


def build(path: Path, *, title: str, subtitle: str = "", sections: list[dict],
          sources: dict[int, str], charts: dict[str, dict], tables: dict[str, tuple[list[dict], str]],
          used: set[int] | None = None, generated: dt.datetime | None = None) -> dict:
    """Write the PDF. `sections` are already resolved: charts and tables are looked up by
    the keys the caller put in them, so a missing one was refused before this is called."""
    styles = _styles()
    generated = generated or dt.datetime.now()
    cited: list[int] = []
    story: list = []

    brand = Table([[Paragraph("A G E N T", styles["brand"]),
                    Paragraph(generated.strftime("%d %B %Y"), styles["date"])]],
                  colWidths=[87 * mm, 87 * mm])
    brand.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0),
                               ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story += [brand, Spacer(1, 10), Paragraph(_inline(title, cited), styles["title"])]
    if subtitle:
        story.append(Paragraph(_inline(subtitle, cited), styles["subtitle"]))
    rule = Table([[""]], colWidths=[174 * mm], rowHeights=[2])
    rule.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, -1), 1.4, BRAND)]))
    story += [rule, Spacer(1, 10)]

    for section in sections:
        if section.get("heading"):
            story.append(Paragraph(_inline(section["heading"], cited), styles["h2"]))
        if section.get("text"):
            story += markdown_flowables(section["text"], styles, cited)
        if section.get("chart"):
            story += chart_flowables(charts[section["chart"]], styles)
        if section.get("table"):
            rows, label = tables[section["table"]]
            story += table_flowables(rows, styles, title=section.get("table_title", ""),
                                     source=label, columns=section.get("columns"), cited=cited)

    listed = [n for n in sorted(set(cited) | set(used or ())) if sources.get(n)]
    if listed:
        story.append(Paragraph("Sources", styles["h2"]))
        for number in listed:
            text = sources.get(number)
            if text:
                story.append(Paragraph(f"<b>{number}.</b> " + _inline(text, []), styles["source"]))

    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=20 * mm, title=_printable(title),
                            author="AGENT", subject=_printable(subtitle or title))
    footer = f"{title} · generated by AGENT on {generated.strftime('%Y-%m-%d %H:%M')}"

    class Canvas(_NumberedCanvas):
        pass

    Canvas.footer = footer
    doc.build(story, canvasmaker=Canvas)
    pages = _page_count(path)
    return {"path": str(path), "name": path.name, "format": "pdf",
            "bytes": path.stat().st_size, "pages": pages}


def _page_count(path: Path) -> int:
    data = path.read_bytes()
    found = re.findall(rb"/Type\s*/Page[^s]", data)
    return len(found)
