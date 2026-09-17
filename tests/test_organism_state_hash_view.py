from app.core import stable_hash
from app.organism_loop import GovernedOrganism

FROZEN = "a" * 64


def _make() -> GovernedOrganism:
    return GovernedOrganism(
        organism_id="state-hash-view-organism",
        frozen_contract_hash=FROZEN,
        runtime_id="state-hash-view-runtime",
        state_secret="state-hash-view-secret",
        authority_secrets={"omega-authority": "state-hash-view-authority"},
    )


def test_internal_hash_view_matches_public_payload_root_exactly():
    organism = _make()
    for i in range(30):
        organism.evaluate(event_id=f"hash-view-{i}", proposal={"action": "OBSERVE", "i": i})
        assert stable_hash(organism._state_hash_payload()) == stable_hash(organism.state_payload())
        assert organism.state_root() == stable_hash(organism.state_payload())


def test_public_state_payload_remains_defensive_copy():
    organism = _make()
    organism.evaluate(event_id="copy-seed", proposal={"action": "OBSERVE", "value": 1})
    canonical_root = organism.state_root()

    exported_payload = organism.state_payload()
    exported_payload["lineage"][0]["proposal"]["value"] = 999
    exported_payload["lineage"].append({"type": "FORGED"})

    assert organism.state_root() == canonical_root
    assert organism.state_payload()["lineage"][0]["proposal"]["value"] == 1
    assert all(item.get("type") != "FORGED" for item in organism.state_payload()["lineage"])


def test_export_state_root_remains_public_payload_hash():
    organism = _make()
    for i in range(12):
        organism.evaluate(event_id=f"export-hash-view-{i}", proposal={"action": "OBSERVE", "i": i})
    exported = organism.export_state()
    assert exported["state_root"] == stable_hash(organism.state_payload())
