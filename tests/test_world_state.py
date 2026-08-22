from conftest import auth_request


def test_world_real_remains_pending_until_external_gates(client):
    response = auth_request(client, "admin", "GET", "/world-real")
    assert response.status_code == 200
    state = response.json()
    assert state["status"] == "PENDING"
    assert state["criteria"]["authenticated_principals"] is True
    assert state["criteria"]["federation_routing_integrated"] is True
    assert state["criteria"]["endpoint_public_live"] is False
    assert state["criteria"]["external_reproduction"] is False
