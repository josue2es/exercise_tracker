"""REST API tests: auth, scopes, isolation, and the endpoint surface.

The API router is mounted on a bare FastAPI app so tests don't drag in
NiceGUI. Service-level behavior is covered by the service tests; here we
prove the transport layer (status codes, error shape, auth).
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import services.api_keys as api_keys
import services.users as users
from api.router import router, service_error_handler
from services.errors import ServiceError
from tests.conftest import make_exercise, make_user


@pytest.fixture
def client(engine):
    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(ServiceError, service_error_handler)
    return TestClient(app)


@pytest.fixture
def user_a(engine):
    return make_user(email="a@example.com", role="member")


@pytest.fixture
def user_b(engine):
    return make_user(email="b@example.com", role="member")


def _make_key(user_id, scopes=("read",), label="test key"):
    from services.context import ui_context

    _, raw = api_keys.create_key(ui_context(user_id), label, list(scopes))
    return raw


def _headers(key):
    return {"Authorization": f"Bearer {key}"}


# --- authentication -------------------------------------------------------------------


def test_missing_key_is_401(client):
    r = client.get("/api/v1/me")
    assert r.status_code == 401
    assert r.json()["error"] == "unauthorized"


def test_bad_key_is_401(client):
    r = client.get("/api/v1/me", headers=_headers("gym_notarealkey"))
    assert r.status_code == 401
    r = client.get("/api/v1/me", headers={"Authorization": "Basic abc"})
    assert r.status_code == 401


def test_me_returns_user_and_scopes(client, user_a):
    key = _make_key(user_a, scopes=("read", "write"))
    r = client.get("/api/v1/me", headers=_headers(key))
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "a@example.com"
    assert body["scopes"] == ["read", "write"]


def test_revoked_key_is_401(client, user_a):
    from services.context import ui_context

    info, raw = api_keys.create_key(ui_context(user_a), "k", ["read"])
    api_keys.revoke_key(ui_context(user_a), info.id)
    assert client.get("/api/v1/me", headers=_headers(raw)).status_code == 401


def test_deactivated_user_key_is_401(client, user_a, user_b):
    # user_b (admin) deactivates user_a
    admin = make_user(email="root@example.com", role="admin")
    from services.context import UserContext

    users.set_user_active(UserContext(user_id=admin, role="admin", actor="ui"), user_a, False)
    key = _make_key(user_a)
    assert client.get("/api/v1/me", headers=_headers(key)).status_code == 401


# --- scopes: a read key gets 403 on every write -------------------------------------------


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("POST", "/api/v1/workouts", {"name": "X"}),
        ("PATCH", "/api/v1/workouts/1", {"name": "X"}),
        ("PUT", "/api/v1/workouts/1/exercises", {"exercises": []}),
        ("POST", "/api/v1/sessions", {"workout_id": 1}),
        ("PATCH", "/api/v1/sessions/1", {"finished": True}),
        ("POST", "/api/v1/sessions/1/sets", {"exercise_id": 1, "reps": 5}),
        ("PATCH", "/api/v1/sets/1", {"reps": 5}),
    ],
)
def test_read_key_cannot_write(client, user_a, method, path, body):
    key = _make_key(user_a, scopes=("read",))
    r = client.request(method, path, headers=_headers(key), json=body)
    assert r.status_code == 403
    assert r.json()["error"] == "forbidden"


def test_write_key_can_write(client, user_a):
    key = _make_key(user_a, scopes=("read", "write"))
    r = client.post("/api/v1/workouts", headers=_headers(key), json={"name": "Via API"})
    assert r.status_code == 201
    assert r.json()["name"] == "Via API"


# --- isolation: user B gets 404 for user A's data (and so does the admin) --------------------


@pytest.fixture
def workout_of_a(client, user_a):
    key = _make_key(user_a, scopes=("read", "write"))
    r = client.post("/api/v1/workouts", headers=_headers(key), json={"name": "A's plan"})
    return r.json()["id"], key


def test_isolation_workouts(client, user_a, user_b, workout_of_a):
    workout_id, _ = workout_of_a
    b_key = _make_key(user_b, scopes=("read", "write"))
    admin_key = _make_key(
        make_user(email="root@example.com", role="admin"), scopes=("read", "write")
    )
    for key in (b_key, admin_key):
        assert client.get(f"/api/v1/workouts/{workout_id}", headers=_headers(key)).status_code == 404
        assert (
            client.patch(
                f"/api/v1/workouts/{workout_id}", headers=_headers(key), json={"name": "stolen"}
            ).status_code
            == 404
        )
    # B sees only their own (empty) list.
    assert client.get("/api/v1/workouts", headers=_headers(b_key)).json()["workouts"] == []


def test_isolation_sessions_and_sets(client, user_a, user_b, workout_of_a):
    workout_id, a_key = workout_of_a
    exercise_id = make_exercise(name="Row")

    # A starts a session and logs a set.
    session_id = client.post(
        "/api/v1/sessions", headers=_headers(a_key), json={"workout_id": workout_id}
    ).json()["id"]
    set_id = client.post(
        f"/api/v1/sessions/{session_id}/sets",
        headers=_headers(a_key),
        json={"exercise_id": exercise_id, "reps": 8, "weight": {"value": 50, "unit": "kg"}},
    ).json()["id"]

    b_key = _make_key(user_b, scopes=("read", "write"))
    assert client.get(f"/api/v1/sessions/{session_id}", headers=_headers(b_key)).status_code == 404
    assert (
        client.post(
            f"/api/v1/sessions/{session_id}/sets",
            headers=_headers(b_key),
            json={"exercise_id": exercise_id, "reps": 1},
        ).status_code
        == 404
    )
    assert client.patch(f"/api/v1/sets/{set_id}", headers=_headers(b_key), json={"reps": 1}).status_code == 404
    assert client.get("/api/v1/sessions/open", headers=_headers(b_key)).json()["session"] is None


# --- endpoint behavior ----------------------------------------------------------------------


def test_exercise_search_pagination(client, user_a):
    key = _make_key(user_a)
    for i in range(5):
        make_exercise(name=f"Press {i}", source_id=f"press{i}")
    r = client.get("/api/v1/exercises", headers=_headers(key), params={"q": "press", "limit": 2})
    body = r.json()
    assert len(body["items"]) == 2
    assert body["more_available"] is True and body["next_cursor"]
    r2 = client.get(
        "/api/v1/exercises",
        headers=_headers(key),
        params={"q": "press", "limit": 2, "cursor": body["next_cursor"]},
    )
    assert r2.json()["items"][0]["id"] > body["items"][-1]["id"]


def test_last_performance_endpoint(client, user_a, workout_of_a):
    workout_id, a_key = workout_of_a
    exercise_id = make_exercise(name="Row")
    client.post("/api/v1/sessions", headers=_headers(a_key), json={"workout_id": workout_id})
    client.post(
        f"/api/v1/sessions/{client.get('/api/v1/sessions/open', headers=_headers(a_key)).json()['session']['id']}/sets",
        headers=_headers(a_key),
        json={"exercise_id": exercise_id, "reps": 8, "weight": {"value": 50, "unit": "kg"}},
    )
    client.patch(
        f"/api/v1/sessions/{client.get('/api/v1/sessions/open', headers=_headers(a_key)).json()['session']['id']}",
        headers=_headers(a_key),
        json={"finished": True},
    )
    r = client.get(f"/api/v1/exercises/{exercise_id}/last", headers=_headers(a_key))
    assert r.status_code == 200
    perf = r.json()["last_performance"]
    assert perf["sets"][0]["weight"] == {"value": 50.0, "unit": "kg"}
    assert "date" in perf


def test_repeated_set_number_returns_existing(client, user_a, workout_of_a):
    workout_id, a_key = workout_of_a
    exercise_id = make_exercise(name="Row")
    session_id = client.post(
        "/api/v1/sessions", headers=_headers(a_key), json={"workout_id": workout_id}
    ).json()["id"]
    first = client.post(
        f"/api/v1/sessions/{session_id}/sets",
        headers=_headers(a_key),
        json={"exercise_id": exercise_id, "reps": 8, "set_number": 1},
    ).json()
    repeat = client.post(
        f"/api/v1/sessions/{session_id}/sets",
        headers=_headers(a_key),
        json={"exercise_id": exercise_id, "reps": 99, "set_number": 1},
    ).json()
    assert repeat["id"] == first["id"]
    assert repeat["reps"] == 8  # unchanged


def test_error_shape_and_validation(client, user_a):
    key = _make_key(user_a, scopes=("read", "write"))
    r = client.post("/api/v1/workouts", headers=_headers(key), json={"name": ""})
    assert r.status_code == 422
    assert set(r.json()) == {"error", "message"}
    r = client.get("/api/v1/workouts/99999", headers=_headers(key))
    assert r.status_code == 404
    assert r.json() == {"error": "not_found", "message": "Workout not found"}
    r = client.post(
        "/api/v1/sessions/99999/sets", headers=_headers(key), json={"exercise_id": 1, "reps": 5}
    )
    assert r.status_code == 404


def test_stats_summary(client, user_a, workout_of_a):
    _, a_key = workout_of_a
    r = client.get(
        "/api/v1/stats/summary", headers=_headers(a_key), params={"date_from": "2020-01-01T00:00:00Z"}
    )
    assert r.status_code == 200
    assert {"session_count", "total_sets", "total_volume_kg", "volume_per_muscle", "best_sets"} <= set(
        r.json()
    )


def test_audit_rows_for_api_writes(client, user_a, workout_of_a, engine):
    from db.models import AuditLog
    from db.session import get_session
    from sqlalchemy import select

    _, a_key = workout_of_a
    client.post("/api/v1/workouts", headers=_headers(a_key), json={"name": "Audited"})
    with get_session() as session:
        actions = [row.action for row in session.scalars(select(AuditLog)).all()]
    assert "workout.create" in actions
