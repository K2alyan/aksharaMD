"""KV QA pilot question definitions."""
from __future__ import annotations
from pydantic import BaseModel
from typing import Literal

class KVQuestion(BaseModel):
    question_id: str
    question: str
    question_type: Literal[
        "direct_field_lookup",
        "record_specific_lookup",
        "multi_record_differentiation",
        "metadata_lookup",
        "specification_lookup",
        "negative_control",
    ]
    document_fixture: str      # case_id from corpus
    accepted_answers: list[str]
    representation_dependency: str   # which field makes this answerable: e.g. "Time"
    record_number: int | None = None  # which record (0-indexed) for multi-record
    notes: str = ""

KV_QA_QUESTIONS: list[KVQuestion] = [
    # Abergowrie regression — locked
    KVQuestion(
        question_id="kv-q01",
        question="What time is the Sunday service at Abergowrie?",
        question_type="record_specific_lookup",
        document_fixture="abergowrie_schedule",
        accepted_answers=["9:00 AM", "9:00am", "9 AM"],
        representation_dependency="Time",
        record_number=1,
        notes="Locked regression case — must not conflate Saturday 7:00 PM with Sunday 9:00 AM",
    ),
    KVQuestion(
        question_id="kv-q02",
        question="What time is the Saturday service at Abergowrie?",
        question_type="record_specific_lookup",
        document_fixture="abergowrie_schedule",
        accepted_answers=["7:00 PM", "7:00pm", "7 PM"],
        representation_dependency="Time",
        record_number=0,
    ),
    KVQuestion(
        question_id="kv-q03",
        question="Which service takes place on Saturday?",
        question_type="multi_record_differentiation",
        document_fixture="abergowrie_schedule",
        accepted_answers=["Abergowrie", "Saturday service"],
        representation_dependency="Day",
        record_number=0,
    ),
    KVQuestion(
        question_id="kv-q04",
        question="What is the contact email?",
        question_type="direct_field_lookup",
        document_fixture="contact_card_01",
        accepted_answers=["alice@example.com"],
        representation_dependency="Email",
    ),
    KVQuestion(
        question_id="kv-q05",
        question="What is the document author?",
        question_type="metadata_lookup",
        document_fixture="docx_properties_01",
        accepted_answers=["Alice Smith", "Alice"],
        representation_dependency="Author",
    ),
    KVQuestion(
        question_id="kv-q06",
        question="What is the operating voltage?",
        question_type="specification_lookup",
        document_fixture="spec_sheet_01",
        accepted_answers=["12V", "12 V", "12 volts"],
        representation_dependency="Voltage",
    ),
    KVQuestion(
        question_id="kv-q07",
        question="What are the office hours on Monday?",
        question_type="record_specific_lookup",
        document_fixture="office_hours_01",
        accepted_answers=["9:00 AM - 5:00 PM", "9am-5pm", "9:00-17:00"],
        representation_dependency="Monday",
    ),
    KVQuestion(
        question_id="kv-q08",
        question="Who owns this project?",
        question_type="metadata_lookup",
        document_fixture="project_metadata_01",
        accepted_answers=["Kalyan", "kalyan"],
        representation_dependency="Owner",
    ),
    # Negative control — prose, should not have structured answer
    KVQuestion(
        question_id="kv-q09",
        question="What is being explained in the note?",
        question_type="negative_control",
        document_fixture="rhetorical_prose_01",
        accepted_answers=[],  # no KV structure expected
        representation_dependency="none",
        notes="Verify prose is not promoted",
    ),
    # Additional questions for broader coverage
    KVQuestion(
        question_id="kv-q10",
        question="What is the product model number?",
        question_type="specification_lookup",
        document_fixture="product_01",
        accepted_answers=["XR-500"],
        representation_dependency="Model",
    ),
    KVQuestion(
        question_id="kv-q11",
        question="What is the document version?",
        question_type="metadata_lookup",
        document_fixture="doc_metadata_01",
        accepted_answers=["2.1"],
        representation_dependency="Version",
    ),
    KVQuestion(
        question_id="kv-q12",
        question="What time is the Wednesday training session?",
        question_type="record_specific_lookup",
        document_fixture="training_schedule_01",
        accepted_answers=["6:00 AM"],
        representation_dependency="Wednesday",
    ),
    KVQuestion(
        question_id="kv-q13",
        question="What is Alice's email address?",
        question_type="record_specific_lookup",
        document_fixture="multi_contact_01",
        accepted_answers=["alice@example.com"],
        representation_dependency="Email",
        record_number=0,
    ),
    KVQuestion(
        question_id="kv-q14",
        question="What is Bob's email address?",
        question_type="record_specific_lookup",
        document_fixture="multi_contact_01",
        accepted_answers=["bob@example.com"],
        representation_dependency="Email",
        record_number=1,
        notes="Tests differentiation across two contact records",
    ),
    KVQuestion(
        question_id="kv-q15",
        question="What is the database name?",
        question_type="specification_lookup",
        document_fixture="server_config_01",
        accepted_answers=["production"],
        representation_dependency="Database",
    ),
]
