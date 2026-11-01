from __future__ import annotations

import html
import json
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    XPreformatted,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "pdf"
QA = ROOT / "tmp" / "pdfs" / "algorithm-v1"
OUT.mkdir(parents=True, exist_ok=True)
QA.mkdir(parents=True, exist_ok=True)

FONT_DIR = Path("C:/Windows/Fonts")
for name, filename in [
    ("Geo", "arial.ttf"),
    ("GeoBold", "arialbd.ttf"),
    ("GeoItalic", "ariali.ttf"),
    ("GeoMono", "consola.ttf"),
]:
    pdfmetrics.registerFont(TTFont(name, str(FONT_DIR / filename)))
pdfmetrics.registerFontFamily(
    "Geo", normal="Geo", bold="GeoBold", italic="GeoItalic", boldItalic="GeoBold"
)

INK = colors.HexColor("#172B3B")
TEAL = colors.HexColor("#126B70")
MUTED = colors.HexColor("#61727C")
PALE = colors.HexColor("#EAF3F3")
RULE = colors.HexColor("#D8E2E5")
WIDTH, HEIGHT = A4
MARGIN = 47
FRAME_W = WIDTH - 2 * MARGIN
FRAME_H = HEIGHT - 109


def normalize(text: str) -> str:
    for char in "\u2010\u2011\u2012\u2013\u2014\u2212":
        text = text.replace(char, "-")
    return text.replace("\u00a0", " ").replace("\u202f", " ")


def inline(text: str) -> str:
    text = normalize(text)
    tokens: list[str] = []

    def link(match: re.Match) -> str:
        label, url = match.group(1), match.group(2)
        tokens.append(
            f'<a href="{html.escape(url, quote=True)}" color="#126B70">'
            f"<u>{html.escape(label)}</u></a>"
        )
        return f"@@LINK{len(tokens) - 1}@@"

    text = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", link, text)
    text = html.escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    for i, value in enumerate(tokens):
        text = text.replace(f"@@LINK{i}@@", value)
    return text


def make_styles(scale: float = 1):
    body = 10.15 * scale
    leading = 14.05 * scale
    shared = dict(fontName="Geo", textColor=INK, alignment=TA_LEFT, splitLongWords=True)
    return {
        "body": ParagraphStyle(
            "body", fontSize=body, leading=leading, spaceAfter=7 * scale, **shared
        ),
        "title": ParagraphStyle(
            "title",
            fontName="GeoBold",
            textColor=INK,
            fontSize=21 * scale,
            leading=26 * scale,
            spaceAfter=14 * scale,
            keepWithNext=True,
        ),
        "cover": ParagraphStyle(
            "cover",
            fontName="GeoBold",
            textColor=TEAL,
            fontSize=30 * scale,
            leading=35 * scale,
            spaceAfter=14 * scale,
            keepWithNext=True,
        ),
        "h2": ParagraphStyle(
            "h2",
            fontName="GeoBold",
            textColor=INK,
            fontSize=19 * scale,
            leading=24 * scale,
            spaceAfter=12 * scale,
            keepWithNext=True,
        ),
        "h3": ParagraphStyle(
            "h3",
            fontName="GeoBold",
            textColor=TEAL,
            fontSize=11.4 * scale,
            leading=15.3 * scale,
            spaceBefore=8 * scale,
            spaceAfter=5 * scale,
            keepWithNext=True,
        ),
        "list": ParagraphStyle(
            "list",
            fontSize=body,
            leading=leading,
            leftIndent=13 * scale,
            firstLineIndent=-13 * scale,
            spaceAfter=6 * scale,
            **shared,
        ),
        "cell": ParagraphStyle(
            "cell", fontSize=9.3 * scale, leading=12.25 * scale, spaceAfter=0, **shared
        ),
        "headcell": ParagraphStyle(
            "headcell",
            fontName="GeoBold",
            textColor=colors.white,
            fontSize=9.2 * scale,
            leading=12.15 * scale,
        ),
        "code": ParagraphStyle(
            "code", fontName="GeoMono", fontSize=8.1 * scale, leading=10.65 * scale, textColor=INK
        ),
    }


