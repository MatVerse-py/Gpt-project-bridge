"""matverse_atlas/claims.py — Registro de contribuições sob gate fail-closed.

Recuperado do corpus MatVerse e promovido para fonte executável.
Sem dependências externas.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from enum import Enum
from typing import Dict, List, Optional

DELTA_CONVERGENCE = timedelta(days=180)
KAPPA_FIRST = 0.70
KAPPA_NOVEL = 0.50


class EvidenceClass(str, Enum):
    OBSERVED_TEXT = "OBSERVED_TEXT"
    FILE_READ = "FILE_READ"
    COMPUTED = "COMPUTED"
    EXTERNAL_VERIFIED = "EXTERNAL_VERIFIED"
    INFERRED = "INFERRED"
    UNVERIFIED = "UNVERIFIED"


ADMISSIBLE_WITNESS = {EvidenceClass.EXTERNAL_VERIFIED, EvidenceClass.COMPUTED}


class Axis(str, Enum):
    INFORMACAO = "INFORMACAO"
    ACAO = "ACAO"
    TEMPO = "TEMPO"


class Maturity(int, Enum):
    CONCEIVED = 0
    FORMALIZED = 1
    IMPLEMENTED = 2
    REPRODUCED = 3
    BENCHMARKED = 4
    EXTERNALLY_VALIDATED = 5


class Priority(str, Enum):
    UNKNOWN = "PRIORITY_UNKNOWN"
    PRIOR_ART_EXISTS = "PRIOR_ART_EXISTS"
    EARLY_FORMULATION = "EARLY_FORMULATION"
    INDEPENDENT_CONVERGENCE = "INDEPENDENT_CONVERGENCE"
    SUPPORTED = "PRIORITY_SUPPORTED"


class ClaimStrength(int, Enum):
    BLOCKED = -1
    DESCRIBED = 0
    ORIGINAL_SYNTHESIS = 1
    NOVEL = 2
    FIRST = 3
    SUPERIOR = 4


@dataclass(frozen=True)
class Witness:
    kind: str
    ref: str
    stamped_at: date
    evidence_class: EvidenceClass

    @property
    def admissible(self) -> bool:
        return self.evidence_class in ADMISSIBLE_WITNESS


@dataclass
class PriorArtSearch:
    venues_searched: List[str] = field(default_factory=list)
    venues_relevant: List[str] = field(default_factory=list)
    earliest_external: Optional[date] = None
    refs: List[str] = field(default_factory=list)
    performed: bool = False

    @property
    def kappa(self) -> float:
        if not self.venues_relevant:
            return 0.0
        hit = len(set(self.venues_searched) & set(self.venues_relevant))
        return hit / len(self.venues_relevant)


@dataclass
class Contribution:
    cid: str
    name: str
    axis: Axis
    claim_text: str
    maturity: Maturity = Maturity.CONCEIVED
    formal_def: Optional[str] = None
    artifact_ref: Optional[str] = None
    test_ref: Optional[str] = None
    baseline_ref: Optional[str] = None
    witnesses: List[Witness] = field(default_factory=list)
    prior_art: PriorArtSearch = field(default_factory=PriorArtSearch)
    psi_form: float = 0.0
    repro: float = 0.0
    bench: float = 0.0
    contradicted_by: Optional[str] = None
    contradiction_rationale: Optional[str] = None

    def t_internal(self) -> Optional[date]:
        adm = [w.stamped_at for w in self.witnesses if w.admissible]
        return min(adm) if adm else None

    def priority(self) -> Priority:
        ti = self.t_internal()
        if ti is None or not self.prior_art.performed:
            return Priority.UNKNOWN
        te = self.prior_art.earliest_external
        k = self.prior_art.kappa
        if te is not None and te < ti - DELTA_CONVERGENCE:
            return Priority.PRIOR_ART_EXISTS
        if te is not None and abs((te - ti).days) <= DELTA_CONVERGENCE.days:
            return Priority.INDEPENDENT_CONVERGENCE
        return Priority.SUPPORTED if k >= KAPPA_FIRST else Priority.EARLY_FORMULATION

    def max_strength(self) -> ClaimStrength:
        if self.contradicted_by:
            return ClaimStrength.BLOCKED
        p, m, k = self.priority(), self.maturity, self.prior_art.kappa
        if (
            m >= Maturity.BENCHMARKED
            and self.baseline_ref
            and self.test_ref
            and p != Priority.PRIOR_ART_EXISTS
        ):
            return ClaimStrength.SUPERIOR
        if p is Priority.SUPPORTED and m >= Maturity.IMPLEMENTED and k >= KAPPA_FIRST:
            return ClaimStrength.FIRST
        if (
            p
            in (
                Priority.EARLY_FORMULATION,
                Priority.INDEPENDENT_CONVERGENCE,
                Priority.SUPPORTED,
            )
            and m >= Maturity.FORMALIZED
            and k >= KAPPA_NOVEL
        ):
            return ClaimStrength.NOVEL
        if m >= Maturity.FORMALIZED and self.formal_def:
            return ClaimStrength.ORIGINAL_SYNTHESIS
        return ClaimStrength.DESCRIBED

    def omega_claim(self) -> float:
        rho = (
            1.0
            if self.priority() is Priority.PRIOR_ART_EXISTS
            else (1.0 - self.prior_art.kappa)
        )
        return round(
            0.35 * self.psi_form
            + 0.25 * self.repro
            + 0.25 * (1.0 - rho)
            + 0.15 * self.bench,
            4,
        )

    def hash(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Atlas:
    """Registro append-only. Rebaixamento é automático; CONTRADICTED exige humano."""

    def __init__(self) -> None:
        self._items: Dict[str, Contribution] = {}
        self._log: List[dict] = []

    def add(self, c: Contribution) -> None:
        if c.cid in self._items:
            raise ValueError(f"cid duplicado: {c.cid}")
        self._items[c.cid] = c
        self._log.append({"op": "add", "cid": c.cid, "hash": c.hash()})

    def get(self, cid: str) -> Contribution:
        return self._items[cid]

    def contradict(self, cid: str, actor: str, rationale: str) -> None:
        if not actor or not rationale:
            raise ValueError("CONTRADICTED requer ator humano e rationale")
        c = self._items[cid]
        c.contradicted_by, c.contradiction_rationale = actor, rationale
        self._log.append(
            {"op": "contradict", "cid": cid, "actor": actor, "rationale": rationale}
        )

    def report(self) -> List[dict]:
        rows = []
        for c in sorted(self._items.values(), key=lambda x: -x.omega_claim()):
            rows.append(
                {
                    "cid": c.cid,
                    "name": c.name,
                    "axis": c.axis.value,
                    "maturity": c.maturity.name,
                    "priority": c.priority().value,
                    "kappa": round(c.prior_art.kappa, 2),
                    "max_claim": c.max_strength().name,
                    "omega_c": c.omega_claim(),
                }
            )
        return rows

    def export(self, path: str) -> str:
        blob = json.dumps(
            {
                "items": [asdict(c) for c in self._items.values()],
                "log": self._log,
            },
            sort_keys=True,
            default=str,
        )
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(blob)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


SEED = [
    ("MV-001", "M-Bit — unidade informacional governada", Axis.INFORMACAO,
     "Informação como entidade cuja existência depende de coerência, custo, persistência e identidade criptográfica: m=(e,Psi,C,tau,h)."),
    ("MV-002", "MNB — memória como população governada", Axis.INFORMACAO,
     "Memória é o que sobreviveu a um regime explícito de admissibilidade."),
    ("MV-003", "Densidade de valor informacional rho", Axis.INFORMACAO,
     "rho = (Psi*tau)/C como critério de retenção e escalonamento."),
    ("MV-004", "Admissibilidade como primitiva universal", Axis.INFORMACAO,
     "A(x|escala) precede execução, persistência e publicação."),
    ("MV-005", "CanExist antes de CanExecute", Axis.INFORMACAO,
     "Ingresso no estado admissível precede autorização de ação."),
    ("MV-006", "Inteligência != soberania", Axis.ACAO,
     "O modelo propõe; entidade independente decide; runtime executa."),
    ("MV-007", "Body-D / Body-A / Body-X", Axis.ACAO,
     "Separação de poderes decisão/administração/execução em runtime."),
    ("MV-008", "Governed Event como átomo computacional", Axis.ACAO,
     "E=(actor,intent,context,policy,risk,decision,action,result,prov,memory)."),
    ("MV-009", "Omega-Gate composto", Axis.ACAO,
     "Admissibilidade multiobjetivo: coerência, desempenho, cauda, prova."),
    ("MV-010", "Three Wise Monkeys — separação epistêmica de privilégio", Axis.ACAO,
     "Manipula sem ver; vê sem publicar; recebe sem persistir."),
    ("MV-011", "Receipt-before-memory", Axis.TEMPO,
     "Ação só entra na memória canônica após adquirir prova mínima."),
    ("MV-012", "Root(execução) == Root(replay)", Axis.TEMPO,
     "Critério criptográfico de equivalência execução/reconstrução."),
    ("MV-013", "Distributed Replay Quorum", Axis.TEMPO,
     "Validade não depende de replay unilateral."),
    ("MV-014", "SVCA — cápsula autoverificável", Axis.TEMPO,
     "Artefato acoplado às suas próprias condições de verificabilidade."),
    ("MV-015", "External Proof of Vitality", Axis.TEMPO,
     "Sistema não prova sozinho sua própria operacionalidade."),
    ("MV-016", "DRIFT — continuidade causal com divergência comportamental", Axis.TEMPO,
     "Sob linhagem causal contínua, divergência comportamental governança-relevante acima do onset de ao menos uma lente legítima é tratada como DRIFT; detecção de DRIFT não equivale a perda de identidade."),
]


def build_seed_atlas() -> Atlas:
    atlas = Atlas()
    for cid, name, axis, claim in SEED:
        atlas.add(Contribution(cid=cid, name=name, axis=axis, claim_text=claim))
    return atlas


if __name__ == "__main__":
    atlas = build_seed_atlas()
    print("ESTADO REAL — sem testemunhas, sem busca de anterioridade")
    print(f"{'CID':<8}{'PRIORIDADE':<26}{'KAPPA':<7}{'ASSERCAO MAXIMA'}")
    for r in atlas.report():
        print(f"{r['cid']:<8}{r['priority']:<26}{r['kappa']:<7}{r['max_claim']}")
