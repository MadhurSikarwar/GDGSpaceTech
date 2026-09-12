from datetime import datetime, timezone

from fastapi.testclient import TestClient

from services.propagation.app.main import app
from services.propagation.app.database.repository import DatabaseRepository, SessionLocal


def test_websocket_ping_pong_keepalive():
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/ws/telemetry") as ws:
            ws.send_text("ping")
            assert ws.receive_text() == "pong"


def test_websocket_receives_object_updated_broadcast_from_a_real_repository_write():
    # This is the exact concurrency path the feature depends on: routes.py's
    # handlers are plain `def` and run in FastAPI's worker thread pool, off
    # the event loop the WebSocket connection actually lives on -- so the
    # repository's write here (made directly, same as any route handler
    # would) has to hand the broadcast back to that other thread's loop via
    # ConnectionManager.broadcast_threadsafe(). Using TestClient(app) as a
    # context manager runs the app's real lifespan, so ws_manager really is
    # bound to the TestClient's actual event loop for this test, not a stub.
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/ws/telemetry") as ws:
            db = SessionLocal()
            try:
                repo = DatabaseRepository(db)
                repo.save_object(
                    catalog_id="TEST-WS-BROADCAST",
                    name="WS Broadcast Test Object",
                    object_type="DEBRIS",
                    epoch=datetime.now(timezone.utc),
                    source="unit-test",
                    tle_line_1="1 00000U 00000A   26255.00000000  .00000000  00000+0  00000+0 0  9990",
                    tle_line_2="2 00000  51.6000   0.0000 0001000   0.0000   0.0000 15.50000000000000",
                )
            finally:
                db.close()

            message = ws.receive_json()
            assert message["type"] == "object_updated"
            assert message["catalog_id"] == "TEST-WS-BROADCAST"


def test_websocket_disconnect_is_handled_cleanly():
    from services.propagation.app.realtime.connection_manager import manager as ws_manager

    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/ws/telemetry"):
            assert len(ws_manager._connections) >= 1
        # Context exit closes the socket; the server-side handler's
        # WebSocketDisconnect branch should have removed it from the registry.
        assert len(ws_manager._connections) == 0
