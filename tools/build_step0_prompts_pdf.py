from __future__ import annotations

import html
import json
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import BaseDocTemplate, Frame, PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle

from build_algorithm_pdfs import (
    FRAME_H, FRAME_W, HEIGHT, INK, MARGIN, MUTED, NumberedCanvas,
    ROOT, RULE, TEAL, WIDTH, normalize,
)

SOURCE = ROOT / "docs/PROMPTS_STEP_0.md"
TARGET = ROOT / "output/pdf/Промты для Шага 0.pdf"
QA = ROOT / "tmp/pdfs/step0-prompts"
QA.mkdir(parents=True, exist_ok=True)


def styles(scale=1):
    return {
        "body": ParagraphStyle("prompt-body", fontName="Geo", fontSize=10.4*scale,
                               leading=13.6*scale, textColor=INK, spaceAfter=5*scale, splitLongWords=True),
        "list": ParagraphStyle("prompt-list", fontName="Geo", fontSize=10.4*scale,
                               leading=13*scale, textColor=INK, spaceAfter=1.2*scale,
                               leftIndent=13*scale, firstLineIndent=-13*scale, splitLongWords=True),
        "label": ParagraphStyle("prompt-label", fontName="GeoBold", fontSize=8.5,
                                leading=11, textColor=TEAL, spaceAfter=8),
        "title": ParagraphStyle("prompt-title", fontName="GeoBold", fontSize=20*scale,
                                leading=25*scale, textColor=INK, spaceAfter=15*scale, keepWithNext=True),
        "cover": ParagraphStyle("prompt-cover", fontName="GeoBold", fontSize=30,
                                leading=36, textColor=TEAL, spaceAfter=18),
        "h2": ParagraphStyle("prompt-h2", fontName="GeoBold", fontSize=12,
                             leading=16, textColor=TEAL, spaceBefore=11, spaceAfter=7, keepWithNext=True),
        "cell": ParagraphStyle("prompt-cell", fontName="Geo", fontSize=10.2,
                               leading=14, textColor=INK),
    }


def flows_for(chunk, index, scale=1):
    s = styles(scale)
    lines = normalize(chunk).strip().splitlines()
    result = []
    if index:
        result.append(Paragraph(f"ПРОМПТ {index} ИЗ 8 / ТЕКСТ ДЛЯ КОПИРОВАНИЯ", s["label"]))
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line:
            continue
        if line.startswith("# "):
            result.append(Paragraph(html.escape(line[2:]), s["cover" if index == 0 else "title"]))
            continue
        if line.startswith("## "):
            result.append(Paragraph(html.escape(line[3:]), s["h2"]))
            continue
        if line.startswith("- ") or re.match(r"^\d+\. ", line):
            result.append(Paragraph(html.escape(line), s["list"]))
            continue
        paragraph = [line]
        while i < len(lines) and lines[i].strip() and not re.match(r"^(#|- |\d+\. )", lines[i].strip()):
            paragraph.append(lines[i].strip())
            i += 1
        text = " ".join(paragraph)
        style = s["body"]
        if text.endswith(":"):
            style = ParagraphStyle("lead-in", parent=style, keepWithNext=True, spaceAfter=4*scale)
        result.append(Paragraph(html.escape(text), style))
    return result


def total_height(flows):
    return sum(flow.wrap(FRAME_W, FRAME_H)[1] + flow.getSpaceBefore() + flow.getSpaceAfter() for flow in flows)


def decorate(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(TEAL)
    canvas.setFont("GeoBold", 8)
    canvas.drawString(MARGIN, HEIGHT-29, "GEO / ПРОМПТЫ / ШАГ 0")
    canvas.setFillColor(MUTED)
    canvas.setFont("Geo", 8)
    canvas.drawRightString(WIDTH-MARGIN, HEIGHT-29, "01.11.2026")
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.6)
    canvas.line(MARGIN, HEIGHT-39, WIDTH-MARGIN, HEIGHT-39)
    canvas.line(MARGIN, 44, WIDTH-MARGIN, 44)
    canvas.setFont("Geo", 7.8)
    canvas.drawString(MARGIN, 29, "Последовательная разработка Python-кода")
    canvas.restoreState()


def main():
    chunks = SOURCE.read_text(encoding="utf-8").split("---PROMPT---")
    assert len(chunks) == 9
    story = []
    layout = []
    for index, chunk in enumerate(chunks):
        scale = 1.0
        flows = flows_for(chunk, index, scale)
        height = total_height(flows)
        while height > FRAME_H-8 and scale > 0.96:
            scale = round(scale-0.01, 2)
            flows = flows_for(chunk, index, scale)
            height = total_height(flows)
        if height > FRAME_H-8:
            raise RuntimeError(f"Prompt {index}: content needs {height:.1f} points")
        if index:
            story.append(PageBreak())
        story.extend(flows)
        layout.append({"page": index+1, "prompt": index, "height": round(height, 1), "scale": scale})
    doc = BaseDocTemplate(str(TARGET), pagesize=(WIDTH, HEIGHT), title="Промты для Шага 0",
                          author="Проект GEO", subject="Восемь промптов для разработки шага 0",
                          leftMargin=MARGIN, rightMargin=MARGIN, topMargin=56, bottomMargin=53,
                          pageCompression=1)
    doc.addPageTemplates(PageTemplate(id="prompts", frames=[Frame(MARGIN, 53, FRAME_W, FRAME_H,
                         leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)], onPage=decorate))
    doc.build(story, canvasmaker=NumberedCanvas)
    (QA / "layout.json").write_text(json.dumps(layout, indent=2), encoding="utf-8")
    print(json.dumps({"path": str(TARGET), "pages": 9, "layout": layout}, ensure_ascii=False))


if __name__ == "__main__":
    main()
