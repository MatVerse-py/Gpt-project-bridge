from app.html_metadata import representation_from_html
from app.semantic_provenance import (
    SemanticObservation,
    Speaker,
    SpeechAct,
    resolve_semantics,
)
from app.source_evidence import (
    EvidenceState,
    RepresentationType,
    SourceRepresentation,
    build_source_evidence,
)
from app.source_resolver import resolve_source, source_is_admissible


ZENODO_HTML = """
<html>
<head>
<meta name="description" content="Paper 03. DOI: Será atribuído após publicação. Autor ORCID: 0009-0008-2973-4047">
<meta name="citation_title" content="MatVerse Paper 03: MNB - Substrato Mem-nano-Bits">
<meta name="citation_author" content="Arêas, Mateus Alves">
<meta name="citation_doi" content="10.5281/zenodo.19112289">
<meta name="citation_pdf_url" content="https://zenodo.org/records/19112289/files/03_MNB.pdf">
<link rel="canonical" href="https://zenodo.org/records/19112289">
</head>
</html>
"""


def test_saved_html_structured_metadata_beats_stale_prose():
    rep = representation_from_html(
        html=ZENODO_HTML,
        locator="library://19112289.html",
        captured_at="2026-05-23T19:20:06Z",
    )
    evidence = build_source_evidence(
        original_url="https://zenodo.org/records/19112289",
        representations=[rep],
    )

    assert evidence.state is EvidenceState.VERIFIED_SNAPSHOT
    assert evidence.identifiers["doi"] == "10.5281/zenodo.19112289"
    assert evidence.identifiers["orcid"] == "0009-0008-2973-4047"
    assert evidence.identifiers["canonical_url"] == "https://zenodo.org/records/19112289"
    assert any(c.code == "STALE_PROSE" and not c.blocking for c in evidence.conflicts)


def test_live_failure_falls_back_to_saved_html_and_generates_receipt():
    def live(_: str):
        raise RuntimeError("javascript blocked")

    def saved(_: str):
        return representation_from_html(
            html=ZENODO_HTML,
            locator="library://19112289.html",
            captured_at="2026-05-23T19:20:06Z",
        )

    result = resolve_source(
        original_url="https://zenodo.org/records/19112289",
        loaders={
            RepresentationType.LIVE_HTML: live,
            RepresentationType.SAVED_HTML: saved,
        },
    )

    assert result.evidence.state is EvidenceState.VERIFIED_SNAPSHOT
    assert source_is_admissible(result)
    assert any(a.kind is RepresentationType.LIVE_HTML and a.status == "ERROR" for a in result.attempts)
    assert any(a.kind is RepresentationType.SAVED_HTML and a.status == "HIT" for a in result.attempts)
    assert result.receipt["schema"] == "matverse.evidence-receipt.v1"
    assert result.receipt["event_type"] == "SOURCE_RESOLUTION"


def test_structured_identifier_conflict_fails_closed():
    saved = representation_from_html(
        html=ZENODO_HTML,
        locator="library://19112289.html",
    )
    api = SourceRepresentation.from_text(
        kind=RepresentationType.API_METADATA,
        locator="api://zenodo/19112289",
        content='{"doi":"10.5281/zenodo.99999999"}',
        metadata={"doi": "10.5281/zenodo.99999999"},
    )
    evidence = build_source_evidence(
        original_url="https://zenodo.org/records/19112289",
        representations=[saved, api],
    )
    assert evidence.state is EvidenceState.CONFLICT
    assert not any(c.code == "STALE_PROSE" and c.blocking for c in evidence.conflicts)
    assert any(c.code == "IDENTIFIER_CONFLICT" and c.blocking for c in evidence.conflicts)


def test_tamper_signal_blocks():
    rep = SourceRepresentation.from_text(
        kind=RepresentationType.CORPUS_COPY,
        locator="library://copy",
        content="x",
        metadata={"tampered": True},
    )
    evidence = build_source_evidence(
        original_url="https://example.invalid",
        representations=[rep],
    )
    assert evidence.state is EvidenceState.BLOCK_TAMPERED


def test_user_correction_overrides_older_model_proposal():
    observations = [
        SemanticObservation(
            statement_id="s1",
            term="MENBIT",
            meaning="Memory Evidence Node Bit",
            speaker=Speaker.MODEL,
            speech_act=SpeechAct.PROPOSAL,
            observed_at="2025-01-01",
            source_id="chat:model",
        ),
        SemanticObservation(
            statement_id="s2",
            term="MENBIT",
            meaning="historical abbreviated form of MNB",
            speaker=Speaker.USER,
            speech_act=SpeechAct.CORRECTION,
            observed_at="2026-07-02",
            source_id="chat:user",
            supersedes=("s1",),
        ),
    ]
    resolved = resolve_semantics(observations)
    assert resolved.state == "USER_CORRECTED"
    assert resolved.preferred is not None
    assert resolved.preferred.statement_id == "s2"


def test_antiquity_breaks_tie_for_equal_authority():
    observations = [
        SemanticObservation(
            statement_id="old",
            term="COG",
            meaning="Cognitive Organization & Generation",
            speaker=Speaker.PROJECT_ARTIFACT,
            speech_act=SpeechAct.DEFINITION,
            observed_at="2025-03-05",
            source_id="paper:old",
        ),
        SemanticObservation(
            statement_id="new",
            term="COG",
            meaning="Cognitive Organization & Generation",
            speaker=Speaker.PROJECT_ARTIFACT,
            speech_act=SpeechAct.DEFINITION,
            observed_at="2026-01-10",
            source_id="paper:new",
        ),
    ]
    resolved = resolve_semantics(observations)
    assert resolved.preferred is not None
    assert resolved.preferred.statement_id == "old"
