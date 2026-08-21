from fastapi.testclient import TestClient
from app.main import app
from app import storage


def setup_function():
    if storage.DB_PATH.exists():
        storage.DB_PATH.unlink()


def test_pass_and_replay():
    c = TestClient(app)
    r = c.post('/bridge/ingress', json={
        'payload': {'task':'ping'},
        'action':'EXECUTE',
        'human': {'consent': True, 'purpose':'test'},
        'ontology_ok': True,
        'signature_valid': True,
        'transition_valid': True
    })
    assert r.status_code == 200
    body = r.json()
    assert body['decision'] == 'PASS'
    assert body['executed'] is True
    replay = c.get('/replay').json()
    assert replay['integrity']['ok'] is True
    assert replay['replayed_state']['accepted'] == 1


def test_valid_signature_invalid_transition_blocks():
    c = TestClient(app)
    r = c.post('/bridge/ingress', json={
        'payload': {'task':'danger'},
        'action':'COMMIT',
        'ontology_ok': True,
        'signature_valid': True,
        'transition_valid': False
    }).json()
    assert r['decision'] == 'BLOCK'
    assert r['executed'] is False
    assert r['po_state'] == 'PO_FAIL'


def test_human_serialization_blocks():
    c = TestClient(app)
    r = c.post('/bridge/ingress', json={
        'payload': {'task':'export'},
        'action':'EXPORT',
        'human': {'consent': True, 'purpose':'migration', 'serialize_human': True},
        'ontology_ok': True,
        'signature_valid': True,
        'transition_valid': True
    }).json()
    assert r['decision'] == 'BLOCK'
    assert r['quarantined'] is True


def test_world_real_stays_pending_until_external_evidence():
    c = TestClient(app)
    state = c.get('/world-real').json()
    assert state['status'] == 'PENDING'
    assert state['criteria']['external_reproduction'] is False
