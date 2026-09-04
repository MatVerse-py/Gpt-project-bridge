from __future__ import annotations

from pathlib import Path

from app.reproduction_capsule import build_capsule, verify_capsule


def test_capsule_is_deterministic_and_verifiable(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("alpha\n", encoding="utf-8")
    (root / "b.txt").write_text("beta\n", encoding="utf-8")

    first = build_capsule(
        root=root,
        include=["b.txt", "a.txt"],
        output=tmp_path / "first.tar",
        metadata={"contract_hash": "a" * 64, "scope": "TEST"},
    )
    second = build_capsule(
        root=root,
        include=["a.txt", "b.txt"],
        output=tmp_path / "second.tar",
        metadata={"scope": "TEST", "contract_hash": "a" * 64},
    )

    assert first.archive_sha256 == second.archive_sha256
    assert first.manifest_sha256 == second.manifest_sha256
    assert [entry.path for entry in first.entries] == ["a.txt", "b.txt"]

    verified = verify_capsule(first.archive_path)
    assert verified["verified"] is True
    assert verified["archive_sha256"] == first.archive_sha256
    assert verified["manifest_sha256"] == first.manifest_sha256
    assert [item["path"] for item in verified["entries"]] == ["a.txt", "b.txt"]


def test_capsule_rejects_path_escape_and_symlink(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    try:
        build_capsule(root=root, include=["../outside.txt"], output=tmp_path / "bad.tar")
    except ValueError as exc:
        assert "escapes root" in str(exc)
    else:
        raise AssertionError("path escape must be rejected")

    link = root / "link.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        return

    try:
        build_capsule(root=root, include=["link.txt"], output=tmp_path / "symlink.tar")
    except ValueError as exc:
        assert "symlinks are not allowed" in str(exc)
    else:
        raise AssertionError("symlink must be rejected")
