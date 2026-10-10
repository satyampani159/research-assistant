"""Markdown report -> PDF bytes for the frontend's download button (fpdf2)."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from fpdf import FPDF

_FONT_DIRS = [
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("C:/Windows/Fonts"),
]


def _unicode_font_dir() -> Path | None:
    for d in _FONT_DIRS:
        if (d / "DejaVuSans.ttf").is_file() and (d / "DejaVuSans-Bold.ttf").is_file():
            return d
    return None


class _Pdf(FPDF):
    def __init__(self) -> None:
        super().__init__()
        self.set_auto_page_break(auto=True, margin=18)
        self.set_margins(18, 18, 18)
        d = _unicode_font_dir()
        self.unicode = d is not None
        if d is not None:
            self.family = "DejaVu"
            self.add_font("DejaVu", "", str(d / "DejaVuSans.ttf"))
            self.add_font("DejaVu", "B", str(d / "DejaVuSans-Bold.ttf"))
            italic = d / "DejaVuSans-Oblique.ttf"
            self.add_font("DejaVu", "I", str(italic if italic.is_file() else d / "DejaVuSans.ttf"))
            bi = d / "DejaVuSans-BoldOblique.ttf"
            self.add_font("DejaVu", "BI", str(bi if bi.is_file() else d / "DejaVuSans-Bold.ttf"))
        else:
            self.family = "Helvetica"

    def txt(self, text: str) -> str:
        if self.unicode:
            return text
        return text.encode("latin-1", "replace").decode("latin-1")

    def footer(self) -> None:
        self.set_y(-12)
        self.set_font(self.family, "I", 8)
        self.set_text_color(130, 130, 130)
        self.cell(0, 8, f"Page {self.page_no()}/{{nb}}", align="C")
        self.set_text_color(0, 0, 0)


_BOLD_ITALIC = re.compile(r"(\*\*\*|___)(.+?)\1")
_CODE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")


def _inline(text: str) -> str:
    """Reduce markdown to what fpdf2's markdown=True understands (**bold**, __italic__)."""
    text = _LINK.sub(r"\1 (\2)", text)
    text = _CODE.sub(r"\1", text)
    text = _BOLD_ITALIC.sub(r"**\2**", text)
    text = re.sub(r"(?<!\*)\*(?!\*)([^*\n]+?)(?<!\*)\*(?!\*)", r"__\1__", text)
    return text


def build_pdf(data: dict) -> bytes:
    pdf = _Pdf()
    pdf.alias_nb_pages()
    pdf.add_page()
    f = pdf.family

    def line(text: str, style: str = "", size: int = 10, h: float = 5.2, md: bool = True) -> None:
        pdf.set_font(f, style, size)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(0, h, pdf.txt(_inline(text) if md else text), markdown=md,
                       new_x="LMARGIN", new_y="NEXT")

    # title block
    line("Research Report", "B", 20, 10, md=False)
    pdf.ln(1)
    meta = [("Topic", data.get("topic")), ("Audience", data.get("audience")),
            ("Depth", data.get("depth")), ("Generated", datetime.now().strftime("%Y-%m-%d %H:%M"))]
    for k, v in meta:
        if v:
            line(f"{k}: {v}", "", 10, 5.5, md=False)
    pdf.ln(2)
    pdf.set_draw_color(160, 160, 160)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(5)

    # body
    for raw in (data.get("report_md") or "").splitlines():
        s = raw.rstrip()
        if not s.strip():
            pdf.ln(2.5)
            continue
        m = re.match(r"^(#{1,4})\s+(.*)", s)
        if m:
            level = len(m.group(1))
            size = {1: 16, 2: 13.5, 3: 12, 4: 11}[level]
            pdf.ln(2.5)
            line(m.group(2).replace("*", ""), "B", size, size * 0.55, md=False)
            pdf.ln(1)
            continue
        b = re.match(r"^\s*[-*+]\s+(.*)", s) or re.match(r"^\s*\d+[.)]\s+(.*)", s)
        if b:
            line("-  " + b.group(1), "", 10)
            continue
        if s.lstrip().startswith(">"):
            line(s.lstrip("> ").strip(), "I", 10)
            continue
        if re.match(r"^\s*(-{3,}|\*{3,})\s*$", s):
            continue
        line(s, "", 10)

    # metrics
    metrics = data.get("metrics") or {}
    if metrics:
        pdf.ln(4)
        line("Run metrics", "B", 12, 7, md=False)
        for k, v in metrics.items():
            line(f"{k}: {v}", "", 9, 5, md=False)

    # references
    all_refs = data.get("references") or []
    groups = [("References", [r for r in all_refs if r.get("role") != "further_reading"]),
              ("Further reading", [r for r in all_refs if r.get("role") == "further_reading"])]
    for heading, refs in groups:
        if not refs:
            continue
        pdf.ln(4)
        line(heading, "B", 12, 7, md=False)
        for i, r in enumerate(refs, 1):
            head = f"[{i}] {r.get('title', '')}"
            if r.get("publisher"):
                head += f" - {r['publisher']}"
            if r.get("date"):
                head += f" ({r['date']})"
            line(head, "", 9, 4.6, md=False)
            if r.get("source_type"):
                line("    Source type: " + r["source_type"], "", 8, 4.2, md=False)
            if r.get("note"):
                line("    " + r["note"], "I", 8, 4.2, md=False)
            if r.get("quote"):
                line("    “" + r["quote"] + "”", "I", 8, 4.2, md=False)
            if r.get("url"):
                pdf.set_text_color(30, 70, 160)
                line("    " + r["url"], "", 8, 4.2, md=False)
                pdf.set_text_color(0, 0, 0)
            pdf.ln(1)

    return bytes(pdf.output())
