"""
Print Headshots: pick models from the Final Roster list, choose how many copies of each,
and download one PDF of same-size (4:5) headshots with large names.

  GET /day-of/headshots/models                  - every Final Roster model (for the search box)
  GET /day-of/print/headshots?items=12:2,15:1   - the PDF  (model id : copies, in print order)
"""
import asyncio
import io
import urllib.request

from fastapi import APIRouter, Depends, HTTPException, Query
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as pdfcanvas
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Applicant, Designer, DesignerAssignment
from app.routers.dayof import _pick_photo
from app.routers.printing import BRASS_DARK, INK, MUTED, _category_label
from app.routers.printing_dayof import _pdf_response

router = APIRouter(prefix="/day-of", tags=["day-of-headshots"])

MAX_PEOPLE = 300
MAX_COPIES = 20


# ---------------------------------------------------------------------------
# Model list for the search box
# ---------------------------------------------------------------------------
@router.get("/headshots/models")
async def headshot_models(db: AsyncSession = Depends(get_db)):
    """Every model on the Final Roster (anyone with a pool assignment), alphabetical,
    with the show days they are assigned to a designer on (for the 'add everyone for a day' buttons)."""
    applicants = (
        await db.execute(
            select(Applicant)
            .where(Applicant.pool_assignment.has())
            .options(selectinload(Applicant.photos))
            .order_by(Applicant.full_name)
        )
    ).scalars().all()
    ids = [a.id for a in applicants]

    days_by_model: dict[int, set[int]] = {}
    if ids:
        rows = await db.execute(
            select(DesignerAssignment.applicant_id, Designer.show_day_id)
            .join(Designer, Designer.id == DesignerAssignment.designer_id)
            .where(DesignerAssignment.applicant_id.in_(ids))
        )
        for applicant_id, day_id in rows.all():
            days_by_model.setdefault(applicant_id, set()).add(day_id)

    return [
        {
            "id": a.id,
            "full_name": a.full_name,
            "category": _category_label(a.category),
            "photo_url": _pick_photo(a.photos),
            "day_ids": sorted(days_by_model.get(a.id, [])),
        }
        for a in applicants
    ]


# ---------------------------------------------------------------------------
# Headshot images — 4:5 portrait crop, biased toward the top (faces)
# ---------------------------------------------------------------------------
THUMB_W, THUMB_H = 420, 525


