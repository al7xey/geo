from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pdfplumber
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
QA = ROOT / "tmp" / "pdfs" / "algorithm-v1"
FILES = [
    (0, ROOT / "output/pdf/Шаг 0 - Проверка адреса сайта.pdf", 11),
    (1, ROOT / "output/pdf/Шаг 1 - Чтение сайта и технические проверки.pdf", 18),
]


def require(condition: bool, detail: object) -> None:
    if not condition:
        raise ValueError(detail)


results = []
for step, path, expected in FILES:
    reader = PdfReader(path)
    require(len(reader.pages) == expected, (step, len(reader.pages), expected))
    texts = [page.extract_text() or "" for page in reader.pages]
    require(all(len(text) > 600 for text in texts), (step, "unexpected short page"))
    require(all("\ufffd" not in text and "\u25a0" not in text for text in texts), "Invalid glyph")
    for i, text in enumerate(texts, 1):
        require(f"{i} / {expected}" in text, (step, i, "footer mismatch"))
    outside = []
    with pdfplumber.open(path) as pdf:
        for n, page in enumerate(pdf.pages, 1):
            for char in page.chars:
                if (
                    char["x0"] < 45
                    or char["x1"] > page.width - 45
                    or char["top"] < 15
                    or char["bottom"] > page.height - 18
                ):
                    outside.append(
                        {"page": n, "text": char["text"], "x0": char["x0"], "x1": char["x1"]}
                    )
    require(not outside, (step, outside[:10]))
    links = sum(len(page.get("/Annots", [])) for page in reader.pages)
    results.append(
        {
            "step": step,
            "pages": expected,
            "characters": sum(map(len, texts)),
            "links": links,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "text_and_bounds": "passed",
        }
    )
    (QA / f"step{step}-extracted.txt").write_text("\n\n".join(texts), encoding="utf-8")

    images = sorted(QA.glob(f"step{step}-[0-9]*.png"))
    require(len(images) == expected, (step, "rendering not complete", len(images)))
    for start in range(0, expected, 6):
        page_set = images[start : start + 6]
        cell_w, cell_h = 470, 700
        sheet = Image.new("RGB", (2 * cell_w, math.ceil(len(page_set) / 2) * cell_h), "#dce4e6")
        draw = ImageDraw.Draw(sheet)
        font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 18)
        for index, image_path in enumerate(page_set):
            preview = Image.open(image_path).convert("RGB")
            preview.thumbnail((cell_w - 20, cell_h - 38))
            x = (index % 2) * cell_w + (cell_w - preview.width) // 2
            y = (index // 2) * cell_h + 29
            sheet.paste(preview, (x, y))
            draw.text(
                ((index % 2) * cell_w + 12, (index // 2) * cell_h + 5),
                f"STEP {step} / PAGE {start + index + 1}",
                fill="#17343c",
                font=font,
            )
        sheet.save(QA / f"contact-step{step}-{start // 6 + 1}.png")

source = ROOT / "docs/source/geo-audit-algorithm-2026-10-31.pdf"
require(
    hashlib.sha256(source.read_bytes()).hexdigest().upper()
    == "EF8D4DF5902719F4350089D1BC803902FD66A73C9EC542742E5B2BAACA820F48",
    "Original PDF checksum mismatch",
)
(QA / "verification.json").write_text(
    json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(json.dumps(results, ensure_ascii=False, indent=2))
