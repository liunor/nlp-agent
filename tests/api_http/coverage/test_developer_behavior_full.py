"""Full-tier behavior checks for developer runtime configuration endpoints."""

from __future__ import annotations

import pytest

from ..support.environment import SeededUser
from ..support.http import json_response, problem_response


pytestmark = pytest.mark.api_full


def test_developer_health_and_model_configuration_round_trip(
    authenticated_client_for,
    developer_user: SeededUser,
) -> None:
    client = authenticated_client_for(developer_user)
    health = json_response(client.get("/api/v1/developer/health"), 200)
    assert "status" in health

    snapshot = json_response(client.get("/api/v1/developer/snapshot"), 200)
    models = snapshot["models"]
    providers = models["providers"]
    presets = models["presets"]
    routes = models["routes"]
    profiles = models["profiles"]
    assert providers and presets and routes and profiles

    provider_name, provider_value = next(iter(providers.items()))
    provider_config = {
        key: value
        for key, value in provider_value.items()
        if key != "api_key_configured"
    }
    provider_result = json_response(
        client.put(
            f"/api/v1/developer/models/providers/{provider_name}",
            json={"config": provider_config},
        ),
        200,
    )
    assert provider_result["provider"] == provider_name

    preset_name, preset_config = next(iter(presets.items()))
    preset_result = json_response(
        client.put(
            f"/api/v1/developer/models/presets/{preset_name}",
            json={"config": preset_config},
        ),
        200,
    )
    assert preset_result["preset"] == preset_name

    route_name, route_config = next(iter(routes.items()))
    route_result = json_response(
        client.put(
            f"/api/v1/developer/models/routes/{route_name}",
            json={"config": route_config},
        ),
        200,
    )
    assert route_result["route"] == route_name

    profile_name, profile_config = next(iter(profiles.items()))
    profile_result = json_response(
        client.put(
            f"/api/v1/developer/models/profiles/{profile_name}",
            json={"config": profile_config},
        ),
        200,
    )
    assert profile_result["profile"] == profile_name

    invalid = client.put(
        "/api/v1/developer/models/routes/not.valid",
        json={"config": {"primary": "worker-flash"}},
    )
    assert problem_response(invalid, 422)["code"] == "developer_configuration_invalid"
