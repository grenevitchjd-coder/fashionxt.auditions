"""
Server-generated PDFs — real downloadable files that work on any device
(including browsers with no print dialog, like the Meta Quest browser).

Shared look for every FashioNXT printout: logo header, brass accent rule,
card-style sections, "Page X of Y" footer. Later printouts (day-of check-in,
hair, make-up, headshots) reuse the helpers in this file.
"""
import io
import re
from datetime import datetime, timezone
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import (
    CondPageBreak, Flowable, HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Designer, DesignerAssignment, ShowDay

router = APIRouter(tags=["printing"])

# ---- Brand palette (matches the app) -------------------------------------
INK = colors.HexColor("#14151a")
MUTED = colors.HexColor("#5f6368")
BRASS = colors.HexColor("#a87c3f")
BRASS_DARK = colors.HexColor("#7d5c2c")
HEAD_BG = colors.HexColor("#f6efe3")      # soft warm tint for card headers
NOTE_BG = colors.HexColor("#fbf5ea")
ZEBRA = colors.HexColor("#faf7f1")
CARD_BORDER = colors.HexColor("#d8cfbd")
ROW_LINE = colors.HexColor("#e7e0d1")

# ---- FashioNXT wordmark as vector path data (1054 x 134 units) -----------
LOGO_W, LOGO_H = 1054, 134
LOGO_PATH = "M569 1C569 2 569 29 569 62C570 128 569 125 577 129C588 135 592 133 653 84C682 61 705 42 705 42C706 42 706 62 706 86L706 131 742 131L778 131 800 112C812 101 824 90 828 87L834 82 872 106L910 131 982 131L1054 131 1054 118L1054 105 1023 105L992 105 992 52L992 0 977 0L962 0 962 53L962 105 939 105L915 105 885 86C868 75 854 66 854 65C854 65 870 50 890 33C910 16 926 1 926 1C926 0 917 0 905 0L884 0 856 25L828 49 790 24L752 0 732 0C714 1 712 1 709 3C707 4 681 24 653 47L600 89 600 45L600 0 585 0C573 0 570 0 569 1ZM223 3C198 6 184 18 181 38C180 47 180 47 184 47C188 47 188 47 188 41C191 20 209 8 236 9C259 11 273 25 269 43C267 54 257 60 236 65C199 73 189 78 185 92C178 117 202 137 234 133C258 130 271 119 274 100L274 95 270 95C266 95 266 95 266 98C266 129 202 138 193 108C188 91 197 81 225 75C255 68 261 66 268 59C283 46 279 20 261 9C252 4 235 1 223 3ZM484 3C455 9 438 33 438 68C438 108 460 133 497 133C524 133 544 119 553 93C557 81 557 57 553 45C543 14 516 -3 484 3ZM0 68L0 131 39 131L78 131 78 128L78 124 43 124L8 124 7 99L7 74 39 74L71 74 71 70L71 67 39 67L7 67 7 36L7 5 4 5L0 5 0 68ZM92 68L118 130 122 130L126 130 149 72C162 40 173 12 174 9L176 5 172 5C168 5 167 5 166 8C165 10 162 19 158 28L152 45 122 45L92 45 84 25L76 5 71 5L67 5 92 68ZM295 68L296 130 299 131L303 131 303 103L303 74 343 74L383 74 383 102L383 131 387 131L391 131 391 68L391 5 387 5L383 5 383 36L383 67 343 67L303 67 303 36L303 5 299 5L295 5 295 68ZM413 68L413 131 417 131L420 130 421 68L421 5 417 5L413 5 413 68ZM509 11C539 18 555 53 546 86C531 142 458 140 446 84C437 38 470 0 509 11ZM773 43C792 55 808 66 808 66C808 67 738 128 737 129C736 129 736 105 736 74C736 44 736 20 737 20C737 20 754 31 773 43ZM149 54C148 55 142 70 135 89C128 107 122 122 122 121C121 120 95 55 95 53C95 52 104 52 122 52C148 52 149 52 149 54Z"

MARGIN = 0.6 * inch
PAGE_W = letter[0] - 2 * MARGIN - 12  # 12 = the frame's built-in 6pt padding each side

# ---- Text styles ----------------------------------------------------------
SUMMARY = ParagraphStyle("summary", fontName="Helvetica", fontSize=10.5, leading=14, textColor=MUTED, spaceBefore=2, spaceAfter=12)
DESIGNER_NAME = ParagraphStyle("dn", fontName="Times-Bold", fontSize=18, leading=21, textColor=INK)
NOTES_LABEL = ParagraphStyle("nl", fontName="Helvetica-Bold", fontSize=8, leading=10, textColor=BRASS_DARK)
NOTES_TEXT = ParagraphStyle("nt", fontName="Helvetica", fontSize=11, leading=14.5, textColor=INK)
MODEL_NUM = ParagraphStyle("mn", fontName="Helvetica-Bold", fontSize=13, leading=17, textColor=BRASS_DARK, alignment=2)
MODEL_NAME = ParagraphStyle("mname", fontName="Helvetica", fontSize=13, leading=17, textColor=INK)
EMPTY = ParagraphStyle("empty", fontName="Helvetica-Oblique", fontSize=11, leading=14, textColor=MUTED)


# ---- Reusable flowables ---------------------------------------------------
def _draw_logo(c, x, y, width):
    """Draws the FashioNXT wordmark with its lower-left corner at (x, y)."""
    scale = width / LOGO_W
    c.saveState()
    c.translate(x, y)
    c.scale(scale, scale)
    c.setFillColor(INK)
    p = c.beginPath()
    for cmd, args in re.findall(r"([MLCZ])([^MLCZ]*)", LOGO_PATH):
        nums = [float(n) for n in args.split()]
        if cmd == "M":
            p.moveTo(*nums[:2])
        elif cmd == "L":
            for i in range(0, len(nums), 2):
                p.lineTo(nums[i], nums[i + 1])
        elif cmd == "C":
            for i in range(0, len(nums), 6):
                p.curveTo(*nums[i:i + 6])
        elif cmd == "Z":
            p.close()
    c.drawPath(p, stroke=0, fill=1, fillMode=1)  # 1 = even-odd (keeps the letter counters open)
    c.restoreState()


class PageHeader(Flowable):
    """Logo on the left; day name, date and copy type on the right."""

    def __init__(self, title: str, subtitle: str, tag: str, width: float = PAGE_W):
        super().__init__()
        self.title, self.subtitle, self.tag = title, subtitle, tag
        self.width, self.height = width, 0.85 * inch

    def draw(self):
        c = self.canv
        _draw_logo(c, 0, 0.30 * inch, 2.35 * inch)
        c.setFillColor(INK)
        c.setFont("Times-Bold", 24)
        c.drawRightString(self.width, 0.52 * inch, self.title)
        c.setFillColor(BRASS_DARK)
        c.setFont("Helvetica", 10.5)
        c.drawRightString(self.width, 0.34 * inch, self.subtitle)
        # copy-type tag
        c.setFont("Helvetica-Bold", 8)
        tag_w = c.stringWidth(self.tag, "Helvetica-Bold", 8) + 14
        c.setStrokeColor(BRASS)
        c.setFillColor(BRASS_DARK)
        c.setLineWidth(1)
        c.roundRect(self.width - tag_w, 0.06 * inch, tag_w, 0.19 * inch, 3, stroke=1, fill=0)
        c.drawCentredString(self.width - tag_w / 2, 0.115 * inch, self.tag)


class NumberBadge(Flowable):
    """Filled brass circle with a white number."""

    def __init__(self, number, size=0.40 * inch):
        super().__init__()
        self.number, self.size = str(number), size
        self.width = self.height = size

    def draw(self):
        c = self.canv
        r = self.size / 2
        c.setFillColor(BRASS)
        c.circle(r, r, r, stroke=0, fill=1)
        c.setFillColor(colors.white)
        font_size = 14 if len(self.number) < 3 else 11
        c.setFont("Helvetica-Bold", font_size)
        c.drawCentredString(r, r - font_size * 0.35, self.number)


class NumberedCanvas(pdfcanvas.Canvas):
    """Two-pass canvas so the footer can say 'Page X of Y'."""

    footer_left = ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved = []

    def showPage(self):
        self._saved.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved)
        for state in self._saved:
            self.__dict__.update(state)
            self._draw_footer(total)
            super().showPage()
        super().save()

    def _draw_footer(self, total):
        self.saveState()
        self.setStrokeColor(CARD_BORDER)
        self.setLineWidth(0.8)
        self.line(MARGIN + 6, 0.58 * inch, letter[0] - MARGIN - 6, 0.58 * inch)
        self.setFont("Helvetica", 8)
        self.setFillColor(MUTED)
        self.drawString(MARGIN + 6, 0.40 * inch, self.footer_left)
        self.drawRightString(letter[0] - MARGIN - 6, 0.40 * inch, f"Page {self._pageNumber} of {total}")
        self.restoreState()


