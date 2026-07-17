"""Question set for Benchmark C text-only QA pilot.

All answers are verified against baseline_b_document.md for each document.
This module is pure data — no aksharamd imports.
"""

from __future__ import annotations

KNOWN_SHARED_FAILURES: dict[str, dict] = {
    "blackrock-q03": {
        "failure_category": "shared_parsing_header_ambiguity",
        "representation_specific": False,
        "description": (
            "Truncated table column headers in the parsed PDF make it impossible to "
            "distinguish fund names across columns. All three representations fail "
            "identically. This is a parser-level quality issue, not a payload "
            "representation issue."
        ),
    },
}

PILOT_DOCUMENT_IDS: list[str] = [
    "pb-text-topofthenews",
    "pb-text-legalref",
    "pb-chart-egov-p62",
    "pub-pdf-multicolumn",
    "pb-table-blackrock",
    "pb-table-fblb-p10",
    "syn-docx-01",
    "syn-xlsx-01",
    "syn-xlsx-02",
]

# ---------------------------------------------------------------------------
# Questions as plain dicts — loaded into QuestionRecord objects by runner.py
# ---------------------------------------------------------------------------

PILOT_QUESTIONS: list[dict] = [
    # ------------------------------------------------------------------
    # pb-text-topofthenews (prose, Deepavali + SilverCare Hub article)
    # ------------------------------------------------------------------
    {
        "question_id": "topofthenews-q01",
        "document_id": "pb-text-topofthenews",
        "question": "On which date does the Hindu Festival of Lights (Deepavali) fall according to the article?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["Nov 6"],
            "answer_type": "exact",
            "grading_method": "deterministic",
            "notes": "Exact date as it appears in the document.",
        },
    },
    {
        "question_id": "topofthenews-q02",
        "document_id": "pb-text-topofthenews",
        "question": "What are the Indian celestial swans featured in the Deepavali light-up called?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["annapatchi"],
            "answer_type": "exact",
            "grading_method": "deterministic",
            "notes": "Exact term used in the article.",
        },
    },
    {
        "question_id": "topofthenews-q03",
        "document_id": "pb-text-topofthenews",
        "question": "What is the name of the new hub that was officially opened to meet seniors' health and social needs in the east?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["Our SilverCare Hub"],
            "answer_type": "exact",
            "grading_method": "deterministic",
            "notes": "Full name as used in the article.",
        },
    },
    {
        "question_id": "topofthenews-q04",
        "document_id": "pb-text-topofthenews",
        "question": "How many medical conditions will GPNext initially cover?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["14"],
            "answer_type": "normalized",
            "grading_method": "deterministic",
            "notes": "Numeric value from the article.",
        },
    },
    {
        "question_id": "topofthenews-q05",
        "document_id": "pb-text-topofthenews",
        "question": "What hospital can seniors in the east be referred to for complex or specialised care?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["Changi General Hospital", "CGH"],
            "answer_type": "exact",
            "grading_method": "deterministic",
            "notes": "Both full name and abbreviation accepted.",
        },
    },
    # ------------------------------------------------------------------
    # pb-text-legalref (Portuguese legal gazette)
    # ------------------------------------------------------------------
    {
        "question_id": "legalref-q01",
        "document_id": "pb-text-legalref",
        "question": "Qual é o número MAMP de João Carlos Veras?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["1258"],
            "answer_type": "exact",
            "grading_method": "deterministic",
            "notes": "MAMP identifier for João Carlos Veras.",
        },
    },
    {
        "question_id": "legalref-q02",
        "document_id": "pb-text-legalref",
        "question": "Qual é a classe do MP-77 atribuído a João Carlos Veras?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["classe B"],
            "answer_type": "exact",
            "grading_method": "deterministic",
            "notes": "Class designation in Portuguese as it appears.",
        },
    },
    {
        "question_id": "legalref-q03",
        "document_id": "pb-text-legalref",
        "question": "Qual é o número MAMP de Simone de Cássia Coelho?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["2041"],
            "answer_type": "exact",
            "grading_method": "deterministic",
            "notes": "MAMP identifier for Simone de Cássia Coelho.",
        },
    },
    {
        "question_id": "legalref-q04",
        "document_id": "pb-text-legalref",
        "question": "Qual é a data do Diário Eletrônico do MPMG mencionada no documento?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["21/07/2012"],
            "answer_type": "exact",
            "grading_method": "deterministic",
            "notes": "Date as it appears in the footer section.",
        },
    },
    {
        "question_id": "legalref-q05",
        "document_id": "pb-text-legalref",
        "question": "Qual é o MP e classe de Sandro Luiz Venuto (MAMP 1394)?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["MP-60 classe C"],
            "answer_type": "exact",
            "grading_method": "deterministic",
            "notes": "MP designation and class for Sandro Luiz Venuto.",
        },
    },
    # ------------------------------------------------------------------
    # pb-chart-egov-p62 (UN e-government statistics)
    # ------------------------------------------------------------------
    {
        "question_id": "egovp62-q01",
        "document_id": "pb-chart-egov-p62",
        "question": "What percentage of the 193 countries assessed have very high EGDI values (above 0.75) in 2024?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["39 per cent", "39%"],
            "answer_type": "normalized",
            "grading_method": "deterministic",
            "notes": "Percentage stated in prose.",
        },
    },
    {
        "question_id": "egovp62-q02",
        "document_id": "pb-chart-egov-p62",
        "question": "How many countries had very high EGDI values in 2014?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["25"],
            "answer_type": "normalized",
            "grading_method": "deterministic",
            "notes": "Count value from the prose.",
        },
    },
    {
        "question_id": "egovp62-q03",
        "document_id": "pb-chart-egov-p62",
        "question": "Which region has the highest average EGDI value in 2024?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["Europe"],
            "answer_type": "exact",
            "grading_method": "deterministic",
            "notes": "Region named in the prose.",
        },
    },
    {
        "question_id": "egovp62-q04",
        "document_id": "pb-chart-egov-p62",
        "question": "What is the average EGDI value for Europe in 2024?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["0.8493"],
            "answer_type": "normalized",
            "grading_method": "deterministic",
            "notes": "Numeric value from the prose.",
        },
    },
    {
        "question_id": "egovp62-q05",
        "document_id": "pb-chart-egov-p62",
        "question": "Which region saw the sharpest increase in average EGDI value since 2022?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["Asia"],
            "answer_type": "exact",
            "grading_method": "deterministic",
            "notes": "Region named in the prose.",
        },
    },
    # ------------------------------------------------------------------
    # pub-pdf-multicolumn (academic, Lorem Ipsum + EU countries table)
    # ------------------------------------------------------------------
    {
        "question_id": "multicolumn-q01",
        "document_id": "pub-pdf-multicolumn",
        "question": "What is the title of the document?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": [
                "Two-Column",
                "Document with Lorem Ipsum",
                "Two-Column Document with Lorem Ipsum",
            ],
            "answer_type": "semantic",
            "grading_method": "deterministic",
            "notes": "Either heading or full title accepted.",
        },
    },
    {
        "question_id": "multicolumn-q02",
        "document_id": "pub-pdf-multicolumn",
        "question": "What date appears on the document?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["January 3, 2024"],
            "answer_type": "exact",
            "grading_method": "deterministic",
            "notes": "Date from the document header.",
        },
    },
    {
        "question_id": "multicolumn-q03",
        "document_id": "pub-pdf-multicolumn",
        "question": "What is the population of Austria in the EU Countries table?",
        "question_type": "table_lookup",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["8.9"],
            "answer_type": "normalized",
            "grading_method": "deterministic",
            "notes": "Population in millions from the EU Countries table.",
        },
    },
    {
        "question_id": "multicolumn-q04",
        "document_id": "pub-pdf-multicolumn",
        "question": "What is the capital of Finland according to the EU Countries table?",
        "question_type": "table_lookup",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["Helsinki"],
            "answer_type": "exact",
            "grading_method": "deterministic",
            "notes": "Capital city from the EU Countries table.",
        },
    },
    {
        "question_id": "multicolumn-q05",
        "document_id": "pub-pdf-multicolumn",
        "question": "What is the area of Denmark (in km2) according to the EU Countries table?",
        "question_type": "table_lookup",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["42,951", "42951"],
            "answer_type": "normalized",
            "grading_method": "deterministic",
            "notes": "Area in km2 from the EU Countries table.",
        },
    },
    # ------------------------------------------------------------------
    # pb-table-blackrock (financial, truncated row labels)
    # ------------------------------------------------------------------
    {
        "question_id": "blackrock-q01",
        "document_id": "pb-table-blackrock",
        "question": "What is the total net assets value for BlackRock Systematic Asia Pacific Equity Absolute Return Fund?",
        "question_type": "table_lookup",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["148,177,357", "148177357"],
            "answer_type": "normalized",
            "grading_method": "deterministic",
            "notes": "talnetassets row, first fund column (USD).",
        },
    },
    {
        "question_id": "blackrock-q02",
        "document_id": "pb-table-blackrock",
        "question": "What is the total net assets value for BlackRock Systematic ESG World Equity Fund?",
        "question_type": "table_lookup",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["1,176,345,394", "1176345394"],
            "answer_type": "normalized",
            "grading_method": "deterministic",
            "notes": "talnetassets row, second fund column (USD).",
        },
    },
    {
        "question_id": "blackrock-q03",
        "document_id": "pb-table-blackrock",
        "question": "What is the total net assets value for the BlackRock Systematic Global Equity Absolute Return Fund?",
        "question_type": "table_lookup",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["21,876,893", "21876893"],
            "answer_type": "normalized",
            "grading_method": "deterministic",
            "notes": "talnetassets row, third fund column (USD).",
        },
    },
    {
        "question_id": "blackrock-q04",
        "document_id": "pb-table-blackrock",
        "question": "What is the cash at bank value for BlackRock Systematic ESG World Equity Fund?",
        "question_type": "table_lookup",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["13,211,976", "13211976"],
            "answer_type": "normalized",
            "grading_method": "deterministic",
            "notes": "ashatbank row, ESG World Equity Fund column (USD).",
        },
    },
    {
        "question_id": "blackrock-q05",
        "document_id": "pb-table-blackrock",
        "question": "What is the total liabilities value for the BlackRock Systematic Asia Pacific Equity Absolute Return Fund?",
        "question_type": "table_lookup",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["42,130,377", "42130377"],
            "answer_type": "normalized",
            "grading_method": "deterministic",
            "notes": "talliabilities row, first fund column (USD).",
        },
    },
    # ------------------------------------------------------------------
    # pb-table-fblb-p10 (insurance rate filing)
    # ------------------------------------------------------------------
    {
        "question_id": "fblbp10-q01",
        "document_id": "pb-table-fblb-p10",
        "question": "What is the SERFF Tracking Number for this filing?",
        "question_type": "table_lookup",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["FBLB-134215544"],
            "answer_type": "exact",
            "grading_method": "deterministic",
            "notes": "SERFF tracking number from the filing header table.",
        },
    },
    {
        "question_id": "fblbp10-q02",
        "document_id": "pb-table-fblb-p10",
        "question": "What is the Overall Percentage of Last Rate Revision?",
        "question_type": "table_lookup",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["8.400%", "8.4%", "8.400"],
            "answer_type": "normalized",
            "grading_method": "deterministic",
            "notes": "Overall percentage of last rate revision.",
        },
    },
    {
        "question_id": "fblbp10-q03",
        "document_id": "pb-table-fblb-p10",
        "question": "What is the Overall Rate Impact for Farm Bureau Property & Casualty Insurance Company?",
        "question_type": "table_lookup",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["4.400%", "4.4%", "4.400"],
            "answer_type": "normalized",
            "grading_method": "deterministic",
            "notes": "Overall % Rate Impact column for Farm Bureau Property & Casualty.",
        },
    },
    {
        "question_id": "fblbp10-q04",
        "document_id": "pb-table-fblb-p10",
        "question": "How many policyholders are affected by this program for Farm Bureau Property & Casualty Insurance Company?",
        "question_type": "table_lookup",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["25,392", "25392"],
            "answer_type": "normalized",
            "grading_method": "deterministic",
            "notes": "Number of Policy Holders Affected for Farm Bureau.",
        },
    },
    {
        "question_id": "fblbp10-q05",
        "document_id": "pb-table-fblb-p10",
        "question": "What is the Written Premium for this Program for Western Agricultural Insurance Company?",
        "question_type": "table_lookup",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["$27,091,793", "27,091,793", "27091793"],
            "answer_type": "normalized",
            "grading_method": "deterministic",
            "notes": (
                "CORRECTED 2026-07-14: previous accepted_answers "
                '[\"$1,073,338\", \"1,073,338\", \"1073338\"] corresponded to the '
                '"Written Premium Change for this Program" column. The correct value '
                'for "Written Premium for this Program" for Western Agricultural '
                "Insurance Company is $27,091,793. Supporting table: pb-table-fblb-p10 "
                'Rate Information table, row "Western Agricultural Insurance Company", '
                'column "Written Premium for this Program".'
            ),
        },
    },
    # ------------------------------------------------------------------
    # syn-docx-01 (synthetic DOCX)
    # ------------------------------------------------------------------
    {
        "question_id": "synDocx01-q01",
        "document_id": "syn-docx-01",
        "question": "What is the title of the document?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["AksharaMD Benchmark Document"],
            "answer_type": "exact",
            "grading_method": "deterministic",
            "notes": "Title heading from the document.",
        },
    },
    {
        "question_id": "synDocx01-q02",
        "document_id": "syn-docx-01",
        "question": "What are the two main section headings in the document?",
        "question_type": "text_retrieval",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": [
                "Section 1: Introduction",
                "Section 2: Data Table",
            ],
            "answer_type": "semantic",
            "grading_method": "deterministic",
            "notes": "Both section names. Semantic because model may list them together.",
        },
    },
    {
        "question_id": "synDocx01-q03",
        "document_id": "syn-docx-01",
        "question": "What is the Precision value in the Data Table?",
        "question_type": "table_lookup",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["0.95"],
            "answer_type": "normalized",
            "grading_method": "deterministic",
            "notes": "Precision row, Value column.",
        },
    },
    {
        "question_id": "synDocx01-q04",
        "document_id": "syn-docx-01",
        "question": "What is the Recall value in the Data Table?",
        "question_type": "table_lookup",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["0.88"],
            "answer_type": "normalized",
            "grading_method": "deterministic",
            "notes": "Recall row, Value column.",
        },
    },
    {
        "question_id": "synDocx01-q05",
        "document_id": "syn-docx-01",
        "question": "What is the unit for the Precision row in the Data Table?",
        "question_type": "table_lookup",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["ratio"],
            "answer_type": "exact",
            "grading_method": "deterministic",
            "notes": "Unit column for Precision row.",
        },
    },
    # ------------------------------------------------------------------
    # syn-xlsx-01 (XLSX Benchmark table)
    # ------------------------------------------------------------------
    {
        "question_id": "synXlsx01-q01",
        "document_id": "syn-xlsx-01",
        "question": "What is the Score for sample-001 in the Benchmark table?",
        "question_type": "table_lookup",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["0.91"],
            "answer_type": "normalized",
            "grading_method": "deterministic",
            "notes": "Score column for sample-001.",
        },
    },
    {
        "question_id": "synXlsx01-q02",
        "document_id": "syn-xlsx-01",
        "question": "What format is sample-002 in?",
        "question_type": "table_lookup",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["DOCX"],
            "answer_type": "exact",
            "grading_method": "deterministic",
            "notes": "Format column for sample-002.",
        },
    },
    {
        "question_id": "synXlsx01-q03",
        "document_id": "syn-xlsx-01",
        "question": "How many pages does sample-003 have?",
        "question_type": "table_lookup",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["1"],
            "answer_type": "normalized",
            "grading_method": "deterministic",
            "notes": "Pages column for sample-003.",
        },
    },
    {
        "question_id": "synXlsx01-q04",
        "document_id": "syn-xlsx-01",
        "question": "How many blocks does sample-004 have?",
        "question_type": "table_lookup",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["3"],
            "answer_type": "normalized",
            "grading_method": "deterministic",
            "notes": "Blocks column for sample-004.",
        },
    },
    {
        "question_id": "synXlsx01-q05",
        "document_id": "syn-xlsx-01",
        "question": "What is the Mean Score in the Summary table?",
        "question_type": "table_lookup",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["0.8775"],
            "answer_type": "normalized",
            "grading_method": "deterministic",
            "notes": "Mean Score row in the Summary table.",
        },
    },
    # ------------------------------------------------------------------
    # syn-xlsx-02 (XLSX with Summary, PDF Results, Synthetic Results)
    # ------------------------------------------------------------------
    {
        "question_id": "synXlsx02-q01",
        "document_id": "syn-xlsx-02",
        "question": "What is the Total value in the Summary table?",
        "question_type": "table_lookup",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["10"],
            "answer_type": "normalized",
            "grading_method": "deterministic",
            "notes": "Total row, Value column in Summary table.",
        },
    },
    {
        "question_id": "synXlsx02-q02",
        "document_id": "syn-xlsx-02",
        "question": "What is the Success value in the Summary table?",
        "question_type": "table_lookup",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["9"],
            "answer_type": "normalized",
            "grading_method": "deterministic",
            "notes": "Success row, Value column in Summary table.",
        },
    },
    {
        "question_id": "synXlsx02-q03",
        "document_id": "syn-xlsx-02",
        "question": "In the PDF Results table, how many pages does pdf-001 have?",
        "question_type": "table_lookup",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["1"],
            "answer_type": "normalized",
            "grading_method": "deterministic",
            "notes": "Pages column for pdf-001 in PDF Results.",
        },
    },
    {
        "question_id": "synXlsx02-q04",
        "document_id": "syn-xlsx-02",
        "question": "What is the Elapsed time for pdf-001 in the PDF Results table?",
        "question_type": "table_lookup",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["0.12"],
            "answer_type": "normalized",
            "grading_method": "deterministic",
            "notes": "Elapsed column for pdf-001 in PDF Results.",
        },
    },
    {
        "question_id": "synXlsx02-q05",
        "document_id": "syn-xlsx-02",
        "question": "In the Synthetic Results table, what format is syn-001?",
        "question_type": "table_lookup",
        "requires_visual": False,
        "answer_key": {
            "accepted_answers": ["docx"],
            "answer_type": "exact",
            "grading_method": "deterministic",
            "notes": "Format column for syn-001 in Synthetic Results.",
        },
    },
]
