"""Conservative PDF guide step discovery with source coordinates.

Never marks the guide complete, rewrites source PDFs, or executes Salesforce actions.
The parsed manifest can contain confidential guide content: keep it private.
"""

from dataclasses import asdict, dataclass
from pathlib import Path
import argparse
import json
import re
import unicodedata


NUMBER = re.compile(r"^(\d{1,2})\.\s*(.*)$")
LETTER = re.compile(r"^([a-z])\.\s*(.*)$", re.I)
ROMAN = re.compile(r"^(x{0,3}(?:ix|iv|v?i{1,3}|v))\.\s*(.*)$", re.I)
# Roman substeps sit further right than the letter step they belong to.
ROMAN_INDENT = 15
# Headings print larger than step text. A guide without "Exercise N" lines
# still names each exercise this way.
HEADING_SIZE = 14.5
TASK = re.compile(r"^Task\s+\d+\s*:.+", re.I)
SECTION = re.compile(r"^(?:Exercise\s+\d+(?:-\d+)?|Use Case\s+\d+(?:-\d+)?|Set Up Your Instance)\b.*", re.I)
FOOTER = re.compile(r"^\d+\s*\|\s*.*(?:Guide|Workbook)\b", re.I)
TABLE_CONTEXT = re.compile(r"\b(?:following values|following information|table below|using the table)\b", re.I)
SUSPECT = re.compile(r"\bfi\b|\b(?:enter|update|select|click in the|once the)\s*$", re.I)


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).replace("\u200b", "")).strip()


