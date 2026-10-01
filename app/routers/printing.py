"""
Server-generated PDFs — real downloadable files that work on any device
(including browsers with no print dialog, like the Meta Quest browser).
"""
import io
from datetime import datetime, timezone
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Designer, DesignerAssignment, ShowDay

router = APIRouter(tags=["printing"])

INK = colors.HexColor("#14151a")
MUTED = colors.HexColor("#555555")
LINE = colors.HexColor("#999999")
NOTE_BG = colors.HexColor("#f3f3f3")

H1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=22, leading=26, textColor=INK, alignment=TA_LEFT)
SUB = ParagraphStyle("sub", fontName="Helvetica", fontSize=11, leading=14, textColor=MUTED, spaceAfter=14)
DESIGNER_NAME = ParagraphStyle("dn", fontName="Helvetica-Bold", fontSize=14, leading=17, textColor=INK)
DESIGNER_COUNT = ParagraphStyle("dc", fontName="Helvetica", fontSize=9, leading=12, textColor=MUTED, alignment=2)
NOTES_LABEL = ParagraphStyle("nl", fontName="Helvetica-Bold", fontSize=7.5, leading=10, textColor=INK)
NOTES_TEXT = ParagraphStyle("nt", fontName="Helvetica", fontSize=10, leading=13, textColor=INK)
MODEL_NUM = ParagraphStyle("mn", fontName="Helvetica-Bold", fontSize=10.5, leading=14, textColor=INK, alignment=2)
MODEL_NAME = ParagraphStyle("mname", fontName="Helvetica", fontSize=10.5, leading=14, textColor=INK)
EMPTY = ParagraphStyle("empty", fontName="Helvetica-Oblique", fontSize=10, leading=13, textColor=MUTED)

PAGE_W = letter[0] - 1.2 * inch  # 0.6in margins left and right


def _day_title(day: ShowDay) -> str:
    if not day.show_date:
        return ""
    d = day.show_date
    return f"{d:%A, %B} {d.day}, {d.year}"


def _printed_stamp() -> str:
    now = datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        local = now.astimezone(ZoneInfo("America/Los_Angeles"))
        return f"{local:%b} {local.day}, {local.year} {local:%I:%M %p} PT"
    except Exception:
        return f"{now:%b} {now.day}, {now.year} {now:%H:%M} UTC"


def _designer_block(designer: Designer, show_notes: bool):
    head = Table(
        [[
            Paragraph(str(designer.order_in_day), ParagraphStyle("num", parent=DESIGNER_NAME, alignment=1)),
            Paragraph(escape(designer.name), DESIGNER_NAME),
            Paragraph(
                f"{len(designer.assignments)} model{'' if len(designer.assignments) == 1 else 's'}",
                DESIGNER_COUNT,
            ),
        ]],
        colWidths=[0.4 * inch, PAGE_W - 0.4 * inch - 0.4 * inch - 1.1 * inch, 1.1 * inch],
    )
    head.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOX", (0, 0), (0, 0), 1.2, INK),
        ("LINEBELOW", (1, 0), (-1, 0), 0.6, LINE),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
    ]))

    rows = [[head]]

    if show_notes and designer.notes:
        notes = Table(
            [[Paragraph("NOTES", NOTES_LABEL)],
             [Paragraph(escape(designer.notes).replace("\n", "<br/>"), NOTES_TEXT)]],
            colWidths=[PAGE_W - 0.4 * inch],
        )
        notes.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), NOTE_BG),
            ("LINEBEFORE", (0, 0), (0, -1), 2.5, INK),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        rows.append([Spacer(1, 4)])
        rows.append([notes])
        rows.append([Spacer(1, 4)])

    if not designer.assignments:
        rows.append([Paragraph("No models assigned yet.", EMPTY)])
    else:
        model_rows = []
        for idx, a in enumerate(designer.assignments, start=1):
            category = a.applicant.category.value if hasattr(a.applicant.category, "value") else str(a.applicant.category)
            category = category.replace("_", "-")
            model_rows.append([
                Paragraph(str(idx), MODEL_NUM),
                Paragraph(f"{escape(a.applicant.full_name)} <font size='9' color='#555555'>({escape(category)})</font>", MODEL_NAME),
            ])
        models = Table(model_rows, colWidths=[0.4 * inch, PAGE_W - 0.8 * inch])
        models.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LINEBELOW", (0, 0), (-1, -2), 0.4, colors.HexColor("#bbbbbb")),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        rows.append([models])

    outer = Table(rows, colWidths=[PAGE_W - 0.2 * inch])
    outer.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 1.2, INK),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    # Keep a designer's whole block on one page when it fits.
    return KeepTogether([outer, Spacer(1, 10)])


def build_designer_day_pdf(day: ShowDay, designers: list[Designer], show_notes: bool) -> bytes:
    copy_label = "Staff copy" if show_notes else "Designer copy"
    stamp = _printed_stamp()
    buffer = io.BytesIO()

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(0.6 * inch, 0.4 * inch, f"FashioNXT Week - {day.name} - {copy_label} - Printed {stamp}")
        canvas.drawRightString(letter[0] - 0.6 * inch, 0.4 * inch, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        leftMargin=0.6 * inch, rightMargin=0.6 * inch, topMargin=0.6 * inch, bottomMargin=0.7 * inch,
        title=f"FashioNXT {day.name} - {copy_label}", author="FashioNXT",
    )

    story = [Paragraph(f"FashioNXT Week &mdash; {escape(day.name)}", H1)]
    sub_bits = [b for b in (_day_title(day), copy_label) if b]
    story.append(Paragraph(" &middot; ".join(escape(b) for b in sub_bits), SUB))

    if not designers:
        story.append(Paragraph("No designers have been added to this day yet.", EMPTY))
    for d in designers:
        story.append(_designer_block(d, show_notes))

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


@router.get("/designers/print-pdf")
async def designers_print_pdf(
    show_day_id: int,
    notes: bool = Query(False, description="Include designer notes (staff copy)"),
    db: AsyncSession = Depends(get_db),
):
    """One show day's designers + lineups as a downloadable PDF. notes=1 -> staff copy, notes=0 -> designer copy."""
    day = await db.get(ShowDay, show_day_id)
    if not day:
        raise HTTPException(status_code=404, detail="Show day not found")

    result = await db.execute(
        select(Designer)
        .where(Designer.show_day_id == show_day_id)
        .options(selectinload(Designer.assignments).selectinload(DesignerAssignment.applicant))
        .order_by(Designer.order_in_day)
    )
    designers = result.scalars().all()

    pdf = build_designer_day_pdf(day, designers, show_notes=notes)
    label = "Staff" if notes else "Designer"
    filename = f"FashioNXT_{day.name}_{label}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )