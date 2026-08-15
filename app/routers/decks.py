import secrets
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Deck, DeckModel, DeckLink, Applicant, Photo

router = APIRouter(prefix="/decks", tags=["decks"])


@router.get("")
async def list_decks(event_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Deck).where(Deck.event_id == event_id).options(selectinload(Deck.link), selectinload(Deck.models))
    )
    decks = result.scalars().all()
    return [
        {
            "id": d.id,
            "designer_name": d.designer_name,
            "model_count": len(d.models),
            "share_url": f"/deck/{d.link.access_token}" if d.link else None,
        }
        for d in decks
    ]


@router.post("")
async def create_deck(event_id: int, designer_name: str, db: AsyncSession = Depends(get_db)):
    deck = Deck(event_id=event_id, designer_name=designer_name)
    db.add(deck)
    await db.flush()

    link = DeckLink(deck_id=deck.id, access_token=secrets.token_urlsafe(24))
    db.add(link)
    await db.commit()
    await db.refresh(deck)
    await db.refresh(link)
    return {"deck_id": deck.id, "designer_name": deck.designer_name, "share_url": f"/deck/{link.access_token}"}


@router.get("/{deck_id}")
async def get_deck(deck_id: int, db: AsyncSession = Depends(get_db)):
    """Staff-facing deck view — list current models for editing."""
    result = await db.execute(
        select(Deck).where(Deck.id == deck_id).options(selectinload(Deck.link))
    )
    deck = result.scalar_one_or_none()
    if not deck:
        raise HTTPException(status_code=404, detail="Deck not found")

    result = await db.execute(
        select(DeckModel).where(DeckModel.deck_id == deck_id).options(selectinload(DeckModel.applicant))
    )
    deck_models = result.scalars().all()

    return {
        "id": deck.id,
        "designer_name": deck.designer_name,
        "share_url": f"/deck/{deck.link.access_token}" if deck.link else None,
        "models": [
            {
                "applicant_id": dm.applicant_id,
                "name": dm.applicant.full_name,
                "audition_number": dm.applicant.audition_number,
                "designer_response": dm.designer_response,
            }
            for dm in deck_models
        ],
    }


@router.put("/{deck_id}/models/{applicant_id}")
async def add_model_to_deck(deck_id: int, applicant_id: int, db: AsyncSession = Depends(get_db)):
    """
    Add a model to a designer's deck at any point — doesn't require them to
    have passed through pool selection first. Flexible, not gated.
    """
    existing = await db.execute(
        select(DeckModel).where(DeckModel.deck_id == deck_id, DeckModel.applicant_id == applicant_id)
    )
    if existing.scalar_one_or_none():
        return {"status": "already in deck"}

    db.add(DeckModel(deck_id=deck_id, applicant_id=applicant_id))
    await db.commit()
    return {"status": "added"}


@router.delete("/{deck_id}/models/{applicant_id}")
async def remove_model_from_deck(deck_id: int, applicant_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(DeckModel).where(DeckModel.deck_id == deck_id, DeckModel.applicant_id == applicant_id)
    )
    deck_model = result.scalar_one_or_none()
    if deck_model:
        await db.delete(deck_model)
        await db.commit()
    return {"status": "removed"}


@router.get("/view/{access_token}")
async def view_deck_by_token(access_token: str, db: AsyncSession = Depends(get_db)):
    """
    Public, no-login view a designer opens via their shared link.
    Returns each model with photos + measurements so they can mark 1 or 2.
    """
    result = await db.execute(select(DeckLink).where(DeckLink.access_token == access_token))
    link = result.scalar_one_or_none()
    if not link:
        raise HTTPException(status_code=404, detail="Invalid or expired link")

    result = await db.execute(
        select(DeckModel)
        .where(DeckModel.deck_id == link.deck_id)
        .options(
            selectinload(DeckModel.applicant).selectinload(Applicant.photos),
            selectinload(DeckModel.applicant).selectinload(Applicant.measurement),
        )
    )
    deck_models = result.scalars().all()

    return {
        "deck_id": link.deck_id,
        "models": [
            {
                "applicant_id": dm.applicant_id,
                "name": dm.applicant.full_name,
                "designer_response": dm.designer_response,
                "photos": [p.url for p in dm.applicant.photos],
                "measurements": (
                    {
                        "height": dm.applicant.measurement.height,
                        "bust_chest": dm.applicant.measurement.bust_chest,
                        "waist_size": dm.applicant.measurement.waist_size,
                        "hip_size": dm.applicant.measurement.hip_size,
                    }
                    if dm.applicant.measurement else None
                ),
            }
            for dm in deck_models
        ],
    }


@router.put("/view/{access_token}/models/{applicant_id}/response")
async def set_designer_response(
    access_token: str, applicant_id: int, designer_response: str | None, db: AsyncSession = Depends(get_db)
):
    """Designer taps 1 or 2 on a model from their share link. No auth beyond the token."""
    result = await db.execute(select(DeckLink).where(DeckLink.access_token == access_token))
    link = result.scalar_one_or_none()
    if not link:
        raise HTTPException(status_code=404, detail="Invalid or expired link")

    result = await db.execute(
        select(DeckModel).where(DeckModel.deck_id == link.deck_id, DeckModel.applicant_id == applicant_id)
    )
    deck_model = result.scalar_one_or_none()
    if not deck_model:
        raise HTTPException(status_code=404, detail="Model not in this deck")

    deck_model.designer_response = designer_response
    await db.commit()
    return {"status": "updated", "designer_response": designer_response}