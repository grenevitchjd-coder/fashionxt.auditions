"""
Hair / Make Up team PDF: one designer per page (a long lineup continues onto the next page),
models in the team's current order with headshot, late note, "also walking for" and
tick boxes. GET /day-of/print/team-sheet?team=hair|makeup&show_day_id=
"""
import asyncio
import io
import urllib.request
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.platypus import CondPageBreak, Flowable, HRFlowable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.routers.printing import (
    BRASS, BRASS_DARK, CARD_BORDER, EMPTY, HEAD_BG, INK, MARGIN, MUTED, NOTE_BG, PAGE_W, ROW_LINE, ZEBRA,
    DESIGNER_NAME, NumberBadge, NumberedCanvas, PageHeader, _printed_stamp,
)
from app.routers.printing_dayof import CheckBox, _date_title, _pdf_response
from app.routers.teams import load_team_board

router = APIRouter(prefix="/day-of/print", tags=["day-of-print"])

PHOTO = 0.5 * inch
NAME_STYLE = ParagraphStyle("tn", fontName="Helvetica-Bold", fontSize=13, leading=16, textColor=INK)
ALSO_STYLE = ParagraphStyle("ta", fontName="Helvetica-Bold", fontSize=9, leading=11.5, textColor=BRASS_DARK)
NOTE_STYLE = ParagraphStyle("tnote", fontName="Helvetica-Oblique", fontSize=9.5, leading=12, textColor=colors.HexColor("#8a5a00"))
POS_STYLE = ParagraphStyle("tp", fontName="Helvetica-Bold", fontSize=13, leading=16, textColor=BRASS_DARK, alignment=2)
COUNT_STYLE = ParagraphStyle("tc", fontName="Helvetica", fontSize=20, leading=24, alignment=2)


