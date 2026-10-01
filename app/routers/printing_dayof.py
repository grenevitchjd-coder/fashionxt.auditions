"""
Day-of printouts (PDF downloads):
  GET /day-of/print/check-in-sheet  - clipboard checklist: Models / Designers / Staff
  GET /day-of/print/model-cards     - cut-out cards, 8 per page, one per model

Reuses the shared look from app/routers/printing.py. Notes are never printed.
"""
import io
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import CondPageBreak, Flowable, HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.routers.dayof import load_day_roster
from app.routers.printing import (
    BRASS, BRASS_DARK, CARD_BORDER, EMPTY, HEAD_BG, INK, MARGIN, MUTED, PAGE_W, ROW_LINE, SUMMARY, ZEBRA,
    NumberedCanvas, PageHeader, _draw_logo, _printed_stamp,
)

router = APIRouter(prefix="/day-of/print", tags=["day-of-print"])


def _date_title(day: dict) -> str:
    iso = day.get("show_date")
    if not iso:
        return ""
    from datetime import date
    d = date.fromisoformat(iso)
    return f"{d:%B} {d.day}, {d.year}"


def _pdf_response(pdf: bytes, filename: str) -> Response:
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Check-in sheet
# ---------------------------------------------------------------------------
ROW_NAME = ParagraphStyle("rn", fontName="Helvetica-Bold", fontSize=12.5, leading=15, textColor=INK)
ROW_SUB = ParagraphStyle("rs", fontName="Helvetica", fontSize=9.5, leading=12, textColor=MUTED)
SECTION = ParagraphStyle("sec", fontName="Times-Bold", fontSize=16, leading=19, textColor=INK)
SECTION_COUNT = ParagraphStyle("secc", fontName="Helvetica-Bold", fontSize=9, leading=12, textColor=BRASS_DARK, alignment=2)


class CheckBox(Flowable):
    """Empty square for a pen tick; drawn with a tick when already checked in."""

    def __init__(self, checked=False, size=13):
        super().__init__()
        self.checked, self.size = checked, size
        self.width = self.height = size

    def draw(self):
        c = self.canv
        c.setStrokeColor(INK)
        c.setLineWidth(1.2)
        c.rect(0, 0, self.size, self.size, stroke=1, fill=0)
        if self.checked:
            c.setStrokeColor(BRASS_DARK)
            c.setLineWidth(2)
            s = self.size
            c.lines([(s * 0.2, s * 0.5, s * 0.42, s * 0.22), (s * 0.42, s * 0.22, s * 0.85, s * 0.82)])


def _section_table(title: str, rows: list[tuple[bool, str, str]]):
    """rows = [(checked, name, subline)]. One splittable table; the heading repeats on a new page."""
    W = PAGE_W
    head = [Paragraph(escape(title), SECTION), "", Paragraph(f"{len(rows)} TOTAL", SECTION_COUNT)]
    data = [head]
    for checked, name, sub in rows:
        cell = [Paragraph(escape(name), ROW_NAME)]
        if sub:
            cell.append(Paragraph(sub, ROW_SUB))
        data.append([CheckBox(checked), cell, ""])

    t = Table(data, colWidths=[0.55 * inch, W - 0.55 * inch - 1.2 * inch, 1.2 * inch], repeatRows=1)
    style = [
        ("BOX", (0, 0), (-1, -1), 0.9, CARD_BORDER),
        ("SPAN", (0, 0), (1, 0)),
        ("BACKGROUND", (0, 0), (-1, 0), HEAD_BG),
        ("LINEBELOW", (0, 0), (-1, 0), 1.4, BRASS),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (0, 0), 12),
        ("RIGHTPADDING", (-1, 0), (-1, 0), 12),
        ("TOPPADDING", (0, 0), (-1, 0), 8),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
        ("LEFTPADDING", (0, 1), (0, -1), 14),
        ("TOPPADDING", (0, 1), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 6),
    ]
    last = len(data) - 1
    for r in range(1, last):
        style.append(("LINEBELOW", (0, r), (-1, r), 0.5, ROW_LINE))
    for r in range(2, last + 1, 2):
        style.append(("BACKGROUND", (0, r), (-1, r), ZEBRA))
    t.setStyle(TableStyle(style))
    return [CondPageBreak(1.6 * inch), t, Spacer(1, 14)]