def _download_portrait(url: str) -> bytes | None:
    try:
        from PIL import Image, ImageOps
        req = urllib.request.Request(url, headers={"User-Agent": "FashioNXT-print"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read(20_000_000)
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
        w, h = img.size
        target = 4 / 5
        if w / h > target:            # too wide: trim the sides evenly
            new_w = int(h * target)
            left = (w - new_w) // 2
            img = img.crop((left, 0, left + new_w, h))
        else:                          # too tall: trim mostly from the bottom, keep the head
            new_h = int(w / target)
            top = int((h - new_h) * 0.15)
            img = img.crop((0, top, w, top + new_h))
        img = img.resize((THUMB_W, THUMB_H))
        out = io.BytesIO()
        img.save(out, "JPEG", quality=85)
        return out.getvalue()
    except Exception:
        return None


async def _fetch_portraits(urls: set[str]) -> dict[str, bytes | None]:
    sem = asyncio.Semaphore(8)

    async def one(u):
        async with sem:
            return u, await asyncio.to_thread(_download_portrait, u)

    return dict(await asyncio.gather(*(one(u) for u in urls)))


# ---------------------------------------------------------------------------
# PDF — 2 across x 3 down, dashed cut lines
# ---------------------------------------------------------------------------
COLS, ROWS = 2, 3
CELL_W, CELL_H = 270.0, 240.0
GRID_X = (letter[0] - COLS * CELL_W) / 2
GRID_TOP = letter[1] - (letter[1] - ROWS * CELL_H) / 2
PHOTO_H = 150.0
PHOTO_W = PHOTO_H * 4 / 5


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


def _draw_cell(c, x, y, person: dict, photo: bytes | None):
    """(x, y) = lower-left corner of the cell."""
    cx = x + CELL_W / 2
    px = cx - PHOTO_W / 2
    py = y + CELL_H - 8 - PHOTO_H
    if photo:
        c.drawImage(ImageReader(io.BytesIO(photo)), px, py, PHOTO_W, PHOTO_H)
    else:
        c.setFillColor(colors.HexColor("#e6e3dc"))
        c.rect(px, py, PHOTO_W, PHOTO_H, stroke=0, fill=1)
        c.setFillColor(MUTED)
        c.setFont("Helvetica-Bold", 40)
        ini = "".join(w[0].upper() for w in person["full_name"].split()[:2]) or "?"
        c.drawCentredString(cx, py + PHOTO_H / 2 - 14, ini)
    c.setStrokeColor(colors.HexColor("#d8cfbd"))
    c.setLineWidth(0.8)
    c.rect(px, py, PHOTO_W, PHOTO_H, stroke=1, fill=0)

    # large name (shrinks to fit; wraps to two lines at most)
    name = person["full_name"]
    font = "Helvetica-Bold"
    size = 22.0
    lines = _wrap(name, font, size, CELL_W - 20)
    while (len(lines) > 2 or any(stringWidth(l, font, size) > CELL_W - 20 for l in lines)) and size > 12:
        size -= 1
        lines = _wrap(name, font, size, CELL_W - 20)
    lines = lines[:2]
    if len(lines) == 2 and size > 19:
        size = 19
    c.setFillColor(INK)
    c.setFont(font, size)
    ty = py - 6 - size
    for ln in lines:
        c.drawCentredString(cx, ty, ln)
        ty -= size + 1
    c.setFillColor(BRASS_DARK)
    c.setFont("Helvetica", 12)
    c.drawCentredString(cx, ty - 1, f"({person['category']})")


def build_headshot_pdf(people: list[dict], thumbs: dict) -> bytes:
    buffer = io.BytesIO()
    c = pdfcanvas.Canvas(buffer, pagesize=letter)
    c.setTitle("FashioNXT Headshots")
    c.setAuthor("FashioNXT")

    # one cell per copy, a person's copies kept together
    cells = [p for p in people for _ in range(p["copies"])]
    per_page = COLS * ROWS
    for start in range(0, len(cells), per_page):
        chunk = cells[start:start + per_page]
        c.saveState()
        c.setStrokeColor(colors.HexColor("#9a9a9a"))
        c.setLineWidth(0.6)
        c.setDash(4, 3)
        for i in range(COLS + 1):
            xx = GRID_X + i * CELL_W
            c.line(xx, GRID_TOP, xx, GRID_TOP - ROWS * CELL_H)
        for j in range(ROWS + 1):
            yy = GRID_TOP - j * CELL_H
            c.line(GRID_X, yy, GRID_X + COLS * CELL_W, yy)
        c.restoreState()
        for idx, person in enumerate(chunk):
            col, row = idx % COLS, idx // COLS
            _draw_cell(c, GRID_X + col * CELL_W, GRID_TOP - (row + 1) * CELL_H, person, thumbs.get(person["photo_url"]))
        c.showPage()
    c.save()
    return buffer.getvalue()


def _parse_items(items: str) -> list[tuple[int, int]]:
    out = []
    for part in items.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            id_s, _, copies_s = part.partition(":")
            out.append((int(id_s), max(1, min(MAX_COPIES, int(copies_s or 1)))))
        except ValueError:
            raise HTTPException(status_code=400, detail="Bad items list")
    return out


@router.get("/print/headshots")
async def headshots_pdf(
    items: str = Query(..., description="model id:copies, comma separated, in print order"),
    db: AsyncSession = Depends(get_db),
):
    parsed = _parse_items(items)
    if not parsed:
        raise HTTPException(status_code=400, detail="Pick at least one model")
    if len(parsed) > MAX_PEOPLE:
        raise HTTPException(status_code=400, detail=f"Too many people (max {MAX_PEOPLE})")

    ids = [i for i, _ in parsed]
    rows = (
        await db.execute(select(Applicant).where(Applicant.id.in_(ids)).options(selectinload(Applicant.photos)))
    ).scalars().all()
    by_id = {a.id: a for a in rows}

    people = []
    for aid, copies in parsed:
        a = by_id.get(aid)
        if a is None:
            continue
        people.append({
            "full_name": a.full_name,
            "category": _category_label(a.category),
            "photo_url": _pick_photo(a.photos),
            "copies": copies,
        })
    if not people:
        raise HTTPException(status_code=404, detail="None of those models were found")

    urls = {p["photo_url"] for p in people if p["photo_url"]}
    thumbs = await _fetch_portraits(urls) if urls else {}
    pdf = build_headshot_pdf(people, thumbs)
    return _pdf_response(pdf, "FashioNXT_Headshots.pdf")