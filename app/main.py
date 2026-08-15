from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import ingest, applicants, decks, events

app = FastAPI(title="FashioNXT Casting API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten to your frontend domain once deployed
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ingest.router)
app.include_router(applicants.router)
app.include_router(decks.router)
app.include_router(events.router)


@app.get("/health")
async def health():
    return {"status": "ok"}