# ---- headshots ------------------------------------------------------------
def _download_thumb(url: str) -> bytes | None:
    """Fetch a photo and shrink it to a small square JPEG (top-biased crop, good for faces)."""
    try:
        from PIL import Image, ImageOps
        req = urllib.request.Request(url, headers={"User-Agent": "FashioNXT-print"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read(15_000_000)
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
        w, h = img.size
        side = min(w, h)
        left = (w - side) // 2
        top = 0 if h > w else (h - side) // 2
        img = img.crop((left, top, left + side, top + side)).resize((220, 220))
        out = io.BytesIO()
        img.save(out, "JPEG", quality=80)
        return out.getvalue()
    except Exception:
        return None


async def fetch_thumbs(urls: set[str]) -> dict[str, bytes | None]:
    sem = asyncio.Semaphore(8)

    async def one(u):
        async with sem:
            return u, await asyncio.to_thread(_download_thumb, u)

    return dict(await asyncio.gather(*(one(u) for u in urls)))


class Headshot(Flowable):
    """Square photo, or an initials tile when there is no photo."""

    def __init__(self, data: bytes | None, name: str, size=PHOTO):
        super().__init__()
        self.data, self.name, self.size = data, name, size
        self.width = self.height = size

    def draw(self):
        c = self.canv
        s = self.size
        if self.data:
            c.drawImage(ImageReader(io.BytesIO(self.data)), 0, 0, s, s)
        else:
            c.setFillColor(colors.HexColor("#e6e3dc"))
            c.rect(0, 0, s, s, stroke=0, fill=1)
            c.setFillColor(MUTED)
            c.setFont("Helvetica-Bold", s * 0.34)
            ini = "".join(w[0].upper() for w in self.name.split()[:2]) or "?"
            c.drawCentredString(s / 2, s / 2 - s * 0.12, ini)
        c.setStrokeColor(CARD_BORDER)
        c.setLineWidth(0.8)
        c.rect(0, 0, s, s, stroke=1, fill=0)


class DoneBoxes(Flowable):
    """One box ('DONE'), or two ('THIS LOOK' / 'ALL LOOKS') for models with several designers."""

    def __init__(self, multi: bool, done: bool, all_done: bool):
        super().__init__()
        self.multi, self.done, self.all_done = multi, done, all_done
        self.width, self.height = (1.35 * inch if multi else 0.6 * inch), 0.46 * inch

    def _box(self, x, checked, label):
        c = self.canv
        size = 15
        y = 0.17 * inch
        c.setStrokeColor(INK)
        c.setLineWidth(1.3)
        c.rect(x, y, size, size, stroke=1, fill=0)
        if checked:
            c.setStrokeColor(BRASS_DARK)
            c.setLineWidth(2.2)
            c.lines([(x + 3, y + 8, x + 6.5, y + 3.5), (x + 6.5, y + 3.5, x + 12.5, y + 12.5)])
        c.setFillColor(MUTED)
        c.setFont("Helvetica-Bold", 6.5)
        c.drawCentredString(x + size / 2, y - 9, label)

    def draw(self):
        if self.multi:
            self._box(4, self.done, "THIS LOOK")
            self._box(0.72 * inch + 4, self.all_done, "ALL LOOKS")
        else:
            self._box(10, self.done, "DONE")


# ---- designer pages ---------------------------------------------------------
def _designer_card(d: dict, thumbs: dict, ) -> list:
    W = PAGE_W
    n = len(d["models"])
    count_html = (
        f"<font name='Helvetica-Bold' size='20' color='#7d5c2c'>{n}</font>"
        f"<font name='Helvetica-Bold' size='9' color='#5f6368'>&nbsp;MODEL{'' if n == 1 else 'S'}</font>"
    )
    head = Table(
        [[NumberBadge(d["order_in_day"]), Paragraph(escape(d["name"]), DESIGNER_NAME), Paragraph(count_html, COUNT_STYLE)]],
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

    cols = [0.45 * inch, PHOTO + 14, W - 0.45 * inch - PHOTO - 14 - 1.45 * inch, 1.45 * inch]
    rows = [[head, "", "", ""]]
    if not d["models"]:
        rows.append([Paragraph("No models assigned yet.", EMPTY), "", "", ""])
    for pos, m in enumerate(d["models"], start=1):
        info = [Paragraph(escape(m["full_name"]), NAME_STYLE)]
        if m["other_designers"]:
            also = " &nbsp;&middot;&nbsp; ".join(f"{x['order_in_day']}. {escape(x['name'])}" for x in m["other_designers"])
            info.append(Paragraph(f"ALSO WALKING FOR: {also}", ALSO_STYLE))
        if m["note"]:
            info.append(Paragraph(f"Note: {escape(m['note'])}", NOTE_STYLE))
        rows.append([
            Paragraph(str(pos), POS_STYLE),
            Headshot(thumbs.get(m["photo_url"]) if m["photo_url"] else None, m["full_name"]),
            info,
            DoneBoxes(m["looks_total"] > 1, m["done"], m["all_done"]),
        ])

    last = len(rows) - 1
    style = [
        ("BOX", (0, 0), (-1, -1), 0.9, CARD_BORDER),
        ("SPAN", (0, 0), (-1, 0)),
        ("VALIGN", (0, 1), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, 0), 0), ("RIGHTPADDING", (0, 0), (-1, 0), 0),
        ("TOPPADDING", (0, 0), (-1, 0), 0), ("BOTTOMPADDING", (0, 0), (-1, 0), 0),
        ("TOPPADDING", (0, 1), (-1, -1), 3), ("BOTTOMPADDING", (0, 1), (-1, -1), 3),
        ("LEFTPADDING", (0, 1), (0, -1), 4), ("RIGHTPADDING", (0, 1), (0, -1), 4),
        ("LEFTPADDING", (1, 1), (1, -1), 8), ("RIGHTPADDING", (1, 1), (1, -1), 6),
        ("LEFTPADDING", (2, 1), (2, -1), 8),
    ]
    if not d["models"]:
        style += [("SPAN", (0, 1), (-1, 1)), ("LEFTPADDING", (0, 1), (-1, 1), 14), ("TOPPADDING", (0, 1), (-1, 1), 10), ("BOTTOMPADDING", (0, 1), (-1, 1), 10)]
    else:
        for r in range(1, last):
            style.append(("LINEBELOW", (0, r), (-1, r), 0.5, ROW_LINE))
        for r in range(2, last + 1, 2):
            style.append(("BACKGROUND", (0, r), (-1, r), ZEBRA))
    card = Table(rows, colWidths=cols, repeatRows=1)
    card.setStyle(TableStyle(style))
    return [card]


def build_team_pdf(board: dict, thumbs: dict) -> bytes:
    day = board["day"]
    label = board["team_label"]
    tag = label.upper()
    stamp = _printed_stamp()
    buffer = io.BytesIO()

    class _Canvas(NumberedCanvas):
        footer_left = f"FashioNXT Week  |  {day['name']}  |  {label}  |  Printed {stamp}"

    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        leftMargin=MARGIN, rightMargin=MARGIN, topMargin=0.55 * inch, bottomMargin=0.8 * inch,
        title=f"FashioNXT {day['name']} - {label}", author="FashioNXT",
    )

    story = []
    designers = board["designers"]
    if not designers:
        story += [PageHeader(day["name"], _date_title(day), tag),
                  HRFlowable(width="100%", thickness=2.2, color=BRASS, spaceBefore=2, spaceAfter=10),
                  Paragraph("No designers have been added to this day yet.", EMPTY)]
    for i, d in enumerate(designers):
        if i:
            story.append(PageBreak())
        story += [PageHeader(day["name"], _date_title(day), tag),
                  HRFlowable(width="100%", thickness=2.2, color=BRASS, spaceBefore=2, spaceAfter=10)]
        story += _designer_card(d, thumbs)
    doc.build(story, canvasmaker=_Canvas)
    return buffer.getvalue()


@router.get("/team-sheet")
async def team_sheet(team: str, show_day_id: int, db: AsyncSession = Depends(get_db)):
    board = await load_team_board(db, team, show_day_id)
    urls = {m["photo_url"] for d in board["designers"] for m in d["models"] if m["photo_url"]}
    thumbs = await fetch_thumbs(urls) if urls else {}
    pdf = build_team_pdf(board, thumbs)
    return _pdf_response(pdf, f"FashioNXT_{board['day']['name']}_{board['team_label'].replace(' ', '')}.pdf")