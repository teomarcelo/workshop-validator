"""Place compact instructor checks directly under affected PDF steps.

Copies the source page as exact-scale, clipped vector strips and inserts new
vertical space at reviewed anchors. Affected pages get taller; untouched pages
keep their original dimensions and rendered appearance. All data stays private.
"""
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
import fitz

DARK=(.09,.19,.27)
TEAL=(.04,.39,.47)
MUTED=(.36,.44,.48)
PALE=(.98,.97,.94)

@dataclass(frozen=True)
class InlineNote:
    source_page: int
    step_id: str
    insert_after_y: float  # reviewed top-origin PDF coordinate in points
    summary: str
    next_check: str
    status: str = "NEEDS_REVIEW"

def _wrap(text, width, size):
    out=[]
    line=""
    for word in " ".join(text.split()).split():
        candidate=(line+" "+word).strip()
        if line and fitz.get_text_length(candidate,fontname="helv",fontsize=size)>width:
            out.append(line)
            line=word
        else:
            line=candidate
    if line:
        out.append(line)
    return out or ["(none)"]

def _height(note, width):
    usable=width-128
    return max(79.,42+13*len(_wrap(note.summary,usable,9))+
               12*len(_wrap("Test: "+note.next_check,usable,8.4))+12)

def _card(page, y, note, height):
    left,right=77.,page.rect.width-64
    top,bottom=y+7,y+height-7
    page.draw_rect(fitz.Rect(left,top,right,bottom),
                   color=(.83,.78,.67),fill=PALE,width=.7)
    page.draw_rect(fitz.Rect(left,top,left+4,bottom),
                   color=None,fill=(.92,.65,.22))
    x=left+16
    page.insert_text((x,top+16),"INSTRUCTOR CHECK  |  "+note.step_id,
                     fontname="hebo",fontsize=8.1,color=TEAL)
    cursor=top+33
    for line in _wrap(note.summary,right-x-13,9):
        page.insert_text((x,cursor),line,fontname="helv",fontsize=9,color=DARK)
        cursor+=13
    for line in _wrap("Test: "+note.next_check,right-x-13,8.4):
        page.insert_text((x,cursor),line,fontname="helv",fontsize=8.4,color=MUTED)
        cursor+=12
    if cursor>bottom-3:
        raise ValueError("Review note text overflow")

def _summary(out, notes):
    page=out.new_page(width=612,height=792)
    page.draw_rect(fitz.Rect(0,0,612,11),color=None,fill=TEAL)
    page.insert_text((62,95),"INSTRUCTOR QUICK REVIEW",fontname="hebo",fontsize=17,color=TEAL)
    page.insert_text((62,125),f"{len(notes)} step checks in the guide",fontsize=12,color=DARK)
    page.insert_text((62,147),"Short notes appear immediately below affected instructions.",
                     fontsize=9.6,color=MUTED)
    page.draw_rect(fitz.Rect(62,172,550,217),color=(.9,.77,.46),fill=PALE,width=.8)
    page.insert_text((78,190),"STATUS: REVIEW COPY - VERIFY IN LIVE ORG",
                     fontname="hebo",fontsize=9,color=(.49,.29,.09))
    page.insert_text((78,206),"Notes alone do not prove that Salesforce guide steps passed.",
                     fontsize=8.7,color=DARK)
    y=258
    shown=0
    for note in notes[:8]:
        if y>620:
            break
        page.insert_text((62,y),f"SOURCE PAGE {note.source_page}  |  {note.step_id}",
                         fontsize=9,fontname="hebo",color=TEAL)
        y+=19
        for line in _wrap(note.summary,462,9.6)[:2]:
            page.insert_text((62,y),line,fontname="helv",fontsize=9.6,color=DARK)
            y+=15
        y+=21
        shown+=1
    if len(notes)>shown:
        page.insert_text((62,min(665,y+6)),f"Plus {len(notes)-shown} other inline notes.",
                         fontsize=9.3,color=MUTED)
    page.draw_line((62,689),(550,689),color=(.78,.83,.85))
    page.insert_text((62,712),"Test before-state and safely skip suspected redundant edits.",
                     fontsize=9,color=MUTED)
    page.insert_text((62,727),"Verify Save and downstream behavior before changing the guide.",
                     fontsize=9,color=MUTED)