@dataclass(frozen=True)
class GuideStep:
    step_id: str
    page: int
    exercise: str | None
    task: str | None
    number: str
    text: str
    bbox: tuple[float, float, float, float]
    parent_id: str | None
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class GuideInventory:
    page_count: int
    steps: tuple[GuideStep, ...]
    pages_without_steps: tuple[int, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict:
        return {"page_count": self.page_count,
                "steps": [asdict(step) for step in self.steps],
                "pages_without_steps": list(self.pages_without_steps),
                "warnings": list(self.warnings),
                "verified_complete": False,
                "review_required": "Visually reconcile every page, step, table and screenshot."}


def _heading_size(line: dict) -> float:
    sizes = [float(char.get("size") or 0) for char in line.get("chars") or () if str(char.get("text", "")).strip()]
    return max(sizes) if sizes else 0.0


def extract_inventory(path: str | Path) -> GuideInventory:
    """Extract candidates, not a verified/actionable guide manifest.

    pdfplumber is MIT licensed; source words/positions come directly from the
    PDF. No OCR or LLM reconstruction is performed. Missing visual/text data
    require human review instead of a false green result.
    """
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError("Requires pdfplumber; see docs/GUIDE_INVENTORY.md") from exc
    found: list[GuideStep] = []
    missing: list[int] = []
    warnings: list[str] = []
    exercise: str | None = None
    task: str | None = None
    parent: str | None = None
    last_number: int | None = None
    letter: tuple[str, float] | None = None
    heading_bottom: float | None = None
    seen_tasks: set[tuple[str | None, str]] = set()
    with pdfplumber.open(str(path)) as pdf:
        for pnum, page in enumerate(pdf.pages, 1):
            ordinal = 0
            active: dict | None = None
            page_found = 0

            def finish():
                nonlocal active, page_found
                if active is None:
                    return
                text = clean(" ".join(active["parts"]))
                flags = list(active["warnings"])
                if len(text) < 10 or SUSPECT.search(text):
                    flags.append("possibly_truncated_text")
                if TABLE_CONTEXT.search(text):
                    flags.append("table_or_visual_context_required")
                found.append(GuideStep(
                    active["id"], pnum, active["exercise"], active["task"],
                    active["number"], text,
                    tuple(round(x, 2) for x in active["bbox"]),
                    active["parent"], tuple(dict.fromkeys(flags))))
                page_found += 1
                active = None

            lines = sorted(page.extract_text_lines(layout=False),
                           key=lambda row: (round(row["top"], 1), row["x0"]))
            for line in lines:
                text = clean(line["text"])
                x, top, right, bottom = (float(line[k]) for k in ("x0", "top", "x1", "bottom"))
                if not text or top > page.height - 38 or FOOTER.match(text):
                    continue
                big = _heading_size(line) >= HEADING_SIZE
                if big and heading_bottom is not None and 0 <= top - heading_bottom <= 10 and exercise:
                    exercise = f"{exercise} {text}"
                    heading_bottom = bottom
                    continue
                heading_bottom = None
                if SECTION.match(text) or (big and not NUMBER.match(text)):
                    finish()
                    exercise, task, last_number, parent, letter = text, None, None, None, None
                    heading_bottom = bottom if big else None
                    continue
                if TASK.match(text):
                    finish()
                    task, last_number, parent = text, None, None
                    if (exercise, task) in seen_tasks:
                        warnings.append(f"Repeated task label on page {pnum}")
                    seen_tasks.add((exercise, task))
                    continue
                number = NUMBER.match(text) if x >= 78 else None
                roman = (ROMAN.match(text) if letter is not None and x >= letter[1] + ROMAN_INDENT
                         else None)
                lettered = LETTER.match(text) if x >= 102 and not roman else None
                if number or lettered or roman:
                    finish()
                    ordinal += 1
                    step_id = f"P{pnum:03}N{ordinal:03}"
                    flags: list[str] = []
                    if number:
                        n = int(number.group(1))
                        if (last_number is None and n != 1) or (last_number is not None and n != last_number + 1):
                            flags.append("numbering_review_required")
                        last_number = n
                        kind_num, source_text, predecessor = str(n), number.group(2), None
                        parent, letter = step_id, None
                    elif roman:
                        kind_num, source_text, predecessor = roman.group(1).lower(), roman.group(2), letter[0]
                    else:
                        kind_num, source_text, predecessor = lettered.group(1).lower(), lettered.group(2), parent
                        letter = (step_id, x)
                        if not parent:
                            flags.append("parent_review_required")
                    active = {"id": step_id, "number": kind_num, "parts": [source_text],
                              "bbox": [x, top, right, bottom], "last_bottom": bottom,
                              "x": x, "parent": predecessor, "exercise": exercise,
                              "task": task, "warnings": flags}
                    continue
                if active and x >= active["x"] + 8 and 0 <= top - active["last_bottom"] <= 10:
                    active["parts"].append(text)
                    active["bbox"][2] = max(active["bbox"][2], right)
                    active["bbox"][3] = max(active["bbox"][3], bottom)
                    active["last_bottom"] = bottom
            finish()
            if page_found == 0:
                missing.append(pnum)
            if not page.extract_words() and page.images:
                warnings.append(f"Page {pnum}: image-only content needs visual review")
        page_count = len(pdf.pages)
    if not found:
        warnings.append("No numbered steps found; inspect rendered pages")
    return GuideInventory(page_count, tuple(found), tuple(missing), tuple(dict.fromkeys(warnings)))


def main():
    parser = argparse.ArgumentParser(description="Private PDF step inventory (never verifies completion)")
    parser.add_argument("guide", help="Path to locally accessible, approved private PDF")
    parser.add_argument("--output", required=True, help="Private manifest JSON destination")
    args = parser.parse_args()
    inventory = extract_inventory(args.guide)
    dest = Path(args.output)
    if dest.exists():
        parser.error("Refusing to overwrite an existing manifest")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(inventory.to_dict(), indent=2), encoding="utf-8")
    dest.chmod(0o600)
    print(json.dumps({"pages": inventory.page_count,
                      "candidate_steps": len(inventory.steps),
                      "flagged_steps": sum(bool(s.warnings) for s in inventory.steps),
                      "verified_complete": False}))


if __name__ == "__main__":
    main()