def build_check_in_sheet(roster: dict, only_missing: bool) -> bytes:
    day = roster["day"]
    models, designers, staff = roster["models"], roster["designers"], roster["staff"]

    def keep(x):
        return not (only_missing and x["checked_in_at"])

    model_rows = [
        (bool(m["checked_in_at"]), m["full_name"],
         "Walking for: " + " &nbsp;&middot;&nbsp; ".join(f"{d['order_in_day']}. {escape(d['name'])} ({escape(d.get('walkthrough') or 'TBD')})" for d in m["designers"]))
        for m in models if keep(m)
    ]
    designer_rows = [
        (bool(d["checked_in_at"]), d["name"], f"Designer #{d['order_in_day']} &middot; {d['model_count']} model{'' if d['model_count'] == 1 else 's'}"
         f" &middot; Walk-through {escape(d.get('walkthrough') or 'TBD')}")
        for d in designers if keep(d)
    ]
    staff_rows = [(bool(s["checked_in_at"]), s["name"], escape(s["attendee_type"])) for s in staff if keep(s)]

    tag = "NOT YET CHECKED IN" if only_missing else "CHECK-IN SHEET"
    stamp = _printed_stamp()
    buffer = io.BytesIO()

    class _Canvas(NumberedCanvas):
        footer_left = f"FashioNXT Week  |  {day['name']}  |  Check-in sheet  |  Printed {stamp}"

    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        leftMargin=MARGIN, rightMargin=MARGIN, topMargin=0.55 * inch, bottomMargin=0.8 * inch,
        title=f"FashioNXT {day['name']} - Check-In Sheet", author="FashioNXT",
    )

    story = [
        PageHeader(day["name"], _date_title(day), tag),
        HRFlowable(width="100%", thickness=2.2, color=BRASS, spaceBefore=2, spaceAfter=4),
        Paragraph(
            f"{len(models)} models &nbsp;&middot;&nbsp; {len(designers)} designers &nbsp;&middot;&nbsp; {len(staff)} staff"
            + (" &nbsp;&middot;&nbsp; <b>only people not yet checked in</b>" if only_missing else ""),
            SUMMARY,
        ),
    ]

    if not (model_rows or designer_rows or staff_rows):
        story.append(Paragraph(
            "Everyone is checked in." if only_missing else "No one has been added to this day yet.", EMPTY))
    if model_rows:
        story += _section_table("Models", model_rows)
    if designer_rows:
        story += _section_table("Designers", designer_rows)
    if staff_rows:
        story += _section_table("Staff", staff_rows)

    doc.build(story, canvasmaker=_Canvas)
    return buffer.getvalue()


@router.get("/check-in-sheet")
async def check_in_sheet(
    show_day_id: int,
    only_missing: bool = Query(False, description="Only people not yet checked in"),
    db: AsyncSession = Depends(get_db),
):
    roster = await load_day_roster(db, show_day_id)
    pdf = build_check_in_sheet(roster, only_missing)
    suffix = "_NotYetCheckedIn" if only_missing else ""
    return _pdf_response(pdf, f"FashioNXT_{roster['day']['name']}_CheckIn{suffix}.pdf")


# ---------------------------------------------------------------------------
# Model cut-out cards — 2 across x 4 down on a letter page
# ---------------------------------------------------------------------------
CARD_W, CARD_H = 270.0, 180.0
COLS, ROWS = 2, 4
GRID_X = (letter[0] - COLS * CARD_W) / 2
GRID_TOP = letter[1] - (letter[1] - ROWS * CARD_H) / 2


def _fit_font(text: str, font: str, max_size: float, min_size: float, max_width: float) -> float:
    size = max_size
    while size > min_size and stringWidth(text, font, size) > max_width:
        size -= 1
    return size


