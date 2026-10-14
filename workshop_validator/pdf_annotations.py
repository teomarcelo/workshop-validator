"""Layout-preserving PDF annotations for private instructor review.

Never writes over source PDF bytes, rasterizes original pages, or treats an
unverified recommendation as fact. Source PDFs/output PDFs are confidential.
"""

from dataclasses import dataclass
import argparse
import json
from pathlib import Path


ALLOWED = frozenset({
    "UI_MISMATCH", "FUNCTIONAL_FAIL", "ENV_BLOCKED", "AUTH_BLOCKED",
    "NEEDS_REVIEW", "NOT_RUN", "RETRY_WAIT",
})


@dataclass(frozen=True)
class ReviewNote:
    source_page: int
    step_id: str
    status: str
    original_instruction: str
    finding: str
    proposed_correction: str = ""
    evidence_ids: tuple[str, ...] = ()
    illustrative: bool = False


def _wrap(text: str, max_width: float, fitz, font_size: float = 10) -> list[str]:
    words = " ".join(text.split()).split()
    if not words:
        return ["(not supplied)"]
    result: list[str] = []
    line = ""
    for word in words:
        # Split single long tokens rather than silently exceeding the boundary.
        parts = []
        while fitz.get_text_length(word, fontname="helv", fontsize=font_size) > max_width:
            head = ""
            for letter in word:
                if fitz.get_text_length(head + letter, fontname="helv", fontsize=font_size) > max_width:
                    break
                head += letter
            if not head:
                head = word[0]
            parts.append(head)
            word = word[len(head):]
        parts.append(word)
        for part in parts:
            next_line = f"{line} {part}".strip()
            if line and fitz.get_text_length(next_line, fontname="helv", fontsize=font_size) > max_width:
                result.append(line)
                line = part
            else:
                line = next_line
    if line:
        result.append(line)
    return result


