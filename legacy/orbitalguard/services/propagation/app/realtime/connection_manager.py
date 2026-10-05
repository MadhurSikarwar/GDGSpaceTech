"""
In-process WebSocket connection registry + broadcast hub for the tracking
service's discrete catalog-change telemetry (new/updated objects, freshly
flagged conjunctions) -- NOT a continuous position stream. Live per-object
motion is already handled better client-side (trajectory-point interpolation
driven by the scrubbable sim clock, zero network cost) than any server push
could do at ~10.9k tracked objects; streaming position deltas for the whole
catalog would be a regression, not an upgrade. See main.py's websocket route
for the client-facing contract.

Single uvicorn worker (confirmed: run_backend.py and .claude/launch.json
both launch this service as one plain `uvicorn` process, no `--workers N`),
so a plain module-level registry is sufficient here -- no Redis/pub-sub
needed.

Thread-safety note, the reason broadcast_threadsafe() exists at all:
services/propagation/app/api/routes.py's handlers are plain `def`, which
FastAPI/Starlette runs in a worker THREAD POOL, off the asyncio event loop
entirely. DatabaseRepository.save_object/save_conjunction (called from
those handlers) therefore also run off the event loop. Calling any
asyncio/WebSocket API directly from that thread would either raise
("no running event loop") or attach to the wrong loop.
broadcast_threadsafe() is the one safe way to trigger a broadcast from
repository code: it hands the actual async send back to the event loop
thread via `asyncio.run_coroutine_threadsafe`, rather than awaiting or
scheduling anything from the calling thread itself.
"""

import asyncio
import json
import logging
from typing import Any, Dict, Optional, Set

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self):
        self._connections: Set[WebSocket] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def bind_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Call once from main.py's lifespan startup, on the event loop thread itself."""
        self._loop = loop

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.add(websocket)
        logger.info(f"Telemetry WebSocket connected ({len(self._connections)} total).")

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.discard(websocket)
        logger.info(f"Telemetry WebSocket disconnected ({len(self._connections)} total).")

    async def _broadcast(self, message: Dict[str, Any]) -> None:
        if not self._connections:
            return
        payload = json.dumps(message, default=str)
        dead = []
        for ws in list(self._connections):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._connections.discard(ws)

    def broadcast_threadsafe(self, message: Dict[str, Any]) -> None:
        """
        Safe to call from ANY thread, in particular from repository.py's
        synchronous write paths running in the route handlers' worker-pool
        thread. A no-op before the event loop is bound (e.g. module import
        during tests that never run the app's lifespan) or after it has
        already closed (shutdown race) -- there is nothing to deliver to
        either way.
        """
        if self._loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(self._broadcast(message), self._loop)
        except RuntimeError:
            pass


manager = ConnectionManager()
