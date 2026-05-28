from fastapi import FastAPI

from app.api.debate import router as debate_router
from app.api.market_data import router as market_data_router
from app.api.watchlist import router as watchlist_router
from app.config import get_settings

settings = get_settings()

app = FastAPI(title=settings.app_name)
app.include_router(watchlist_router)
app.include_router(debate_router)
app.include_router(market_data_router)


@app.get("/health", tags=["health"])
async def health_check() -> dict[str, str]:
    return {"status": "ok"}
