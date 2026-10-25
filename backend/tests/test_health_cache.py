import hashlib
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from app import api
from app.main import app
from app.releases import read_current_release
from backend.tests.release_fixtures import write_runtime_pointer
from backend.tests.test_tiles import _source_database, _write_mbtiles


def test_health_summary_is_single_flight_and_invalidates_with_artifact(tmp_path, monkeypatch):
    archive = tmp_path / "tiles.mbtiles"
    _write_mbtiles(archive)
    pointer = write_runtime_pointer(tmp_path / "data", archive)
    record = json.loads(pointer.read_text())
    database = pointer.parent / record["release_path"] / "app.sqlite3"
    database.write_bytes(b"")
    _source_database(database)
    record.update(block_faces=1, schedule_counts={"REFUSE": 2})
    record["artifacts"]["database"]["sha256"] = hashlib.sha256(database.read_bytes()).hexdigest()
    pointer.write_text(json.dumps(record))
    monkeypatch.setattr(api, "DATA_MANIFEST_PATH", str(pointer))
    api._cached_health_summary.cache_clear()
    api._require_verified_current_release(read_current_release(pointer))

    aggregates = []
    original = api._read_only_database
    def trace(statement):
        if "COUNT(*)" in statement.upper():
            aggregates.append(statement)
            time.sleep(0.02)

    def counted(path):
        connection = original(path)
        connection.set_trace_callback(trace)
        return connection
    monkeypatch.setattr(api, "_read_only_database", counted)
    barrier = threading.Barrier(4)
    def probe(_):
        barrier.wait(timeout=5)
        with TestClient(app) as client:
            return client.get("/api/health")
    with ThreadPoolExecutor(max_workers=4) as workers:
        responses = list(workers.map(probe, range(4)))
    assert all(response.status_code == 200 for response in responses)
    assert len(aggregates) == 3

    with TestClient(app) as client:
        record["verified_at"] = "2026-10-24T12:00:00+00:00"
        pointer.write_text(json.dumps(record))
        assert client.get("/api/health").json()["verified_at"] == record["verified_at"]
        assert len(aggregates) == 3

        def unavailable(path):
            raise sqlite3.OperationalError("unable to open database file")

        with monkeypatch.context() as patch:
            patch.setattr(api, "_read_only_database", unavailable)
            response = client.get("/api/health")
            assert response.status_code == 503
            assert response.json()["detail"] == "Committed database is invalid"
        assert client.get("/api/health").status_code == 200
        assert len(aggregates) == 3

        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE block_face_collection_states SET source_policy_conflict = 1")
        # Cached summaries must never bypass the existing artifact integrity gate.
        assert client.get("/api/health").status_code == 503
        assert len(aggregates) == 3
        record["artifacts"]["database"]["sha256"] = hashlib.sha256(database.read_bytes()).hexdigest()
        pointer.write_text(json.dumps(record))
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json()["policy_conflicts"] > 0
        assert len(aggregates) == 6