def _wrap(text: str, font: str, size: float, max_width: float) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if stringWidth(trial, font, size) <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _draw_card(c, x, y, model: dict, day: dict):
    """(x, y) = lower-left corner of the card."""
    pad = 14
    inner_w = CARD_W - 2 * pad
    top = y + CARD_H

    # header: logo left, day + date right
    _draw_logo(c, x + pad, top - 27, 74)
    iso = day.get("show_date")
    label = day["name"].upper()
    if iso:
        from datetime import date
        d = date.fromisoformat(iso)
        label += f"  ·  {d:%b} {d.day}".upper()
    c.setFont("Helvetica-Bold", 8.5)
    c.setFillColor(BRASS_DARK)
    c.drawRightString(x + CARD_W - pad, top - 22, label)
    c.setStrokeColor(BRASS)
    c.setLineWidth(1.4)
    c.line(x + pad, top - 33, x + CARD_W - pad, top - 33)

    # model name (auto-fit, wraps to two lines at most)
    name = model["full_name"]
    font = "Times-Bold"
    size = _fit_font(name, font, 24, 17, inner_w)
    lines = _wrap(name, font, size, inner_w)
    if len(lines) > 2:
        size = 15
        lines = _wrap(name, font, size, inner_w)[:2]
    c.setFillColor(INK)
    c.setFont(font, size)
    ty = top - 33 - 10 - size
    for ln in lines:
        c.drawString(x + pad, ty, ln)
        ty -= size + 2

    # designers
    ty -= 4
    c.setFont("Helvetica-Bold", 7)
    c.setFillColor(MUTED)
    c.drawString(x + pad, ty, "WALKING FOR  (number = order in the show)")
    c.drawRightString(x + CARD_W - pad, ty, "WALK-THROUGH")
    ty -= 8

    designers = model["designers"]
    bottom_limit = y + 10
    avail = ty - bottom_limit
    step = min(20.0, avail / max(len(designers), 1))
    step = max(step, 9.5)
    badge_r = min(7.5, step / 2 - 0.5)
    text_size = min(11.5, step - 3.5)
    for d in designers:
        cy = ty - step / 2
        c.setFillColor(BRASS)
        c.circle(x + pad + badge_r, cy, badge_r, stroke=0, fill=1)
        c.setFillColor(colors.white)
        num = str(d["order_in_day"])
        nsize = badge_r * (1.15 if len(num) < 3 else 0.9)
        c.setFont("Helvetica-Bold", nsize)
        c.drawCentredString(x + pad + badge_r, cy - nsize * 0.35, num)
        c.setFillColor(INK)
        dname = d["name"]
        walk = d.get("walkthrough")
        wtext = walk or "TBD"
        wsize = min(9.5, max(text_size, 7))
        wwidth = stringWidth(wtext, "Helvetica-Bold", wsize)
        dsize = _fit_font(dname, "Helvetica-Bold", text_size, 7, inner_w - 2 * badge_r - 8 - wwidth - 10)
        c.setFont("Helvetica-Bold", dsize)
        c.drawString(x + pad + 2 * badge_r + 8, cy - dsize * 0.35, dname)
        c.setFont("Helvetica-Bold", wsize)
        c.setFillColor(BRASS_DARK if walk else MUTED)
        c.drawRightString(x + CARD_W - pad, cy - wsize * 0.35, wtext)
        ty -= step


def build_model_cards(roster: dict) -> bytes:
    day, models = roster["day"], roster["models"]
    buffer = io.BytesIO()
    c = pdfcanvas.Canvas(buffer, pagesize=letter)
    c.setTitle(f"FashioNXT {day['name']} - Model Cards")
    c.setAuthor("FashioNXT")

    if not models:
        c.setFont("Times-Bold", 20)
        c.setFillColor(INK)
        c.drawCentredString(letter[0] / 2, letter[1] / 2 + 10, f"{day['name']}: no models assigned yet")
        c.setFont("Helvetica", 11)
        c.setFillColor(MUTED)
        c.drawCentredString(letter[0] / 2, letter[1] / 2 - 10, "Assign models to designers first, then print the cards.")
        c.showPage()
        c.save()
        return buffer.getvalue()

    per_page = COLS * ROWS
    for start in range(0, len(models), per_page):
        chunk = models[start:start + per_page]
        # dashed cut lines for the full grid
        c.saveState()
        c.setStrokeColor(colors.HexColor("#9a9a9a"))
        c.setLineWidth(0.6)
        c.setDash(4, 3)
        for i in range(COLS + 1):
            xx = GRID_X + i * CARD_W
            c.line(xx, GRID_TOP, xx, GRID_TOP - ROWS * CARD_H)
        for j in range(ROWS + 1):
            yy = GRID_TOP - j * CARD_H
            c.line(GRID_X, yy, GRID_X + COLS * CARD_W, yy)
        c.restoreState()

        for idx, m in enumerate(chunk):
            col, row = idx % COLS, idx // COLS
            x = GRID_X + col * CARD_W
            y = GRID_TOP - (row + 1) * CARD_H
            _draw_card(c, x, y, m, day)
        c.showPage()
    c.save()
    return buffer.getvalue()


@router.get("/model-cards")
async def model_cards(show_day_id: int, db: AsyncSession = Depends(get_db)):
    roster = await load_day_roster(db, show_day_id)
    pdf = build_model_cards(roster)
    return _pdf_response(pdf, f"FashioNXT_{roster['day']['name']}_ModelCards.pdf")