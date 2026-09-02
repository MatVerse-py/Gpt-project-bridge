from app.latex_metadata import representation_from_latex
from app.source_evidence import EvidenceState, RepresentationType, SourceRepresentation, build_source_evidence
from app.source_resolver import resolve_source


TEX = r"""
\documentclass{article}
\title{MatVerse Paper 03: MNB - Substrato Mem-nano-Bits}
\author{Arêas, Mateus Alves}
\date{2026-06-20}
\newcommand{\version}{v1.0.0}
\begin{document}
DOI: 10.5281/zenodo.19112289
ORCID: 0009-0008-2973-4047
\end{document}
"""


def test_latex_parser_extracts_version_identity_metadata():
    rep = representation_from_latex(content=TEX, locator="library://paper03.tex")
    assert rep.kind is RepresentationType.LATEX_SOURCE
    assert rep.metadata["title"] == "MatVerse Paper 03: MNB - Substrato Mem-nano-Bits"
    assert rep.metadata["author"] == "Arêas, Mateus Alves"
    assert rep.metadata["version"] == "v1.0.0"
    assert rep.metadata["doi"] == "10.5281/zenodo.19112289"
    assert rep.metadata["orcid"] == "0009-0008-2973-4047"


def test_preserved_tex_is_independent_structured_evidence_but_not_official_by_extension():
    rep = representation_from_latex(content=TEX, locator="library://paper03.tex")
    evidence = build_source_evidence(original_url="library://paper03.tex", representations=(rep,))
    assert evidence.state is EvidenceState.VERIFIED_SNAPSHOT
    assert evidence.independent_evidence is True
    assert evidence.official_version_evidence is False
    assert evidence.evidence_tier == "P3"


def test_official_tex_with_verified_commit_anchor_is_p5_for_version_identity():
    rep = representation_from_latex(
        content=TEX,
        locator="repo://papers/paper03.tex",
        official_version=True,
        repo="MatVerse-py/papers",
        commit_sha="a" * 40,
        commit_verified=True,
    )
    evidence = build_source_evidence(original_url="repo://papers/paper03.tex", representations=(rep,))
    assert evidence.state is EvidenceState.VERIFIED_SNAPSHOT
    assert evidence.official_version_evidence is True
    assert evidence.evidence_tier == "P5"
    assert evidence.identifiers["version"] == "v1.0.0"


def test_official_tex_flag_without_verified_anchor_does_not_gain_p5():
    rep = representation_from_latex(
        content=TEX,
        locator="library://paper03.tex",
        official_version=True,
        commit_sha="a" * 40,
        commit_verified=False,
    )
    evidence = build_source_evidence(original_url="library://paper03.tex", representations=(rep,))
    assert evidence.official_version_evidence is False
    assert evidence.evidence_tier == "P3"
    assert any(c.code == "OFFICIAL_VERSION_UNANCHORED" and not c.blocking for c in evidence.conflicts)


def test_official_tex_conflict_with_other_high_priority_structured_metadata_fails_closed():
    tex = representation_from_latex(
        content=TEX,
        locator="repo://papers/paper03.tex",
        official_version=True,
        commit_sha="a" * 40,
        commit_verified=True,
    )
    api = SourceRepresentation.from_text(
        kind=RepresentationType.API_METADATA,
        locator="api://publication",
        content='{"version":"v2.0.0"}',
        metadata={"version": "v2.0.0"},
    )
    evidence = build_source_evidence(original_url="repo://papers/paper03.tex", representations=(tex, api))
    assert evidence.state is EvidenceState.CONFLICT
    assert any(c.code == "IDENTIFIER_CONFLICT" and c.field == "version" and c.blocking for c in evidence.conflicts)


def test_resolver_can_use_latex_and_receipt_hashes_official_version_decision():
    def tex_loader(_: str):
        return representation_from_latex(
            content=TEX,
            locator="repo://papers/paper03.tex",
            official_version=True,
            release_tag="v1.0.0",
            tag_verified=True,
        )

    result = resolve_source(
        original_url="repo://papers/paper03.tex",
        loaders={RepresentationType.LATEX_SOURCE: tex_loader},
    )
    assert result.evidence.official_version_evidence is True
    assert result.receipt["schema"] == "matverse.evidence-receipt.v1"
    assert result.receipt["output_hash"]
    assert any(a.kind is RepresentationType.LATEX_SOURCE and a.status == "HIT" for a in result.attempts)
