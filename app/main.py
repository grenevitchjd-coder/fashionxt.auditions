from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import engine
from app.migrations import run_migrations
from app.routers import ingest, applicants, events, designers, printing, dayof


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Apply any pending database migrations before serving traffic.
    await run_migrations(engine)
    yield


app = FastAPI(title="FashioNXT Casting API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten to your frontend domain once deployed
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ingest.router)
app.include_router(applicants.router)
app.include_router(events.router)
app.include_router(designers.router)
app.include_router(printing.router)
app.include_router(dayof.router)


@app.get("/health")
async def health():
    return {"status": "ok"}