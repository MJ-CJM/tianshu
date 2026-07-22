from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_shared_evolution_services_are_wired_before_every_ingress_consumer() -> None:
    source = _source("src/tianshu/app.py")

    evolution = source.index("bootstrap.wire_evolution_services(")
    consumers = (
        "tools = await bootstrap.wire_tools(",
        "bootstrap.wire_persona(",
        "bootstrap.wire_channels(",
        "bootstrap.wire_executor(",
        "bootstrap.wire_scheduling(",
    )
    assert all(evolution < source.index(consumer) for consumer in consumers)


def test_production_ingresses_use_the_shared_edict_application_service() -> None:
    expected_wiring = {
        "src/tianshu/bootstrap/wiring_tools.py": "app.state.edict_application_service",
        "src/tianshu/bootstrap/wiring_persona.py": "app.state.edict_application_service",
        "src/tianshu/bootstrap/wiring_executor.py": "app.state.edict_application_service",
        "src/tianshu/bootstrap/wiring_scheduler.py": "app.state.edict_application_service",
        "src/tianshu/bootstrap/wiring_channels.py": "app.state.edict_application_service",
        "src/tianshu/gateway/mcp_server.py": "app.state.edict_application_service.submit(",
        "src/tianshu/gateway/edicts_api.py": "request.app.state.edict_application_service",
    }
    for relative, expression in expected_wiring.items():
        assert expression in _source(relative), relative

    bare_construction = re.compile(
        r"EdictApplicationService\(\s*(?:app\.state\.|self\._)?storage\s*\)"
    )
    offenders = [
        str(path.relative_to(ROOT))
        for path in (ROOT / "src/tianshu").rglob("*.py")
        if bare_construction.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_dispatcher_and_submission_share_the_one_router_from_app_state() -> None:
    evolution_wiring = _source("src/tianshu/bootstrap/wiring_skills.py")
    scheduler_wiring = _source("src/tianshu/bootstrap/wiring_scheduler.py")

    assert "challenger_router=challenger_router" in evolution_wiring
    assert "challenger_router=app.state.challenger_router" in scheduler_wiring
