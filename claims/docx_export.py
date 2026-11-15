"""Word (.docx) export of letters on the office letterhead."""

from io import BytesIO
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

from .letters import parts

LETTERHEAD = Path(__file__).parent / "static" / "claims" / "letterhead.jpg"
FONT = "Cambria"
JUSTIFY = WD_ALIGN_PARAGRAPH.JUSTIFY


def _para(doc, text="", *, bold=False, align=None, indent=None, first=None,
          after=5, size=None, underline=False):
    p = doc.add_paragraph()
    fmt = p.paragraph_format
    fmt.space_after = Pt(after)
    fmt.space_before = Pt(0)
    if align is not None:
        p.alignment = align
    if indent is not None:
        fmt.left_indent = Cm(indent)
    if first is not None:
        fmt.first_line_indent = Cm(first)
    if text:
        run = p.add_run(text)
        run.bold = bold
        run.underline = underline
        if size:
            run.font.size = Pt(size)
    return p


def _top_border(paragraph):
    borders = OxmlElement("w:pBdr")
    top = OxmlElement("w:top")
    for key, value in (("val", "single"), ("sz", "8"), ("space", "4"),
                       ("color", "000000")):
        top.set(qn(f"w:{key}"), value)
    borders.append(top)
    paragraph._p.get_or_add_pPr().append(borders)


def letter_docx(comm):
    ctx = parts(comm)
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = FONT
    style.font.size = Pt(10.5)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)

    section = doc.sections[0]
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.left_margin, section.right_margin = Cm(1.9), Cm(1.7)
    section.top_margin, section.bottom_margin = Cm(0.9), Cm(1.9)
    width = section.page_width - section.left_margin - section.right_margin

    head = doc.add_paragraph()
    head.alignment = WD_ALIGN_PARAGRAPH.CENTER
    head.paragraph_format.space_after = Pt(2)
    head.add_run().add_picture(str(LETTERHEAD), width=width)

    table = doc.add_table(rows=1, cols=2)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    left, right = table.rows[0].cells
    left.paragraphs[0].add_run("No. " + ctx["number"])
    right.paragraphs[0].add_run("Date: " + ctx["date"])
    right.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT

    def to_block(keep=False):
        first = _para(doc, "To", after=0)
        first.paragraph_format.space_before = Pt(6)
        first.paragraph_format.keep_with_next = True
        for i, line in enumerate(ctx["to_lines"]):
            last = i == len(ctx["to_lines"]) - 1
            p = _para(doc, line, after=8 if (keep and last) else 0)
            p.paragraph_format.keep_with_next = not last

    if comm.is_uo:
        _para(doc, "U.O. NOTE", bold=True, underline=True,
              align=WD_ALIGN_PARAGRAPH.CENTER, after=6, size=12).paragraph_format \
            .space_before = Pt(6)
    else:
        to_block()

    sub = _para(doc, after=2, indent=2.6, first=-1.1, align=JUSTIFY)
    sub.paragraph_format.space_before = Pt(10)
    sub.add_run("Sub:\t")
    sub.add_run(comm.subject).bold = True
    for i, ref in enumerate(ctx["references"]):
        label = "Ref:\t" if i == 0 else "\t"
        numbered = f"{i + 1}. {ref}" if len(ctx["references"]) > 1 else ref
        _para(doc, label + numbered, after=2, indent=2.6, first=-1.1)
    _para(doc, "****", align=WD_ALIGN_PARAGRAPH.CENTER, after=4)

    if comm.salutation:
        _para(doc, comm.salutation, after=4)
    for text in ctx["intro"]:
        _para(doc, text, align=JUSTIFY, first=1.25)
    for i, point in enumerate(comm.points, start=1):
        if ctx["numbered"]:
            _para(doc, f"{i}.\t{point}", align=JUSTIFY, indent=1.6, first=-0.8, after=4)
        else:
            _para(doc, point, align=JUSTIFY, first=1.25)
    for item in ctx["closing"]:
        p = _para(doc, item["text"], align=JUSTIFY,
                  first=None if item["flush"] else 1.25)
        if item["urgent"]:
            p.runs[0].bold = True

    sign = _para(doc, "" if comm.is_uo else "Yours faithfully,", indent=11, after=24)
    sign.paragraph_format.space_before = Pt(6)
    sign.paragraph_format.keep_with_next = True
    _para(doc, ctx["signatory"] + ",", indent=11, after=0).paragraph_format \
        .keep_with_next = True
    _para(doc, ctx["signatory_office"], indent=11, after=8)

    if comm.is_uo:
        to_block(keep=True)

    if ctx["copies"]:
        _para(doc, "Copy to:" if comm.is_uo else "COPY TO:", bold=True, underline=True,
              after=3).paragraph_format.keep_with_next = True
        single = len(ctx["copies"]) == 1
        for i, block in enumerate(ctx["copies"], start=1):
            for j, line in enumerate(block):
                prefix = "" if single else (f"{i}.\t" if j == 0 else "\t")
                last = j == len(block) - 1
                p = _para(doc, prefix + line, indent=None if single else 0.8,
                          first=None if single else -0.8, after=4 if last else 0)
                p.paragraph_format.keep_with_next = not last
                p.paragraph_format.keep_together = True

    if ctx["footer_lines"]:
        footer = section.footer
        for i, line in enumerate(ctx["footer_lines"]):
            p = footer.paragraphs[0] if i == 0 else footer.add_paragraph()
            p.paragraph_format.space_after = Pt(0)
            run = p.add_run(line)
            run.bold = True
            run.font.size = Pt(9)
            if i == 0:
                _top_border(p)

    buffer = BytesIO()
    doc.save(buffer)
    return buffer.getvalue()
