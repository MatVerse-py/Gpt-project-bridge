from app.core import stable_hash
from app.qex_substrate import ExperimentContract


def qex_substrate_01_contract() -> ExperimentContract:
    """Canonical frozen contract for QEX-SUBSTRATE-01.

    Derived comparison metrics such as TVD are intentionally not embedded in
    the contract hash. They belong to the adjudication layer, so the same
    experiment identity can be replayed across ideal, noisy, SDK, and hardware-
    derived substrates without silently changing the experiment definition.
    """
    return ExperimentContract(
        experiment_id="QEX-SUBSTRATE-01",
        problem_hash=stable_hash({"problem": "NOT", "domain": "bit"}),
        objective="preserve experiment identity while changing computational substrate",
        required_capabilities=("bit_not",),
        metric_schema_hash=stable_hash({"metrics": ["probability_0", "probability_1"]}),
        observable_schema_hash=stable_hash({"observables": ["result"]}),
        evidence_policy_hash=stable_hash({"policy": "evidence-v1"}),
        require_classical_baseline=True,
    )