def markdown_flowables(markdown: str, scale: float = 1):
    styles = make_styles(scale)
    lines = normalize(markdown).strip().splitlines()
    flows = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if line.startswith("```"):
            i += 1
            code = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i].rstrip())
                i += 1
            code_body = XPreformatted(html.escape("\n".join(code)), styles["code"])
            box = Table([[code_body]], colWidths=[FRAME_W], hAlign="LEFT")
            box.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), PALE),
                        ("BOX", (0, 0), (-1, -1), 0.5, RULE),
                        ("LEFTPADDING", (0, 0), (-1, -1), 9 * scale),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 9 * scale),
                        ("TOPPADDING", (0, 0), (-1, -1), 9 * scale),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 9 * scale),
                    ]
                )
            )
            box.spaceBefore = 4 * scale
            box.spaceAfter = 10 * scale
            flows.append(box)
            i += 1
            continue
        if line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                row = [v.strip() for v in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-+:?", v) for v in row):
                    rows.append(row)
                i += 1
            ncols = len(rows[0])
            if ncols == 2:
                fractions = [0.40, 0.60]
            elif ncols == 3:
                if rows[0][0] == "status":
                    fractions = [0.15, 0.66, 0.19]
                elif rows[0][1] == "material_state":
                    fractions = [0.50, 0.23, 0.27]
                else:
                    fractions = [0.43, 0.23, 0.34]
            else:
                fractions = [1 / ncols] * ncols
            cells = [
                [
                    Paragraph(inline(value), styles["headcell" if ri == 0 else "cell"])
                    for value in row
                ]
                for ri, row in enumerate(rows)
            ]
            table = Table(
                cells, colWidths=[FRAME_W * f for f in fractions], repeatRows=1, hAlign="LEFT"
            )
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), TEAL),
                        (
                            "ROWBACKGROUNDS",
                            (0, 1),
                            (-1, -1),
                            [colors.white, colors.HexColor("#F3F7F8")],
                        ),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 8 * scale),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 8 * scale),
                        ("TOPPADDING", (0, 0), (-1, -1), 4.8 * scale),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4.8 * scale),
                        ("LINEBELOW", (0, -1), (-1, -1), 0.4, RULE),
                    ]
                )
            )
            flows.extend([table, Spacer(1, 9 * scale)])
            continue
        if line.startswith("# "):
            style = "cover" if line in ("# ШАГ 0", "# ШАГ 1") else "title"
            flows.append(Paragraph(inline(line[2:]), styles[style]))
            i += 1
            continue
        if line.startswith("## "):
            flows.append(Paragraph(inline(line[3:]), styles["h2"]))
            i += 1
            continue
        if line.startswith("### "):
            flows.append(Paragraph(inline(line[4:]), styles["h3"]))
            i += 1
            continue
        if re.match(r"^\d+\. ", line):
            flows.append(Paragraph(inline(line), styles["list"]))
            i += 1
            continue
        paragraph = [line]
        i += 1
        while (
            i < len(lines)
            and lines[i].strip()
            and not re.match(r"^(#|\||```|\d+\. )", lines[i].strip())
        ):
            paragraph.append(lines[i].strip())
            i += 1
        flows.append(Paragraph(inline(" ".join(paragraph)), styles["body"]))
    return flows


def measured_height(flows) -> float:
    total = 0
    for flow in flows:
        _, height = flow.wrap(FRAME_W, FRAME_H)
        total += height + flow.getSpaceBefore() + flow.getSpaceAfter()
    return total


class NumberedCanvas(Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.states = []

    def showPage(self):
        self.states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        pages = len(self.states)
        for state in self.states:
            self.__dict__.update(state)
            self.saveState()
            self.setFillColor(MUTED)
            self.setFont("Geo", 8)
            self.drawRightString(WIDTH - MARGIN, 29, f"{self._pageNumber} / {pages}")
            self.restoreState()
            super().showPage()
        super().save()


def header_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("GeoBold", 8)
    canvas.setFillColor(TEAL)
    canvas.drawString(MARGIN, HEIGHT - 29, f"GEO / АЛГОРИТМ / ШАГ {doc.step}")
    canvas.setFont("Geo", 8)
    canvas.setFillColor(MUTED)
    canvas.drawRightString(WIDTH - MARGIN, HEIGHT - 29, "Редакция 1.0 · 31.10.2026")
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.6)
    canvas.line(MARGIN, HEIGHT - 39, WIDTH - MARGIN, HEIGHT - 39)
    canvas.line(MARGIN, 44, WIDTH - MARGIN, 44)
    canvas.setFont("Geo", 7.8)
    canvas.drawString(MARGIN, 29, "Рабочая спецификация для Python-реализации")
    canvas.restoreState()


def build(step: int):
    source = ROOT / "docs" / "steps" / f"STEP_{step}_ALGORITHM_v1.md"
    title = (
        "Проверка адреса и доступности сайта"
        if step == 0
        else "Чтение сайта и технические проверки"
    )
    filename = (
        "Шаг 0 - Проверка адреса сайта.pdf"
        if step == 0
        else "Шаг 1 - Чтение сайта и технические проверки.pdf"
    )
    output = OUT / filename
    chunks = source.read_text(encoding="utf-8").split("---PAGE---")
    story = []
    measurements = []
    for i, chunk in enumerate(chunks):
        scale = 1.0
        flows = markdown_flowables(chunk, scale)
        height = measured_height(flows)
        while height > FRAME_H - 8 and scale > 0.94:
            scale = round(scale - 0.01, 2)
            flows = markdown_flowables(chunk, scale)
            height = measured_height(flows)
        if height > FRAME_H - 8:
            raise RuntimeError(
                f"Step {step}, logical page {i + 1}: {height:.1f} > {FRAME_H - 8:.1f}"
            )
        measurements.append({"logical_page": i + 1, "height": round(height, 1), "scale": scale})
        if i:
            story.append(PageBreak())
        story.extend(flows)
    doc = BaseDocTemplate(
        str(output),
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=56,
        bottomMargin=53,
        title=f"Шаг {step}. {title}",
        author="Проект GEO",
        subject="Подробный алгоритм, редакция 1.0",
        pageCompression=1,
    )
    doc.step = step
    doc.addPageTemplates(
        PageTemplate(
            id="main",
            frames=[
                Frame(
                    MARGIN,
                    53,
                    FRAME_W,
                    FRAME_H,
                    leftPadding=0,
                    rightPadding=0,
                    topPadding=0,
                    bottomPadding=0,
                )
            ],
            onPage=header_footer,
        )
    )
    doc.build(story, canvasmaker=NumberedCanvas)
    (QA / f"step{step}-layout.json").write_text(
        json.dumps(measurements, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "step": step,
                "path": str(output),
                "expected_pages": len(chunks),
                "smallest_scale": min(p["scale"] for p in measurements),
                "bytes": output.stat().st_size,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    build(0)
    build(1)
