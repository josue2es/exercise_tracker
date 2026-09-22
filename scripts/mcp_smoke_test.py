"""MCP smoke test: list tools, search, log a set against a running server.

Usage:
    python -m scripts.mcp_smoke_test --url http://127.0.0.1:8080/mcp --key gym_...

Creates (or reuses) a workout named 'Smoke Test', starts a session, logs one
set and finishes the session. Safe to re-run.
"""

import argparse
import asyncio
import sys

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport


async def main(url: str, key: str) -> int:
    transport = StreamableHttpTransport(url, headers={"Authorization": f"Bearer {key}"})
    async with Client(transport) as client:
        tools = await client.list_tools()
        print(f"1. listed {len(tools)} tools: {', '.join(sorted(t.name for t in tools))}")

        result = await client.call_tool("search_exercises", {"query": "bench press"})
        print(f"2. search: {result.content[0].text.splitlines()[0]}")

        workouts = await client.call_tool("list_workouts", {})
        print(f"3. workouts: {workouts.content[0].text.splitlines()[0] if workouts.content[0].text else '(none)'}")

        if "Smoke Test" not in workouts.content[0].text:
            created = await client.call_tool(
                "create_workout",
                {"name": "Smoke Test", "exercises": [{"exercise": "Barbell Bench Press - Medium Grip", "sets": 1, "reps_min": 8, "reps_max": 12}]},
            )
            print(f"4. created workout: {created.content[0].text.splitlines()[0]}")
        else:
            print("4. reusing existing 'Smoke Test' workout")

        listed = await client.call_tool("list_workouts", {})
        workout_id = int(next(line for line in listed.content[0].text.splitlines() if "Smoke Test" in line).split("[id ")[1].split("]")[0])

        started = await client.call_tool("start_session", {"workout_id": workout_id})
        print(f"5. {started.content[0].text}")

        logged = await client.call_tool(
            "log_set", {"exercise": "Barbell Bench Press - Medium Grip", "reps": 8, "weight": {"value": 60, "unit": "kg"}}
        )
        print(f"6. {logged.content[0].text}")

        finished = await client.call_tool("finish_session", {})
        print(f"7. {finished.content[0].text}")

    print("SMOKE TEST OK")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MCP smoke test")
    parser.add_argument("--url", default="http://127.0.0.1:8080/mcp")
    parser.add_argument("--key", required=True)
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.url, args.key)))
