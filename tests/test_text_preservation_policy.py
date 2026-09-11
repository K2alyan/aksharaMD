"""Bounded preservation policy controls and corruption regressions."""

import hashlib

import pytest

from aksharamd.assessment import Assessor, CandidateArtifact, SourceArtifact
from aksharamd.assessment.text_preservation import SOURCE_TEXT_PRESERVATION_POLICY_ID


def _artifact(kind, text, media_type="text/markdown"):
    data = text.encode("utf-8")
    return kind(data=data, byte_size=len(data), content_hash=hashlib.sha256(data).hexdigest(), media_type=media_type)


def _assess(source, candidate, **kwargs):
    return Assessor().assess(source=_artifact(SourceArtifact, source),
                             candidate=_artifact(CandidateArtifact, candidate),
                             policy_id=SOURCE_TEXT_PRESERVATION_POLICY_ID, **kwargs)


@pytest.mark.parametrize("source,candidate", [
    ("Destination: west warehouse.", "# Destination: **west warehouse**."),
    ("The shipment is not approved.", "The shipment\nis not\napproved."),
    ("Subtotal: $900\nTax: $90", "Tax: $90\nSubtotal: $900"),
    ("The manual illustrates [PLACEHOLDER].", "The manual illustrates [PLACEHOLDER]."),
    ("```python\nif ready:\n    ship()\n```", "```python\nif ready:\n    ship()\n```"),
    ("Owner: Alice\nBob: Manager", "**Owner:** Alice\n**Bob:** Manager"),
])
def test_bounded_clean_controls_accept(source, candidate):
    result = _assess(source, candidate)
    assert result.disposition == "ACCEPT"
    assert result.dimensions["conversion_fidelity"].evidence[-1].details["semantic_fidelity_established"] is False


@pytest.mark.parametrize("source,candidate", [
    ("Do not ship.", "Do ship."),
    ("Destination: west warehouse.", "Destination: [PLACEHOLDER]."),
    ("Subtotal: $900\nAmount due: $990", "Subtotal: $990\nAmount due: $900"),
    ("Ready. Destination: west.", "Ready."),
    ("Ready.", "Ready. Approved."),
    ("Let's eat, Grandma!", "Let's eat Grandma!"),
    ("The answer is no.", "The answer is No."),
    ("Keep this line.\nKeep this line.", "Keep this line."),
    ("Tax: $90", "# Invoice\nTax: $90"),
    ("First approve the order.\nThen ship the parcel.", "Then ship the parcel.\nFirst approve the order."),
    ("Amount: $900\nAmount: $990", "Amount: $990\nAmount: $900"),
    ("Amount: $900\namount: $990", "amount: $990\nAmount: $900"),
    ("Subtotal\n900\nTax\n90", "Subtotal\n90\nTax\n900"),
    ("| Label | Value |\n| Subtotal | 900 |\n| Tax | 90 |",
     "| Label | Value |\n| Tax | 90 |\n| Subtotal | 900 |"),
    ("Step one: authorize: yes\nStep two: ship: yes", "Step two: ship: yes\nStep one: authorize: yes"),
    ("Owner: Alice\nBob: Manager", "Owner: Alice Bob:\nManager"),
    ("Owner: Alice\nBob: Manager", "Owner: Alice Bob: Manager"),
    ("| Owner | Alice |\n| Bob | Manager |", "| Owner | Alice | | Bob | Manager |"),
    ("if ready:\n    ship()\nnotify()", "if ready:\n    ship()\n    notify()"),
    ("```python\nif ready:\n    ship()\n```", "```python\nif ready:\nship()\n```"),
    ("    ship()", "ship()"),
    ("- Owner Alice\n- Bob Manager", "- Owner Alice - Bob Manager"),
])
def test_unproven_changes_abstain(source, candidate):
    result = _assess(source, candidate)
    assert result.disposition == "ABSTAIN"
    assert result.dimensions["conversion_fidelity"].findings[-1].code == "SOURCE_TEXT_PRESERVATION_UNPROVEN"


def test_existing_numeric_failure_still_holds():
    assert _assess("Balance: 12", "Balance: 312").disposition == "HOLD"


def test_html_is_not_proved_from_stripped_text():
    result = Assessor().assess(source=_artifact(SourceArtifact, "<p>Ready.</p>", "text/html"),
                              candidate=_artifact(CandidateArtifact, "Ready."),
                              policy_id=SOURCE_TEXT_PRESERVATION_POLICY_ID)
    assert result.disposition == "ABSTAIN"


def test_unknown_policy_rejected():
    with pytest.raises(ValueError, match="Unknown assessment policy"):
        Assessor().assess(candidate=_artifact(CandidateArtifact, "Ready."), policy_id="typo")
