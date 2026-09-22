"""MCP server tests: the full stack over streamable HTTP, in-process.

Uses an httpx ASGI transport pointed at the mounted MCP app, with the MCP
lifespan entered manually (exactly what app.py does in production).
"""

import httpx
import pytest
from fastapi import FastAPI
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

import services.api_keys as api_keys
import services.workouts as workouts
from mcp_server.server import mcp
from services.context import ui_context
from tests.conftest import make_exercise, make_user

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def mcp_http():
    # http_app(path="/") + mount at /mcp == public endpoint exactly /mcp.
    return mcp.http_app(path="/")


@pytest.fixture
def mcp_app(mcp_http):
    app = FastAPI()
    app.mount("/mcp", mcp_http)
    return app


@pytest.fixture
async def mcp_lifespan(mcp_http):
    async with mcp_http.lifespan(mcp_http):
        yield


def _make_key(user_id, scopes=("read",)):
    _, raw = api_keys.create_key(ui_context(user_id), "mcp test", list(scopes))
    return raw


def _client(app, key):
    transport = StreamableHttpTransport(
        "http://testserver/mcp",
        headers={"Authorization": f"Bearer {key}"},
        httpx_client_factory=lambda **kw: httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), **kw
        ),
    )
    return Client(transport)


@pytest.fixture
def user_a(engine):
    return make_user(email="a@example.com")


@pytest.fixture
def user_b(engine):
    return make_user(email="b@example.com")


async def test_list_tools(mcp_app, mcp_lifespan, user_a):
    async with _client(mcp_app, _make_key(user_a)) as client:
        tools = await client.list_tools()
    names = {t.name for t in tools}
    expected = {
        "search_exercises", "get_exercise", "list_workouts", "get_workout",
        "get_last_performance", "get_exercise_history", "list_sessions",
        "get_open_session", "get_training_summary", "create_workout",
        "update_workout", "start_session", "log_set", "update_set",
        "finish_session",
    }
    assert names == expected
    # Read tools carry the readOnlyHint annotation.
    by_name = {t.name: t for t in tools}
    assert by_name["search_exercises"].annotations.read_only_hint is True
    assert by_name["update_workout"].annotations.destructive_hint is True
    assert by_name["log_set"].annotations.destructive_hint is False


async def test_bad_key_rejected(engine, mcp_app, mcp_lifespan):
    from mcp.shared.exceptions import MCPError

    with pytest.raises(MCPError):
        async with _client(mcp_app, "gym_invalidkey0000000000000000000000000000000000000000000000000000") as client:
            await client.list_tools()


async def test_search_exercises(mcp_app, mcp_lifespan, user_a):
    make_exercise(name="Bench Press")
    make_exercise(name="Squat", source_id="squat")
    async with _client(mcp_app, _make_key(user_a)) as client:
        result = await client.call_tool("search_exercises", {"query": "bench"})
    text = result.content[0].text
    assert "Bench Press" in text and "Squat" not in text


async def test_last_performance_and_log_set_flow(mcp_app, mcp_lifespan, user_a):
    make_exercise(name="Bench Press")
    workout = workouts.create_workout(ui_context(user_a), "Push")

    async with _client(mcp_app, _make_key(user_a, scopes=("read", "write"))) as client:
        no_history = await client.call_tool("get_last_performance", {"exercise": "Bench Press"})
        assert "No history" in no_history.content[0].text

        started = await client.call_tool("start_session", {"workout_id": workout.id})
        assert "Push" in started.content[0].text

        logged = await client.call_tool(
            "log_set",
            {"exercise": "Bench Press", "reps": 8, "weight": {"value": 80, "unit": "kg"}},
        )
        assert "set 1: 8 reps @ 80 kg" in logged.content[0].text

        # Repeated logging with the same set_number doesn't duplicate.
        again = await client.call_tool(
            "log_set",
            {"exercise": "Bench Press", "reps": 8, "weight": {"value": 80, "unit": "kg"}, "set_number": 1},
        )
        assert "set 1" in again.content[0].text

        open_session = await client.call_tool("get_open_session", {})
        assert open_session.content[0].text.count("set 1:") == 1

        finished = await client.call_tool("finish_session", {})
        assert "finished" in finished.content[0].text

        perf = await client.call_tool("get_last_performance", {"exercise": "Bench Press"})
        assert "80 kg x 8" in perf.content[0].text


async def test_read_key_cannot_write(mcp_app, mcp_lifespan, user_a):
    from fastmcp.exceptions import ToolError

    async with _client(mcp_app, _make_key(user_a, scopes=("read",))) as client:
        with pytest.raises(ToolError, match="write scope"):
            await client.call_tool("create_workout", {"name": "Nope"})


async def test_isolation_between_users(mcp_app, mcp_lifespan, user_a, user_b):
    from fastmcp.exceptions import ToolError

    workouts.create_workout(ui_context(user_a), "A's plan")
    async with _client(mcp_app, _make_key(user_b, scopes=("read", "write"))) as client:
        listed = await client.call_tool("list_workouts", {})
        assert "A's plan" not in listed.content[0].text
        with pytest.raises(ToolError):
            await client.call_tool("get_workout", {"workout_id": 1})


async def test_ambiguous_name_lists_candidates(mcp_app, mcp_lifespan, user_a):
    make_exercise(name="Cable Row", source_id="cr1")
    make_exercise(name="Cable Row High", source_id="cr2")
    async with _client(mcp_app, _make_key(user_a)) as client:
        from fastmcp.exceptions import ToolError

        # Ambiguity surfaces via name-based tools:
        with pytest.raises(ToolError, match="ambiguous"):
            await client.call_tool("get_last_performance", {"exercise": "Cable"})
