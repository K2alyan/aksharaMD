"""Fixed answering prompt for QA Pilot v1.0."""

PROMPT_VERSION = "v1.0"

SYSTEM_PROMPT = """You are a precise information retrieval assistant. Your task is to answer questions based solely on the provided document text.

Rules:
- Answer only from the document text provided.
- If the answer is not present in the document, respond with exactly: "NOT FOUND"
- For numeric answers, include units if stated in the document.
- Keep answers concise — prefer exact phrases from the document over paraphrases.
- Do not add explanations, caveats, or surrounding context."""


def build_user_message(document_text: str, question: str) -> str:
    """Build the user message for the answering prompt."""
    return f"""Document:
---
{document_text}
---

Question: {question}

Answer:"""
