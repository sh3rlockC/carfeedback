from __future__ import annotations

import json
from pathlib import Path
from textwrap import wrap
from typing import Any

from PIL import Image, ImageDraw, ImageFont


FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
)


def _font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size, index=0)
    return ImageFont.load_default()


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _load_report(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _blocks(report: dict[str, Any]) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for key, fallback_title in (
        ("strength_blocks", "核心好评"),
        ("weakness_blocks", "核心槽点"),
        ("platform_difference_blocks", "平台差异"),
        ("action_blocks", "产品建议"),
    ):
        for block in report.get(key) or []:
            if isinstance(block, dict):
                title = _text(block.get("title")) or fallback_title
                summary = _text(block.get("summary"))
                if summary:
                    items.append((title, summary))
    for line in report.get("boss_brief") or []:
        text = _text(line)
        if text:
            items.append(("老板口径", text))
    return items


def _draw_wrapped(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, font: ImageFont.ImageFont, fill: str, *, width_chars: int, line_gap: int) -> int:
    x, y = xy
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        lines.extend(wrap(paragraph, width=width_chars, replace_whitespace=False) or [""])
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += font.size + line_gap if hasattr(font, "size") else 22
    return y


def write_report_pdf(
    *,
    report_json_path: str | Path,
    output_path: str | Path,
    model_name: str,
    sample_summary: dict[str, int] | None = None,
) -> Path:
    report_path = Path(report_json_path)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    report = _load_report(report_path)
    headline = _text(report.get("headline")) or f"{model_name} 口碑完整报告"
    executive_summary = _text(report.get("executive_summary") or report.get("summary"))
    sample_summary = sample_summary or {}

    page_size = (1240, 1754)
    margin = 76
    pages: list[Image.Image] = []
    image = Image.new("RGB", page_size, "#f6efe4")
    draw = ImageDraw.Draw(image)
    title_font = _font(44)
    section_font = _font(26)
    body_font = _font(22)
    small_font = _font(18)

    y = margin
    draw.text((margin, y), "车型口碑完整报告", font=small_font, fill="#7a5b2e")
    y += 34
    y = _draw_wrapped(draw, (margin, y), headline, title_font, "#2f271f", width_chars=24, line_gap=10)
    y += 22
    metrics = f"车型：{model_name}    汽车之家：{sample_summary.get('autohome_count', 0)} 条    懂车帝：{sample_summary.get('dcd_count', 0)} 条"
    y = _draw_wrapped(draw, (margin, y), metrics, small_font, "#6f665b", width_chars=58, line_gap=8)
    y += 30
    if executive_summary:
        draw.text((margin, y), "执行摘要", font=section_font, fill="#2f271f")
        y += 38
        y = _draw_wrapped(draw, (margin, y), executive_summary, body_font, "#443a31", width_chars=42, line_gap=8)
        y += 24

    for title, summary in _blocks(report):
        if y > page_size[1] - 230:
            pages.append(image)
            image = Image.new("RGB", page_size, "#f6efe4")
            draw = ImageDraw.Draw(image)
            y = margin
        draw.rounded_rectangle((margin, y, page_size[0] - margin, y + 54), radius=8, fill="#eadcc7")
        draw.text((margin + 18, y + 13), title, font=section_font, fill="#2f271f")
        y += 72
        y = _draw_wrapped(draw, (margin + 18, y), summary, body_font, "#443a31", width_chars=40, line_gap=8)
        y += 24

    pages.append(image)
    first, rest = pages[0], pages[1:]
    first.save(output, "PDF", save_all=True, append_images=rest, resolution=144.0)
    return output
