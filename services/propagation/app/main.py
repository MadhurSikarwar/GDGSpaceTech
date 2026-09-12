import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.propagation.app.config import settings
from services.propagation.app.database.repository import init_db
from services.propagation.app.api.routes import router as api_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing OrbitalGuard Tracking & Screening Database...")
    init_db()
    yield
    logger.info("Shutting down Tracking & Screening service.")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    description="Orbital intelligence foundation for satellite tracking, SGP4 propagation, 2-stage screening, and conjunction candidates.",
    lifespan=lifespan
)

# CORS middleware for multi-agent and frontend inter-service access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include versioned API routes (/api/v1)
app.include_router(api_router)


@app.get("/", include_in_schema=False)
def root_redirect():
    return {
        "service": settings.APP_NAME,
        "version": settings.VERSION,
        "docs_url": "/docs",
        "api_v1_health": f"{settings.API_PREFIX}/health"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("services.propagation.app.main:app", host="0.0.0.0", port=8000, reload=True)
