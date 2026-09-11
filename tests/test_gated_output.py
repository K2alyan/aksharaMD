from __future__ import annotations

import pytest

from aksharamd.assessment import AssessmentDisposition, PromotionError, StagedOutput


def test_nonaccepted_staged_output_never_creates_final_output(tmp_path):
    transaction = StagedOutput(tmp_path / "active")
    (transaction.staging_dir / "document.md").write_text("review me", encoding="utf-8")

    assert transaction.promote_if_accepted(AssessmentDisposition.HOLD) is False
    assert transaction.staging_dir.is_dir()
    assert not transaction.final_output_dir.exists()


def test_accepted_staged_output_promotes_complete_generation(tmp_path):
    transaction = StagedOutput(tmp_path / "active")
    (transaction.staging_dir / "document.md").write_text("accepted", encoding="utf-8")
    (transaction.staging_dir / "chunks").mkdir()
    (transaction.staging_dir / "chunks" / "one.json").write_text("{}", encoding="utf-8")

    assert transaction.promote_if_accepted("ACCEPT") is True
    assert transaction.promoted is True
    assert not transaction.staging_dir.exists()
    assert (transaction.final_output_dir / "document.md").read_text(encoding="utf-8") == "accepted"
    assert (transaction.final_output_dir / "chunks" / "one.json").is_file()


def test_accepted_staged_output_refuses_to_replace_existing_final_output(tmp_path):
    final = tmp_path / "active"
    final.mkdir()
    (final / "document.md").write_text("previous", encoding="utf-8")
    transaction = StagedOutput(final)
    (transaction.staging_dir / "document.md").write_text("new", encoding="utf-8")

    with pytest.raises(PromotionError, match="refusing to replace"):
        transaction.promote_if_accepted("ACCEPT")

    assert (final / "document.md").read_text(encoding="utf-8") == "previous"
    assert (transaction.staging_dir / "document.md").read_text(encoding="utf-8") == "new"
