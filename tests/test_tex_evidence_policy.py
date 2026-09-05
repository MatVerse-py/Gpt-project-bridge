from pathlib import Path

from app.latex_metadata import representation_from_arxiv_latex, representation_from_latex
from app.latex_source import latex_closure, representation_from_latex_file
from app.source_evidence import EvidenceState, RepresentationType, SourceRepresentation, build_source_evidence
from app.source_resolver import resolve_source, source_is_admissible


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


def test_latex_parser_extracts_version_but_keeps_external_identifiers_claimed():
    rep = representation_from_latex(content=TEX, locator="library://paper03.tex")
    assert rep.kind is RepresentationType.LATEX_SOURCE
    assert rep.metadata["title"] == "MatVerse Paper 03: MNB - Substrato Mem-nano-Bits"
    assert rep.metadata["author"] == "Arêas, Mateus Alves"
    assert rep.metadata["version"] == "v1.0.0"
    assert "doi" not in rep.metadata
    assert "orcid" not in rep.metadata
    assert rep.metadata["claimed_identifiers"]["doi"] == ("10.5281/zenodo.19112289",)
    assert rep.metadata["claimed_identifiers"]["orcid"] == ("0009-0008-2973-4047",)


def test_preserved_standalone_tex_is_independent_structured_evidence_but_not_publication_proof():
    rep = representation_from_latex(content=TEX, locator="library://paper03.tex")
    evidence = build_source_evidence(original_url="library://paper03.tex", representations=(rep,))
    assert evidence.state is EvidenceState.VERIFIED_SNAPSHOT
    assert evidence.independent_evidence is True
    assert evidence.official_version_evidence is False
    assert evidence.evidence_tier == "P3"
    assert evidence.authority["content"] == 1.0
    assert evidence.authority["version"] == 1.0
    assert evidence.authority["publication"] == 0.0
    assert "doi" not in evidence.identifiers
    assert evidence.claimed_identifiers["doi"] == ("10.5281/zenodo.19112289",)


def test_tex_claimed_doi_is_resolved_only_by_independent_doi_metadata():
    tex = representation_from_latex(content=TEX, locator="library://paper03.tex")
    doi = SourceRepresentation.from_text(
        kind=RepresentationType.DOI_METADATA,
        locator="doi://10.5281/zenodo.19112289",
        content='{"doi":"10.5281/zenodo.19112289"}',
        metadata={"doi": "10.5281/zenodo.19112289"},
    )
    evidence = build_source_evidence(original_url="library://paper03.tex", representations=(tex, doi))
    assert evidence.identifiers["doi"] == "10.5281/zenodo.19112289"
    assert evidence.claimed_identifiers["doi"] == ("10.5281/zenodo.19112289",)
    assert evidence.authority["publication"] == 1.0


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
    assert evidence.authority["publication"] == 0.0


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


def test_tex_with_unresolved_local_reference_is_partial_and_not_admissible():
    content = TEX.replace("\\begin{document}", "\\input{missing-section}\\n\\begin{document}")
    rep = representation_from_latex(
        content=content,
        locator="library://paper03.tex",
        official_version=True,
        commit_sha="a" * 40,
        commit_verified=True,
    )
    evidence = build_source_evidence(original_url="library://paper03.tex", representations=(rep,))
    result = resolve_source(
        original_url="library://paper03.tex",
        loaders={RepresentationType.LATEX_SOURCE: lambda _: rep},
    )
    assert evidence.state is EvidenceState.PARTIAL
    assert evidence.evidence_tier == "P1"
    assert evidence.official_version_evidence is False
    assert evidence.authority["content"] == 0.0
    assert any(c.code == "LATEX_CLOSURE_INCOMPLETE" for c in evidence.conflicts)
    assert source_is_admissible(result) is False


def test_transitive_closure_includes_tex_bib_and_graphics(tmp_path: Path):
    (tmp_path / "main.tex").write_text(
        r"\documentclass{article}\input{section}\includegraphics{fig}\bibliography{refs}",
        encoding="utf-8",
    )
    (tmp_path / "section.tex").write_text("section", encoding="utf-8")
    (tmp_path / "fig.png").write_bytes(b"PNG")
    (tmp_path / "refs.bib").write_text("@misc{x}", encoding="utf-8")

    closure = latex_closure(tmp_path / "main.tex", root=tmp_path)
    assert closure.complete is True
    assert closure.relative_files == ("fig.png", "main.tex", "refs.bib", "section.tex")
    assert len(closure.digest) == 64


def test_closure_digest_changes_when_dependency_changes(tmp_path: Path):
    (tmp_path / "main.tex").write_text(r"\input{section}", encoding="utf-8")
    section = tmp_path / "section.tex"
    section.write_text("v1", encoding="utf-8")
    first = latex_closure(tmp_path / "main.tex", root=tmp_path).digest
    section.write_text("v2", encoding="utf-8")
    second = latex_closure(tmp_path / "main.tex", root=tmp_path).digest
    assert first != second


def test_closure_aware_file_can_establish_official_version(tmp_path: Path):
    main = tmp_path / "main.tex"
    part = tmp_path / "part.tex"
    main.write_text(r"\newcommand{\version}{v2}\input{part}", encoding="utf-8")
    part.write_text("body", encoding="utf-8")

    rep = representation_from_latex_file(
        entry=main,
        root=tmp_path,
        official_version=True,
        commit_sha="b" * 40,
        commit_verified=True,
    )
    evidence = build_source_evidence(original_url=main.as_uri(), representations=(rep,))
    assert rep.metadata["closure_complete"] is True
    assert len(rep.metadata["closure_digest"]) == 64
    assert evidence.official_version_evidence is True
    assert evidence.evidence_tier == "P5"


def test_official_tex_conflict_with_other_high_priority_version_fails_closed():
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


def test_arxiv_source_needs_verified_external_custody_for_publication_authority():
    unverified = representation_from_arxiv_latex(content=TEX, locator="arxiv://source/1234")
    verified = representation_from_arxiv_latex(
        content=TEX,
        locator="arxiv://source/1234",
        canonical_url="https://arxiv.org/abs/1234.5678",
        canonical_verified=True,
        external_timestamp_verified=True,
    )
    a = build_source_evidence(original_url="arxiv://source/1234", representations=(unverified,))
    b = build_source_evidence(original_url="arxiv://source/1234", representations=(verified,))
    assert a.authority["publication"] == 0.0
    assert b.authority["publication"] == 0.70


def test_resolver_receipt_commits_claims_authority_and_official_version_evidence():
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
    assert result.evidence.authority["publication"] == 0.0
    assert result.evidence.claimed_identifiers["doi"] == ("10.5281/zenodo.19112289",)
    assert result.receipt["schema"] == "matverse.evidence-receipt.v1"
    assert len(result.receipt["output_hash"]) == 64
    assert len(result.receipt["receipt_hash"]) == 64
    assert any(a.kind is RepresentationType.LATEX_SOURCE and a.status == "HIT" for a in result.attempts)
