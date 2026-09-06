from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class Speaker(str, Enum):
    USER = "USER"
    MODEL = "MODEL"
    PROJECT_ARTIFACT = "PROJECT_ARTIFACT"
    CODE = "CODE"
    EXTERNAL_SOURCE = "EXTERNAL_SOURCE"


class SpeechAct(str, Enum):
    QUESTION = "QUESTION"
    HYPOTHESIS = "HYPOTHESIS"
    PROPOSAL = "PROPOSAL"
    DEFINITION = "DEFINITION"
    CORRECTION = "CORRECTION"
    REJECTION = "REJECTION"
    ACCEPTANCE = "ACCEPTANCE"
    CONSTRAINT = "CONSTRAINT"
    IMPLEMENTATION_ORDER = "IMPLEMENTATION_ORDER"
    OBSERVATION = "OBSERVATION"
    CLAIM = "CLAIM"


AUTHORITY_SCORE: dict[tuple[Speaker, SpeechAct], int] = {
    (Speaker.USER, SpeechAct.CORRECTION): 100,
    (Speaker.USER, SpeechAct.DEFINITION): 95,
    (Speaker.USER, SpeechAct.CONSTRAINT): 92,
    (Speaker.USER, SpeechAct.IMPLEMENTATION_ORDER): 90,
    (Speaker.USER, SpeechAct.ACCEPTANCE): 80,
    (Speaker.PROJECT_ARTIFACT, SpeechAct.DEFINITION): 78,
    (Speaker.CODE, SpeechAct.OBSERVATION): 72,
    (Speaker.EXTERNAL_SOURCE, SpeechAct.OBSERVATION): 70,
    (Speaker.MODEL, SpeechAct.OBSERVATION): 40,
    (Speaker.MODEL, SpeechAct.PROPOSAL): 25,
    (Speaker.MODEL, SpeechAct.HYPOTHESIS): 20,
}


@dataclass(frozen=True)
class SemanticObservation:
    statement_id: str
    term: str
    meaning: str
    speaker: Speaker
    speech_act: SpeechAct
    observed_at: str | None
    source_id: str
    supersedes: tuple[str, ...] = ()


@dataclass(frozen=True)
class SemanticResolution:
    preferred: SemanticObservation | None
    alternatives: tuple[SemanticObservation, ...]
    state: str
    reason: str


def authority_score(observation: SemanticObservation) -> int:
    return AUTHORITY_SCORE.get((observation.speaker, observation.speech_act), 50)


def _date_key(value: str | None) -> str:
    return value or "9999-12-31T23:59:59Z"


def resolve_semantics(observations: Iterable[SemanticObservation]) -> SemanticResolution:
    items = tuple(observations)
    if not items:
        return SemanticResolution(None, (), "UNRESOLVED", "no observations")

    active = {item.statement_id: item for item in items}
    superseded_ids = {sid for item in items for sid in item.supersedes}
    active_items = tuple(item for sid, item in active.items() if sid not in superseded_ids) or items

    ranked = sorted(
        active_items,
        key=lambda item: (-authority_score(item), _date_key(item.observed_at), item.statement_id),
    )
    preferred = ranked[0]

    same_authority = [item for item in ranked if authority_score(item) == authority_score(preferred)]
    competing = [item for item in same_authority if item.meaning.casefold() != preferred.meaning.casefold()]
    if competing:
        return SemanticResolution(
            preferred=None,
            alternatives=tuple(ranked),
            state="HOLD_SEMANTICS",
            reason="equally authoritative active meanings conflict",
        )

    state = "USER_CORRECTED" if (
        preferred.speaker is Speaker.USER and preferred.speech_act is SpeechAct.CORRECTION
    ) else "RESOLVED_WITH_PROVENANCE"
    return SemanticResolution(
        preferred=preferred,
        alternatives=tuple(item for item in ranked[1:]),
        state=state,
        reason="authority first; antiquity breaks ties within equal authority",
    )
