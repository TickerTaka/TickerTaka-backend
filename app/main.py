from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.debate import router as debate_router
from app.api.market_data import router as market_data_router
from app.api.watchlist import router as watchlist_router
from app.config import get_settings

settings = get_settings()

app = FastAPI(title=settings.app_name)

# Next.js dev server runs on :3000. Loosen this for dev convenience.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(watchlist_router)
app.include_router(debate_router)
app.include_router(market_data_router)


@app.get("/health", tags=["health"])
async def health_check() -> dict[str, str]:
    return {"status": "ok"}
