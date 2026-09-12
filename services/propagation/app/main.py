import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from services.propagation.app.config import settings
from services.propagation.app.database.repository import init_db
from services.propagation.app.api.routes import router as api_router
from services.propagation.app.realtime.connection_manager import manager as ws_manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing OrbitalGuard Tracking & Screening Database...")
    init_db()
    # Binding the running event loop here (rather than at import time, when
    # there isn't one yet) is what lets repository.py's synchronous,
    # worker-thread write paths hand a broadcast back to this loop safely --
    # see connection_manager.py's module docstring for the full reasoning.
    ws_manager.bind_event_loop(asyncio.get_running_loop())
    yield
    logger.info("Shutting down Tracking & Screening service.")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    description="Orbital intelligence foundation for satellite tracking, SGP4 propagation, 2-stage screening, and conjunction candidates.",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include versioned API routes (/api/v1/...)
app.include_router(api_router)

# Create unversioned alias router for backward-compatibility with direct /objects, /conjunctions, /health calls
from fastapi import APIRouter
unversioned_router = APIRouter(tags=["Unversioned Aliases"])

# Import route handlers from api_router
for route in api_router.routes:
    # Strip prefix for unversioned alias
    path = route.path.replace(settings.API_PREFIX, "")
    if path and path != "/health":
        unversioned_router.add_api_route(
            path,
            route.endpoint,
            methods=route.methods,
            response_model=route.response_model,
            summary=f"{route.summary} (Alias)",
            include_in_schema=False
        )

@unversioned_router.get("/health", include_in_schema=False)
def unversioned_health():
    from services.propagation.app.api.routes import get_health
    from services.propagation.app.database.repository import get_db
    db = next(get_db())
    return get_health(db=db)

app.include_router(unversioned_router)


@app.get("/", include_in_schema=False)
def root_redirect():
    return {
        "service": settings.APP_NAME,
        "version": settings.VERSION,
        "docs_url": "/docs",
        "api_v1_health": f"{settings.API_PREFIX}/health"
    }


# Registered directly on `app` (not on api_router) so it never passes through
# the unversioned-alias loop above: that loop calls add_api_route() with
# route.methods / route.response_model, attributes an HTTP APIRoute has but a
# WebSocketRoute does not -- looping over a router containing this route
# would raise at import time.
@app.websocket(f"{settings.API_PREFIX}/ws/telemetry")
async def telemetry_websocket(websocket: WebSocket):
    """
    Discrete catalog-change telemetry: pushes {type: "object_updated"} and
    {type: "conjunction_flagged"} events as they happen (ingest, synthetic
    injection, screening runs). Deliberately NOT a continuous position
    stream -- see connection_manager.py's module docstring for why that
    would be a regression, not an upgrade, at this catalog's scale.

    The client sends nothing but periodic "ping" keepalives; `receive_text()`
    doubles as this endpoint's only way to detect the socket has gone away
    (a WebSocketDisconnect is raised the moment the client closes), so it is
    intentionally the only thing this loop blocks on.
    """
    await ws_manager.connect(websocket)
    try:
        while True:
            message = await websocket.receive_text()
            if message == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.disconnect(websocket)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("services.propagation.app.main:app", host="0.0.0.0", port=8000, reload=True)
