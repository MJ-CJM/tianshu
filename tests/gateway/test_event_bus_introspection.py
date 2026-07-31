"""Event-bus introspection API regression tests."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tianshu.bus.event_bus import EventBus
from tianshu.gateway.system_api import system_router


class _CallableHandler:
    async def __call__(self, _event) -> None:
        return None


def test_handlers_endpoint_names_callable_objects_by_type() -> None:
    app = FastAPI()
    event_bus = EventBus()
    event_bus.on(
        "test.callable",
        _CallableHandler(),
        consumer_name="test.callable-handler.v1",
    )
    app.state.event_bus = event_bus
    app.include_router(system_router, prefix="/api")

    with TestClient(app) as client:
        response = client.get("/api/event-bus/handlers")

    assert response.status_code == 200
    assert response.json()["data"]["test.callable"] == [
        {"handler": "_CallableHandler", "priority": 100}
    ]
