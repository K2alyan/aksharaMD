"""Question set for Benchmark C text-only QA pilot — held-out evaluation.

All answers are verified against baseline_b_document.md for each document.
This module is pure data — no aksharamd imports.
"""

from __future__ import annotations

HELD_OUT_DOCUMENT_IDS: list[str] = [
    "pb-ho-text-livingword",
    "pb-ho-text-cn-article",
    "pb-ho-table-axa-urd",
    "pb-ho-chart-egov-p170",
    "syn-ho-docx-07",
    "syn-ho-xlsx-03",
]

# ---------------------------------------------------------------------------
# Questions as plain dicts — loaded into QuestionRecord objects by runner.py
# ---------------------------------------------------------------------------

HELD_OUT_QUESTIONS: list[dict] = [
    # ------------------------------------------------------------------
    # pb-ho-text-livingword (church bulletin / parish newsletter)
    # ------------------------------------------------------------------
    {
        "question_id": "ho-livingword-q01",
        "document_id": "pb-ho-text-livingword",
        "question": "What is the name of the parish priest listed in the Parish Contacts section?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["Fr Damian McGrath"],
            "answer_type": "exact",
            "grading_method": "deterministic",
            "notes": "Parish Priest name from the Parish Contacts section.",
        },
    },
    {
        "question_id": "ho-livingword-q02",
        "document_id": "pb-ho-text-livingword",
        "question": "What is the email address listed for the parish office?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["parishoffice@inghamcatholic.com"],
            "answer_type": "exact",
            "grading_method": "deterministic",
            "notes": "Parish office email address.",
        },
    },
    {
        "question_id": "ho-livingword-q03",
        "document_id": "pb-ho-text-livingword",
        "question": "According to the Principal's Pen section, what position did out of 48 teams did St Teresa's College First XIII finish at the Confraternity Shield Rugby League Carnival?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["11th"],
            "answer_type": "exact",
            "grading_method": "deterministic",
            "notes": "Position from the Confraternity Shield result mentioned in Principal's Pen.",
        },
    },
    {
        "question_id": "ho-livingword-q04",
        "document_id": "pb-ho-text-livingword",
        "question": "What time is Sunday Mass at St Teresa's College, Abergowrie?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["8.30am", "Sun 8.30am", "8.30"],
            "answer_type": "exact",
            "grading_method": "deterministic",
            "notes": (
                "Sunday mass time at ST TERESA'S COLLEGE specifically (8.30am). "
                "Document lists two entries: 'St Teresa's College, Abergowrie → Sun 8.30am' "
                "and 'Abergowrie → Sun 10.00am'. The question asks about St Teresa's College. "
                "CORRECTION ec-ho-001 (2026-07-14): old accepted was ['10.00am','10am'], "
                "which refers to the plain Abergowrie entry, not the college."
            ),
        },
    },
    {
        "question_id": "ho-livingword-q05",
        "document_id": "pb-ho-text-livingword",
        "question": "What scripture passage from James is cited in the document?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["James 3:16 – 4:3", "James 3:16", "James 3"],
            "answer_type": "exact",
            "grading_method": "deterministic",
            "notes": "The James passage reference appearing in bold in the text.",
        },
    },
    # ------------------------------------------------------------------
    # pb-ho-text-cn-article (Chinese newspaper page, 2012-07-21)
    # ------------------------------------------------------------------
    {
        "question_id": "ho-cnarticle-q01",
        "document_id": "pb-ho-text-cn-article",
        "question": "What is the date printed at the top of the newspaper page?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["2012年7月21日", "7月21日", "2012-07-21"],
            "answer_type": "exact",
            "grading_method": "deterministic",
            "notes": "Date line at the top of the document.",
        },
    },
    {
        "question_id": "ho-cnarticle-q02",
        "document_id": "pb-ho-text-cn-article",
        "question": "According to the article, how many patient visits did Beijing Children's Hospital receive on the day reported?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["10806", "10,806"],
            "answer_type": "normalized",
            "grading_method": "deterministic",
            "notes": "Daily patient visit count for Beijing Children's Hospital from the medical news article.",
        },
    },
    {
        "question_id": "ho-cnarticle-q03",
        "document_id": "pb-ho-text-cn-article",
        "question": "According to the Tongren Hospital measures listed, how many additional refractive (屈光) appointment numbers per day does the West Zone add?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["100"],
            "answer_type": "normalized",
            "grading_method": "deterministic",
            "notes": "Number of additional refractive appointments added daily at the West Zone.",
        },
    },
    {
        "question_id": "ho-cnarticle-q04",
        "document_id": "pb-ho-text-cn-article",
        "question": "What is the total area in square metres of the Lianshi Road greening project mentioned in the article?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["59600", "59,600"],
            "answer_type": "normalized",
            "grading_method": "deterministic",
            "notes": "Total area of the Lianshi Road greening project.",
        },
    },
    {
        "question_id": "ho-cnarticle-q05",
        "document_id": "pb-ho-text-cn-article",
        "question": "How many yuan per year is the fund set aside for young teachers' social practice work starting from the reported year?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["1000万元", "1000万", "1千万元"],
            "answer_type": "exact",
            "grading_method": "deterministic",
            "notes": "Annual fund amount for young teachers' social practice (1000万元).",
        },
    },
    # ------------------------------------------------------------------
    # pb-ho-table-axa-urd (AXA Group Universal Registration Document 2024)
    # ------------------------------------------------------------------
    {
        "question_id": "ho-axaurd-q01",
        "document_id": "pb-ho-table-axa-urd",
        "question": "What is the section number and title that appears as the main heading of this document?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["1.5 Ratings", "1.5"],
            "answer_type": "exact",
            "grading_method": "deterministic",
            "notes": "Section heading at the top of the document.",
        },
    },
    {
        "question_id": "ho-axaurd-q02",
        "document_id": "pb-ho-table-axa-urd",
        "question": "What is the section number and title of the subsection about insurer financial strength ratings?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": [
                "1.5.1 Insurer financial strength and counterparty credit ratings",
                "1.5.1",
            ],
            "answer_type": "exact",
            "grading_method": "deterministic",
            "notes": "Subsection heading for the ratings table.",
        },
    },
    {
        "question_id": "ho-axaurd-q03",
        "document_id": "pb-ho-table-axa-urd",
        "question": "On what date did S&P Global Ratings last review AXA's ratings?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["February 25, 2025"],
            "answer_type": "exact",
            "grading_method": "deterministic",
            "notes": "Date of last review for S&P Global Ratings.",
        },
    },
    {
        "question_id": "ho-axaurd-q04",
        "document_id": "pb-ho-table-axa-urd",
        "question": "On what date did Moody's Investors Service last review AXA's ratings?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["October 18, 2024"],
            "answer_type": "exact",
            "grading_method": "deterministic",
            "notes": "Date of last review for Moody's Investors Service.",
        },
    },
    {
        "question_id": "ho-axaurd-q05",
        "document_id": "pb-ho-table-axa-urd",
        "question": "On what date did A.M. Best Rating Services last review AXA's ratings?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["October 2, 2024"],
            "answer_type": "exact",
            "grading_method": "deterministic",
            "notes": "Date of last review for A.M. Best Rating Services.",
        },
    },
    # ------------------------------------------------------------------
    # pb-ho-chart-egov-p170 (UN e-government, city portals)
    # ------------------------------------------------------------------
    {
        "question_id": "ho-egovp170-q01",
        "document_id": "pb-ho-chart-egov-p170",
        "question": "What is the title of Figure 4.7?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": [
                "Implementation of content provision indicators in city portals: sectoral information",
            ],
            "answer_type": "semantic",
            "grading_method": "deterministic",
            "notes": "Caption/title of Figure 4.7.",
        },
    },
    {
        "question_id": "ho-egovp170-q02",
        "document_id": "pb-ho-chart-egov-p170",
        "question": "What is the title of Figure 4.8?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": [
                "Implementation of content provision indicators in city portals: addressing everyday needs",
            ],
            "answer_type": "semantic",
            "grading_method": "deterministic",
            "notes": "Caption/title of Figure 4.8.",
        },
    },
    {
        "question_id": "ho-egovp170-q03",
        "document_id": "pb-ho-chart-egov-p170",
        "question": "What percentage of city portals provide municipality budget information (the higher of the two values shown)?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["58%", "58 per cent"],
            "answer_type": "normalized",
            "grading_method": "deterministic",
            "notes": "Municipality budget information availability; document states 'just under 58 per cent'.",
        },
    },
    {
        "question_id": "ho-egovp170-q04",
        "document_id": "pb-ho-chart-egov-p170",
        "question": "What chapter number does the text reference for Local E-Government Development?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["Chapter 4", "4"],
            "answer_type": "exact",
            "grading_method": "deterministic",
            "notes": "Chapter reference appearing in the document.",
        },
    },
    {
        "question_id": "ho-egovp170-q05",
        "document_id": "pb-ho-chart-egov-p170",
        "question": "What country's portal is described as offering resources such as a 'Neighbourly Welcome Guide' for new residents?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["Singapore"],
            "answer_type": "exact",
            "grading_method": "deterministic",
            "notes": "Country named in the prose description of immigrant support portal features.",
        },
    },
    # ------------------------------------------------------------------
    # syn-ho-docx-07 (synthetic DOCX metadata fixture, 4-line document)
    # ------------------------------------------------------------------
    {
        "question_id": "ho-docx07-q01",
        "document_id": "syn-ho-docx-07",
        "question": "What is the title of the document?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["Metadata Fixture"],
            "answer_type": "exact",
            "grading_method": "deterministic",
            "notes": "H1 heading that is the document title.",
        },
    },
    {
        "question_id": "ho-docx07-q02",
        "document_id": "syn-ho-docx-07",
        "question": "What document properties are stated as being set in this fixture?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["author, title, subject, keywords"],
            "answer_type": "semantic",
            "grading_method": "deterministic",
            "notes": "Core properties listed in the document body.",
        },
    },
    {
        "question_id": "ho-docx07-q03",
        "document_id": "syn-ho-docx-07",
        "question": "What is stated as being tested alongside content extraction in this document?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["Metadata extraction", "metadata extraction"],
            "answer_type": "exact",
            "grading_method": "deterministic",
            "notes": "The phrase 'Metadata extraction is tested alongside content extraction.'",
        },
    },
    {
        "question_id": "ho-docx07-q04",
        "document_id": "syn-ho-docx-07",
        "question": "What type of document properties does the fixture set — core or extended?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["core", "core properties"],
            "answer_type": "exact",
            "grading_method": "deterministic",
            "notes": "The document states 'core properties' explicitly.",
        },
    },
    {
        "question_id": "ho-docx07-q05",
        "document_id": "syn-ho-docx-07",
        "question": "What is the purpose of this document as stated in its body?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": [
                "Metadata extraction is tested alongside content extraction",
                "metadata extraction",
            ],
            "answer_type": "semantic",
            "grading_method": "deterministic",
            "notes": "Purpose stated in the second paragraph of the document.",
        },
    },
    # ------------------------------------------------------------------
    # syn-ho-xlsx-03 (XLSX sparse benchmark table)
    # ------------------------------------------------------------------
    {
        "question_id": "ho-xlsx03-q01",
        "document_id": "syn-ho-xlsx-03",
        "question": "What is the heading (section title) of the table in this document?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["Benchmark"],
            "answer_type": "exact",
            "grading_method": "deterministic",
            "notes": "The section/sheet heading above the table.",
        },
    },
    {
        "question_id": "ho-xlsx03-q02",
        "document_id": "syn-ho-xlsx-03",
        "question": "What are the four column headers of the Benchmark table?",
        "question_type": "table_lookup",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["A, B, C, D", "A B C D"],
            "answer_type": "semantic",
            "grading_method": "deterministic",
            "notes": "All four column header labels from the Benchmark table.",
        },
    },
    {
        "question_id": "ho-xlsx03-q03",
        "document_id": "syn-ho-xlsx-03",
        "question": "In the Benchmark table, what value appears in column A of the first data row?",
        "question_type": "table_lookup",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["x"],
            "answer_type": "exact",
            "grading_method": "deterministic",
            "notes": "Column A, first data row value.",
        },
    },
    {
        "question_id": "ho-xlsx03-q04",
        "document_id": "syn-ho-xlsx-03",
        "question": "In the Benchmark table, what value appears in column D of the second data row?",
        "question_type": "table_lookup",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["w"],
            "answer_type": "exact",
            "grading_method": "deterministic",
            "notes": "Column D, second data row value.",
        },
    },
    {
        "question_id": "ho-xlsx03-q05",
        "document_id": "syn-ho-xlsx-03",
        "question": "In the Benchmark table, what value appears in column B of the third data row?",
        "question_type": "table_lookup",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["b"],
            "answer_type": "exact",
            "grading_method": "deterministic",
            "notes": "Column B, third data row value.",
        },
    },
]