def annotate_pdf_inline(source_pdf: str | Path, notes: list[InlineNote],
                        destination: str | Path, *, quick_summary: bool = True) -> dict:
    """Produce a private inline-notes PDF without covering original content.

    Source fragments keep their vector coordinates/scale except that following
    fragments are moved down. Caller supplies a reviewed, source-specific y
    coordinate; unknown anchors must not silently produce unrelated notes.
    """
    source,dest=Path(source_pdf),Path(destination)
    if source.resolve()==dest.resolve():
        raise ValueError("Cannot overwrite the source PDF")
    if dest.exists():
        raise FileExistsError("Refusing to overwrite existing review output")
    if not source.is_file():
        raise FileNotFoundError(source)
    placements=defaultdict(list)
    with fitz.open(source) as original:
        total=len(original)
        for note in notes:
            if (type(note.source_page) is not int or not 1<=note.source_page<=total or
                    type(note.insert_after_y) not in (int,float) or
                    not 36<note.insert_after_y<original[note.source_page-1].rect.height-38 or
                    not isinstance(note.step_id,str) or not note.step_id.strip() or
                    not isinstance(note.summary,str) or not note.summary.strip() or
                    not isinstance(note.next_check,str) or
                    len(note.summary)>240 or len(note.next_check)>240):
                raise ValueError("Each note needs a valid source anchor and concise summary")
            placements[note.source_page].append(note)
        destdoc=fitz.open()
        if quick_summary:
            _summary(destdoc,notes)
        for number,sourcepage in enumerate(original,1):
            page_notes=sorted(placements.get(number,()),key=lambda n:n.insert_after_y)
            width,height=sourcepage.rect.width,sourcepage.rect.height
            sizes=[_height(note,width) for note in page_notes]
            if height+sum(sizes)>14400:
                raise ValueError("Annotated page exceeds supported page height")
            if any(b.insert_after_y-a.insert_after_y<3
                   for a,b in zip(page_notes,page_notes[1:])):
                raise ValueError("Overlapping insertion anchors")
            destpage=destdoc.new_page(width=width,height=height+sum(sizes))
            start=0.
            offset=0.
            for note,note_height in zip(page_notes,sizes):
                cut=note.insert_after_y
                destpage.show_pdf_page(
                    fitz.Rect(0,start+offset,width,cut+offset),original,number-1,
                    clip=fitz.Rect(0,start,width,cut),keep_proportion=False)
                _card(destpage,cut+offset,note,note_height)
                start=cut
                offset+=note_height
            if start<height:
                destpage.show_pdf_page(
                    fitz.Rect(0,start+offset,width,height+offset),original,number-1,
                    clip=fitz.Rect(0,start,width,height),keep_proportion=False)
        destdoc.set_metadata({
            "title":"Instructor quick review - inline PDF annotations",
            "subject":"Private review copy - notes need live verification",
            "creator":"Workshop Validator",
        })
        dest.parent.mkdir(parents=True,exist_ok=True)
        destdoc.save(dest,garbage=4,deflate=True)
        destdoc.close()
    dest.chmod(0o600)
    return {"source_pages":total,"total_pages":total+int(quick_summary),
            "note_count":len(notes),"inline":True,
            "affected_source_pages":sorted(placements)}

def main():
    import argparse
    import json
    parser=argparse.ArgumentParser(description="Private step-anchored PDF review")
    parser.add_argument("source_pdf")
    parser.add_argument("--notes",required=True,help="PRIVATE JSON list of inline note placements")
    parser.add_argument("--output",required=True)
    args=parser.parse_args()
    entries=json.loads(Path(args.notes).read_text(encoding="utf-8"))
    if not isinstance(entries,list):
        parser.error("Notes must be a JSON array")
    print(json.dumps(annotate_pdf_inline(args.source_pdf,
          [InlineNote(**entry) for entry in entries],args.output)))

if __name__=="__main__":
    main()
