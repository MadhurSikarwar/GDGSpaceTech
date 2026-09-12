from fastapi.testclient import TestClient
from services.propagation.app.database.repository import init_db
from services.propagation.app.main import app

init_db()
client = TestClient(app)


def test_health_endpoint():
    res = client.get("/api/v1/health")
    assert res.status_code == 200
    json_data = res.json()
    assert json_data["status"] == "HEALTHY"


def test_ingest_endpoint():
    res = client.post("/api/v1/ingest?group=active")
    assert res.status_code == 200
    json_data = res.json()
    assert "25544" in json_data["catalog_ids"] or len(json_data["catalog_ids"]) > 0


def test_get_objects():
    res = client.get("/api/v1/objects")
    assert res.status_code == 200
    objs = res.json()
    assert isinstance(objs, list)
    assert len(objs) > 0


def test_get_object_detail():
    res = client.get("/api/v1/objects/25544")
    assert res.status_code == 200
    obj = res.json()
    assert obj["catalog_id"] == "25544"
    assert obj["name"] == "ISS (ZARYA)"
    assert "position_km" in obj["state"]


def test_get_trajectory():
    res = client.get("/api/v1/objects/25544/trajectory?horizon=90&step=1")
    assert res.status_code == 200
    traj = res.json()
    assert traj["catalog_id"] == "25544"
    assert len(traj["trajectory"]) == 91


def test_inject_synthetic_demo_and_conjunctions():
    res_inject = client.post("/api/v1/demo/inject-synthetic?target_catalog_id=25544")
    assert res_inject.status_code == 200
    data = res_inject.json()
    assert data["synthetic_object_id"] == "SYNTHETIC-99999"

    res_conj = client.get("/api/v1/conjunctions")
    assert res_conj.status_code == 200
    conjs = res_conj.json()
    assert isinstance(conjs, list)
