# Benchmark Scoring Prompts

These are the exact prompts used in the AksharaMD LLM QA benchmark. Both prompts are implemented in `benchmarks/llm_qa_eval.py`.

---

## Answer prompt

Used to elicit a factual answer from each LLM, given each tool's extracted document text as context.

**Model:** Claude Haiku 4.5 (primary); also Gemini 2.5 Flash and GPT-4o mini for validation runs.  
**Max tokens:** 128

```
Answer the question below using ONLY the document text provided. Be concise — give just the answer value.

Question: {question}

Document:
{context}
```

`{context}` is the first 6,000 characters of the tool's output for that document (~4,000 tokens with the cl100k_base tokenizer).

---

## Judge prompt

Used to score each answer against the expected answer. The judge is separate from the answering model so that model-specific biases do not conflate extraction quality with generation quality.

**Model:** Claude Haiku 4.5 (all runs).  
**Max tokens:** 8 (single integer output)

```
You are an objective answer quality judge.

Question:         {question}
Expected answer:  {expected}
Answer to score:  {answer}

Score the answer from 0 to 10 for correctness and completeness. 10 = fully correct. 0 = wrong or irrelevant. Be strict.
Reply with a single integer only.
```

---

## Q&A generation prompt

Used to generate factual Q&A pairs for each document in the corpus. Q&A pairs are pre-generated and stored in `benchmarks/eval_corpus_qa.yaml` so that all tools answer identical questions.

**Model:** Claude Haiku 4.5.  
**Max tokens:** 600

The generation prompt is in `benchmarks/llm_qa_eval.py` at the `_generate_qa` function.

---

## Scoring methodology notes

- Each answer is scored independently. The judge does not see scores for other answers.
- Answers that start with `[` (error markers from the extraction tools) are scored 0 without calling the judge.
- The judge score is clamped to [0, 10] regardless of what the model returns.
- Per-format averages are computed over all documents of that format where the tool produced output and the document had expected answers.
- Tools that produced no output for a format are excluded from that format's average (marked `—` in results tables).
- LlamaParse JSON and IPYNB scores of 0.9 and 1.1 reflect near-empty extraction, not judge leniency.
