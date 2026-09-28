from app.core import stable_hash
from app.qex_adapters import IdealStatevectorNotAdapter
from app.qex_experiment import qex_substrate_01_contract
from app.qex_hardware_noise_adapter import AerHardwareSnapshotNotAdapter
from app.qex_noisy_adapter import total_variation_distance


def test_hardware_snapshot_preserves_frozen_contract_for_both_bits():
    contract = qex_substrate_01_contract()
    adapter = AerHardwareSnapshotNotAdapter(shots=1024, seed=369)

    for bit in (0, 1):
        ideal = IdealStatevectorNotAdapter().execute(contract, {"bit": bit})
        observed = adapter.execute(contract, {"bit": bit})

        assert observed.contract_hash == ideal.contract_hash == contract.contract_hash
        assert observed.problem_hash == ideal.problem_hash
        assert observed.metric_schema_hash == ideal.metric_schema_hash
        assert observed.observable_schema_hash == ideal.observable_schema_hash
        assert observed.evidence_policy_hash == ideal.evidence_policy_hash
        assert observed.regime.value == "QUANTUM_GATE"
        assert abs(observed.probability_0 + observed.probability_1 - 1.0) < 1e-12
        assert 0.0 <= total_variation_distance(ideal.canonical_observable(), observed.canonical_observable()) <= 1.0
        assert observed.backend_metadata_hash != stable_hash({})


def test_hardware_snapshot_is_replayable_with_fixed_seed():
    contract = qex_substrate_01_contract()
    first = AerHardwareSnapshotNotAdapter(shots=512, seed=369).execute(contract, {"bit": 0})
    second = AerHardwareSnapshotNotAdapter(shots=512, seed=369).execute(contract, {"bit": 0})

    assert first.canonical_observable() == second.canonical_observable()
    assert first.backend_metadata_hash == second.backend_metadata_hash
    assert first.receipt["receipt_hash"] == second.receipt["receipt_hash"]


def test_hardware_snapshot_configuration_fails_closed():
    for kwargs in ({"shots": 0}, {"shots": True}, {"seed": -1}, {"seed": True}):
        try:
            AerHardwareSnapshotNotAdapter(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid configuration accepted: {kwargs}")
