from app.api.v1.health import health


def test_health_endpoint_returns_expected_shape() -> None:
    payload = health()
    assert payload["status"] in {"ok", "degraded"}
    assert isinstance(payload["version"], str)
    assert "database" in payload
    assert isinstance(payload["database"]["connected"], bool)