def annotate_pdf(source_pdf: str | Path, notes: list[ReviewNote],
                 destination: str | Path) -> dict:
    """Copy original vector pages unchanged; insert labeled review sheets.

    Each affected page is followed by its review sheet(s). PDF instructions
    cannot generally be expanded inline while preserving every source pixel
    and layout position. The addendum avoids that destructive tradeoff.
    """
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("PyMuPDF is required for annotated PDF generation") from exc
    source, dest = Path(source_pdf), Path(destination)
    if source.resolve() == dest.resolve():
        raise ValueError("Cannot overwrite the original PDF")
    if dest.exists():
        raise FileExistsError("Refusing to overwrite an existing annotated PDF")
    if not source.is_file():
        raise FileNotFoundError(source)
    # A zero-note review copy preserves the original pages without implying
    # that untested instructions are correct or unnecessary.
    with fitz.open(source) as original:
        total_pages = original.page_count
        if total_pages < 1:
            raise ValueError("Empty source PDF")
        for note in notes:
            if note.status not in ALLOWED or type(note.source_page) is not int or not 1 <= note.source_page <= total_pages:
                raise ValueError("Invalid review status or page")
            if not note.step_id.strip() or not note.finding.strip():
                raise ValueError("Missing note identification or finding")
            if len(note.original_instruction) > 30000 or len(note.finding) > 30000 or len(note.proposed_correction) > 30000:
                raise ValueError("Oversized review note; split into smaller findings")

        grouped: dict[int, list[ReviewNote]] = {}
        for note in notes:
            grouped.setdefault(note.source_page, []).append(note)

        result = fitz.open()
        note_pages = 0
        for p in range(total_pages):
            result.insert_pdf(original, from_page=p, to_page=p,
                              links=False, annots=False)
            for note in grouped.get(p + 1, ()):
                sheet = None
                y = 0.0
                width = float(original[p].rect.width)
                height = float(original[p].rect.height)
                left = 66.0
                right = width - 60.0
                maxwidth = right - left

                def new_sheet(continued: bool = False):
                    nonlocal sheet, y, note_pages
                    sheet = result.new_page(width=width, height=height)
                    note_pages += 1
                    sheet.draw_rect(fitz.Rect(0, 0, width, 12), color=None, fill=(0.05, 0.46, 0.48))
                    sheet.insert_text((left, 65), "INSTRUCTOR REVIEW NOTE", fontname="hebo",
                                      fontsize=17, color=(0.05, 0.31, 0.4))
                    sheet.insert_text((left, 89),
                                      f"Source page {note.source_page}  |  {note.step_id}" +
                                      ("  |  CONTINUED" if continued else ""),
                                      fontname="helv", fontsize=10, color=(0.26, 0.32, 0.37))
                    sheet.insert_text((left, 111), f"STATUS: {note.status}",
                                      fontname="hebo", fontsize=11, color=(0.65, 0.35, 0.06))
                    y = 136.0
                    if note.illustrative:
                        sheet.draw_rect(fitz.Rect(left, y, right, y + 33),
                                        color=(0.77, 0.38, 0.04), fill=(1, 0.95, 0.84))
                        sheet.insert_text((left + 9, y + 20),
                                          "ILLUSTRATIVE ONLY - NOT A VERIFIED UI CHANGE",
                                          fontname="hebo", fontsize=9,
                                          color=(0.5, 0.2, 0.03))
                        y += 53

                def section(title: str, value: str):
                    nonlocal y
                    lines = _wrap(value, maxwidth, fitz)
                    if y + 35 > height - 70:
                        new_sheet(continued=True)
                    sheet.insert_text((left, y + 11), title, fontsize=10, fontname="hebo",
                                      color=(0.05, 0.31, 0.4))
                    y += 24
                    for line in lines:
                        if y + 13 > height - 70:
                            new_sheet(continued=True)
                        sheet.insert_text((left, y + 10), line, fontsize=10, fontname="helv",
                                          color=(0.14, 0.19, 0.22))
                        y += 14
                    y += 19

                new_sheet()
                section("ORIGINAL GUIDE INSTRUCTION", note.original_instruction)
                section("FINDING / WHY THIS NEEDS REVIEW", note.finding)
                section("PROPOSED CORRECTION - NOT AUTO-APPLIED",
                        note.proposed_correction or "No validated correction available.")
                section("EVIDENCE REFERENCES", ", ".join(note.evidence_ids) or
                        "No independent evidence attached.")
                if y + 25 > height - 36:
                    new_sheet(continued=True)
                sheet.draw_line((left, y), (right, y), color=(0.75, 0.82, 0.85), width=0.8)
                y += 23
                sheet.insert_text((left, y),
                                  "Original page content is preserved. Review before publishing.",
                                  fontname="helv", fontsize=9, color=(0.4, 0.44, 0.49))
        result.set_metadata({
            "title": "Instructor Review Copy - Annotated Workshop Exercise Guide",
            "subject": "Private annotations; source pages unchanged; not approved for student distribution",
            "creator": "Workshop Validator prototype",
        })
        dest.parent.mkdir(parents=True, exist_ok=True)
        result.save(dest, garbage=4, deflate=True)
        result.close()
    dest.chmod(0o600)
    return {"source_pages": total_pages, "review_sheets": note_pages,
            "total_pages": total_pages + note_pages, "note_count": len(notes)}


def main():
    parser = argparse.ArgumentParser(description="Privately annotate a source guide with review sheets")
    parser.add_argument("source_pdf")
    parser.add_argument("--notes", required=True, help="PRIVATE JSON array of review notes")
    parser.add_argument("--output", required=True, help="PRIVATE output filename")
    args = parser.parse_args()
    raw = json.loads(Path(args.notes).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        parser.error("Notes JSON must be an array")
    notes = [ReviewNote(**{**entry, "evidence_ids": tuple(entry.get("evidence_ids", ()))}) for entry in raw]
    print(json.dumps(annotate_pdf(args.source_pdf, notes, args.output)))


if __name__ == "__main__":
    main()