# ---- Helpers --------------------------------------------------------------
def _day_title(day: ShowDay) -> str:
    if not day.show_date:
        return ""
    d = day.show_date
    return f"{d:%B} {d.day}, {d.year}"


def _printed_stamp() -> str:
    now = datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        local = now.astimezone(ZoneInfo("America/Los_Angeles"))
        return f"{local:%b} {local.day}, {local.year} {local:%I:%M %p} PT"
    except Exception:
        return f"{now:%b} {now.day}, {now.year} {now:%H:%M} UTC"


def _category_label(category) -> str:
    value = category.value if hasattr(category, "value") else str(category)
    return "-".join(part.capitalize() for part in value.split("_"))


def _designer_block(designer: Designer, show_notes: bool):
    """One designer card. A single table, so a long lineup can flow onto the next
    page, with the designer header repeating at the top of that page."""
    W = PAGE_W
    count = len(designer.assignments)
    count_html = (
        f"<font name='Helvetica-Bold' size='20' color='#7d5c2c'>{count}</font>"
        f"<font name='Helvetica-Bold' size='9' color='#5f6368'>&nbsp;MODEL{'' if count == 1 else 'S'}</font>"
    )
    head = Table(
        [[
            NumberBadge(designer.order_in_day),
            Paragraph(escape(designer.name), DESIGNER_NAME),
            Paragraph(count_html, ParagraphStyle("cnt", fontName="Helvetica", fontSize=20, leading=24, alignment=2)),
        ]],
        colWidths=[0.70 * inch, W - 0.70 * inch - 1.5 * inch, 1.5 * inch],
    )
    head.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 0), (-1, -1), HEAD_BG),
        ("LINEBELOW", (0, 0), (-1, -1), 1.4, BRASS),
        ("LEFTPADDING", (0, 0), (0, 0), 12),
        ("LEFTPADDING", (1, 0), (1, 0), 4),
        ("RIGHTPADDING", (-1, 0), (-1, 0), 14),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))

    NUM_W = 0.55 * inch
    col_widths = [NUM_W, W - NUM_W]
    rows = [[head, ""]]
    span_rows = [0]

    has_notes = bool(show_notes and designer.notes)
    if has_notes:
        notes = Table(
            [[Paragraph("NOTES", NOTES_LABEL)],
             [Paragraph(escape(designer.notes).replace("\n", "<br/>"), NOTES_TEXT)]],
            colWidths=[W - 20 - 3],
        )
        notes.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), NOTE_BG),
            ("LINEBEFORE", (0, 0), (0, -1), 3, BRASS),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        notes_wrap = Table([[notes]], colWidths=[W])
        notes_wrap.setStyle(TableStyle([
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        rows.append([notes_wrap, ""])
        span_rows.append(len(rows) - 1)

    first_model_row = len(rows)
    if not designer.assignments:
        rows.append([Paragraph("No models assigned yet.", EMPTY), ""])
        span_rows.append(len(rows) - 1)
    else:
        for idx, a in enumerate(designer.assignments, start=1):
            rows.append([
                Paragraph(str(idx), MODEL_NUM),
                Paragraph(
                    f"{escape(a.applicant.full_name)}"
                    f"<font name='Helvetica' size='10.5' color='#5f6368'>&nbsp;&nbsp;({_category_label(a.applicant.category)})</font>",
                    MODEL_NAME,
                ),
            ])

    style = [
        ("BOX", (0, 0), (-1, -1), 0.9, CARD_BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        # header + notes rows: no padding of their own (their inner tables handle it)
        ("LEFTPADDING", (0, 0), (-1, first_model_row - 1), 0),
        ("RIGHTPADDING", (0, 0), (-1, first_model_row - 1), 0),
        ("TOPPADDING", (0, 0), (-1, first_model_row - 1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, first_model_row - 1), 0),
        # model rows
        ("TOPPADDING", (0, first_model_row), (-1, -1), 5),
        ("BOTTOMPADDING", (0, first_model_row), (-1, -1), 5),
        ("LEFTPADDING", (0, first_model_row), (0, -1), 6),
        ("LEFTPADDING", (1, first_model_row), (1, -1), 14),
        ("RIGHTPADDING", (-1, first_model_row), (-1, -1), 14),
    ]
    for r in span_rows:
        style.append(("SPAN", (0, r), (-1, r)))
    if not designer.assignments:
        style += [
            ("LEFTPADDING", (0, first_model_row), (-1, first_model_row), 14),
            ("TOPPADDING", (0, first_model_row), (-1, first_model_row), 10),
            ("BOTTOMPADDING", (0, first_model_row), (-1, first_model_row), 10),
        ]
    else:
        last = len(rows) - 1
        for r in range(first_model_row, last):
            style.append(("LINEBELOW", (0, r), (-1, r), 0.5, ROW_LINE))
        for r in range(first_model_row + 1, last + 1, 2):
            style.append(("BACKGROUND", (0, r), (-1, r), ZEBRA))

    card = Table(rows, colWidths=col_widths, repeatRows=1)
    card.setStyle(TableStyle(style))

    # Start the card on a fresh page unless there's room for a decent chunk of it
    # (the whole card if it is short) — avoids a header stranded at the page bottom.
    estimate = 0.8 * inch + (0.9 * inch if has_notes else 0) + max(count, 1) * 0.34 * inch
    return [CondPageBreak(min(estimate, 4.2 * inch)), card, Spacer(1, 14)]


def build_designer_day_pdf(day: ShowDay, designers: list[Designer], show_notes: bool) -> bytes:
    copy_label = "STAFF COPY" if show_notes else "DESIGNER COPY"
    stamp = _printed_stamp()
    buffer = io.BytesIO()

    class _Canvas(NumberedCanvas):
        footer_left = f"FashioNXT Week  |  {day.name}  |  {copy_label.title()}  |  Printed {stamp}"

    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        leftMargin=MARGIN, rightMargin=MARGIN, topMargin=0.55 * inch, bottomMargin=0.8 * inch,
        title=f"FashioNXT {day.name} - {copy_label.title()}", author="FashioNXT",
    )

    total_models = sum(len(d.assignments) for d in designers)
    story = [
        PageHeader(day.name, _day_title(day), copy_label),
        HRFlowable(width="100%", thickness=2.2, color=BRASS, spaceBefore=2, spaceAfter=4),
        Paragraph(
            f"{len(designers)} designer{'' if len(designers) == 1 else 's'} &nbsp;&middot;&nbsp; "
            f"{total_models} model assignment{'' if total_models == 1 else 's'}",
            SUMMARY,
        ),
    ]

    if not designers:
        story.append(Paragraph("No designers have been added to this day yet.", EMPTY))
    for d in designers:
        story.extend(_designer_block(d, show_notes))

    doc.build(story, canvasmaker=_Canvas)
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