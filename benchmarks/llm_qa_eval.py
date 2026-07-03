#!/usr/bin/env python3
"""
Multi-LLM QA evaluation: measure how document conversion quality affects
downstream LLM answer accuracy across Claude, GPT-4, and Gemini.

For each document, AksharaMD, MarkItDown, Unstructured, and Docling each
produce a text representation. The same questions are then sent to every
available LLM using each representation as context. A Claude Haiku judge
scores every answer 0-10 against the expected answer.

This makes the impact of extraction quality directly measurable:
better-structured Markdown → more tokens used by the LLM on actual
content → higher answer accuracy.

Usage:
    # Use a pre-written Q&A YAML file (recommended)
    python -m benchmarks.llm_qa_eval --qa benchmarks/eval_corpus_qa.yaml

    # Conversion stats only — no LLM calls, no API keys needed
    python -m benchmarks.llm_qa_eval --qa benchmarks/eval_corpus_qa.yaml --no-llm

    # Specific tools and LLMs
    python -m benchmarks.llm_qa_eval --qa benchmarks/eval_corpus_qa.yaml \\
        --tools aksharamd markitdown unstructured docling --llms gemini

    # Auto-generate Q&A pairs from documents and evaluate
    python -m benchmarks.llm_qa_eval report.pdf contract.docx

    # Save auto-generated Q&A for reuse
    python -m benchmarks.llm_qa_eval report.pdf --save-qa benchmarks/my_qa.yaml

Q&A YAML format (see benchmarks/qa_pairs_example.yaml):
    documents:
      - path: "report.pdf"
        description: "Optional label"
        qa:
          - q: "What was the total revenue?"
            a: "42.3 million"
          - q: "Who signed the document?"
            a: "Jane Smith"
    (Omit 'a' to get answers without scoring.)

Required packages:
    pip install anthropic               # Claude (also used as judge)
    pip install openai                  # GPT-4o-mini (optional)
    pip install google-genai            # Gemini Flash (optional)
    pip install "unstructured[all-docs]"  # Unstructured (optional)
    pip install docling                 # Docling (optional)

Required env vars:
    ANTHROPIC_API_KEY
    OPENAI_API_KEY       (optional)
    GEMINI_API_KEY       (optional; GOOGLE_API_KEY also accepted)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import warnings
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import yaml

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    with open(env_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value

_load_dotenv()

RESULTS_PATH = Path("benchmark_results/llm_qa_results.json")

# Default models — chosen for speed and cost-efficiency
_CLAUDE_MODEL  = "claude-haiku-4-5-20251001"
_OPENAI_MODEL  = "gpt-4o-mini"
_GEMINI_MODEL  = "gemini-2.5-flash"
_JUDGE_MODEL   = "claude-haiku-4-5-20251001"

LLM_DISPLAY = {
    "claude": "Claude Haiku 4.5",
    "openai": "GPT-4o mini",
    "gemini": "Gemini 2.5 Flash",
}

# Approximate LLM input pricing (USD per token) — verify at vendor websites
# Claude Haiku 4.5: $0.80/1M  |  GPT-4o mini: $0.15/1M  |  Gemini 2.5 Flash: $0.10/1M
_LLM_COST_PER_TOKEN: dict[str, float] = {
    "claude": 0.80 / 1_000_000,
    "openai": 0.15 / 1_000_000,
    "gemini": 0.10 / 1_000_000,
}


# ── token counting ────────────────────────────────────────────────────────────

def _count_tokens(text: str) -> int:
    """Count tokens using tiktoken cl100k_base (GPT-4 tokenizer family)."""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text, disallowed_special=()))
    except Exception:
        return len(text.split())


# ── LLM response container ────────────────────────────────────────────────────

@dataclass
class LLMResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    error: str = ""


# ── LLM backends ──────────────────────────────────────────────────────────────

def _call_claude(prompt: str, max_tokens: int = 256,
                 model: str = _CLAUDE_MODEL) -> LLMResponse:
    try:
        import anthropic
        msg = anthropic.Anthropic().messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return LLMResponse(
            text=msg.content[0].text.strip(),
            input_tokens=msg.usage.input_tokens,
            output_tokens=msg.usage.output_tokens,
        )
    except Exception as exc:
        return LLMResponse(text="", error=str(exc))


def _call_openai(prompt: str, max_tokens: int = 256,
                 model: str = _OPENAI_MODEL) -> LLMResponse:
    try:
        import openai
        resp = openai.OpenAI().chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        usage = resp.usage
        return LLMResponse(
            text=resp.choices[0].message.content.strip(),
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
        )
    except Exception as exc:
        return LLMResponse(text="", error=str(exc))


def _call_gemini(prompt: str, max_tokens: int = 256,
                 model: str = _GEMINI_MODEL) -> LLMResponse:
    try:
        from google import genai
        from google.genai import types
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(max_output_tokens=max(max_tokens, 200)),
        )
        return LLMResponse(text=resp.text.strip() if resp.text else "")
    except Exception as exc:
        return LLMResponse(text="", error=str(exc))


_LLM_FNS: dict[str, object] = {
    "claude": _call_claude,
    "openai": _call_openai,
    "gemini": _call_gemini,
}


def _detect_available_llms() -> list[str]:
    available = []
    try:
        import anthropic  # noqa: F401
        if os.environ.get("ANTHROPIC_API_KEY"):
            available.append("claude")
    except ImportError:
        pass
    try:
        import openai  # noqa: F401
        if os.environ.get("OPENAI_API_KEY"):
            available.append("openai")
    except ImportError:
        pass
    try:
        from google import genai  # noqa: F401
        if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
            available.append("gemini")
    except ImportError:
        pass
    return available


# ── conversion runners ────────────────────────────────────────────────────────

def _convert_aksharamd(path: Path) -> tuple[str, float]:
    from aksharamd.compiler import Compiler
    t0 = time.perf_counter()
    text, _ = Compiler(output_dir="output").compile_to_string(str(path))
    return text, time.perf_counter() - t0


def _convert_markitdown(path: Path) -> tuple[str, float]:
    try:
        from markitdown import MarkItDown
        t0 = time.perf_counter()
        result = MarkItDown().convert(str(path))
        return result.text_content or "", time.perf_counter() - t0
    except Exception as exc:
        return f"[MarkItDown error: {exc}]", 0.0


# Docling lazy singleton — model load is expensive, reuse across all files
_docling_converter: object = None

def _get_docling() -> object:
    global _docling_converter
    if _docling_converter is None:
        import logging
        logging.disable(logging.WARNING)
        from docling.document_converter import DocumentConverter
        _docling_converter = DocumentConverter()
    return _docling_converter


def _convert_unstructured(path: Path) -> tuple[str, float]:
    try:
        from unstructured.partition.auto import partition
        t0 = time.perf_counter()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            els = partition(filename=str(path))
        text = "\n\n".join(str(e) for e in els)
        return text, time.perf_counter() - t0
    except Exception as exc:
        return f"[Unstructured error: {exc}]", 0.0


def _convert_docling(path: Path) -> tuple[str, float]:
    try:
        t0 = time.perf_counter()
        conv = _get_docling()
        result = conv.convert(str(path))
        text = result.document.export_to_markdown()
        return text, time.perf_counter() - t0
    except Exception as exc:
        return f"[Docling error: {exc}]", 0.0


def _convert_pymupdf4llm(path: Path) -> tuple[str, float]:
    try:
        import pymupdf4llm
        t0 = time.perf_counter()
        text = pymupdf4llm.to_markdown(str(path))
        return text, time.perf_counter() - t0
    except ImportError:
        return "[PyMuPDF4LLM error: not installed — pip install pymupdf4llm]", 0.0
    except Exception as exc:
        return f"[PyMuPDF4LLM error: {exc}]", 0.0


def _convert_llamaparse(path: Path) -> tuple[str, float]:
    try:
        from llama_parse import LlamaParse
    except ImportError:
        return "[LlamaParse error: not installed — pip install llama-parse]", 0.0
    api_key = os.environ.get("LLAMA_CLOUD_API_KEY")
    if not api_key:
        return "[LlamaParse error: LLAMA_CLOUD_API_KEY not set]", 0.0
    try:
        t0 = time.perf_counter()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            parser = LlamaParse(api_key=api_key, result_type="markdown", verbose=False)
            documents = parser.load_data(str(path))
        text = "\n\n".join(doc.text for doc in documents)
        return text, time.perf_counter() - t0
    except Exception as exc:
        return f"[LlamaParse error: {exc}]", 0.0


# Marker model singleton — loading surreal-like models once and reusing
_marker_models: object = None

def _get_marker_models() -> object:
    global _marker_models
    if _marker_models is None:
        import logging
        logging.disable(logging.WARNING)
        from marker.models import create_model_dict
        _marker_models = create_model_dict()
        logging.disable(logging.NOTSET)
    return _marker_models


def _convert_marker(path: Path) -> tuple[str, float]:
    try:
        from marker.converters.pdf import PdfConverter
    except ImportError:
        return "[Marker error: not installed — pip install marker-pdf]", 0.0
    try:
        t0 = time.perf_counter()
        models = _get_marker_models()
        converter = PdfConverter(artifact_dict=models)
        rendered = converter(str(path))
        text = rendered.markdown if hasattr(rendered, "markdown") else str(rendered)
        return text, time.perf_counter() - t0
    except Exception as exc:
        return f"[Marker error: {exc}]", 0.0


def _convert_mineru(path: Path) -> tuple[str, float]:
    try:
        import magic_pdf  # noqa: F401
    except ImportError:
        return "[MinerU error: not installed — pip install magic-pdf]", 0.0
    try:
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            t0 = time.perf_counter()
            result = subprocess.run(
                [sys.executable, "-m", "magic_pdf", "-p", str(path), "-o", tmpdir, "-m", "auto"],
                capture_output=True, text=True, timeout=300,
            )
            elapsed = time.perf_counter() - t0
            if result.returncode != 0:
                stderr_snippet = result.stderr.strip()[:200] if result.stderr else "unknown error"
                return f"[MinerU error: {stderr_snippet}]", elapsed
            md_files = sorted(Path(tmpdir).rglob("*.md"))
            if not md_files:
                return "[MinerU error: no output markdown found]", elapsed
            text = "\n\n".join(p.read_text(encoding="utf-8") for p in md_files)
            return text, elapsed
    except Exception as exc:
        return f"[MinerU error: {exc}]", 0.0


_CONVERTERS: dict[str, object] = {
    "aksharamd":    _convert_aksharamd,
    "markitdown":   _convert_markitdown,
    "unstructured": _convert_unstructured,
    "docling":      _convert_docling,
    "pymupdf4llm":  _convert_pymupdf4llm,
    "llamaparse":   _convert_llamaparse,
    "marker":       _convert_marker,
    "mineru":       _convert_mineru,
}

TOOL_DISPLAY = {
    "aksharamd":    "AksharaMD",
    "markitdown":   "MarkItDown",
    "unstructured": "Unstructured",
    "docling":      "Docling",
    "pymupdf4llm":  "PyMuPDF4LLM",
    "llamaparse":   "LlamaParse",
    "marker":       "Marker",
    "mineru":       "MinerU",
}


# ── Q&A auto-generation ───────────────────────────────────────────────────────

def _generate_qa(doc_text: str, filename: str, n: int = 4) -> list[dict]:
    excerpt = doc_text[:5000]
    prompt = (
        f"You are creating a document QA test for '{filename}'.\n\n"
        f"Document excerpt:\n---\n{excerpt}\n---\n\n"
        f"Generate exactly {n} question-answer pairs. Each question must be directly "
        "answerable from the excerpt. Each answer should be specific: a name, number, "
        "date, or short phrase — not a full sentence.\n\n"
        "Respond in YAML only, no other text:\n"
        "qa:\n"
        '  - q: "specific question"\n'
        '    a: "specific answer"\n'
    )
    resp = _call_claude(prompt, max_tokens=600)
    if resp.error or not resp.text:
        return []
    raw = resp.text
    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:]).rstrip("`").strip()
    try:
        parsed = yaml.safe_load(raw) or {}
        pairs = parsed.get("qa", [])
        return [p for p in pairs if isinstance(p, dict) and p.get("q")]
    except Exception:
        return []


# ── judge scoring ─────────────────────────────────────────────────────────────

def _judge(question: str, expected: str, answer: str) -> int:
    if not answer or answer.startswith("["):
        return 0
    prompt = (
        "You are an objective answer quality judge.\n\n"
        f"Question:         {question}\n"
        f"Expected answer:  {expected}\n"
        f"Answer to score:  {answer}\n\n"
        "Score the answer from 0 to 10 for correctness and completeness. "
        "10 = fully correct. 0 = wrong or irrelevant. Be strict.\n"
        "Reply with a single integer only."
    )
    resp = _call_claude(prompt, max_tokens=8, model=_JUDGE_MODEL)
    try:
        return max(0, min(10, int(resp.text.strip().split()[0])))
    except Exception:
        return 0


# ── result container ──────────────────────────────────────────────────────────

@dataclass
class QAResult:
    document: str
    question: str
    expected: str
    tool: str
    llm: str
    answer: str
    score: int        # 0–10; -1 = not scored (no expected answer)
    doc_tokens: int   # tokens in conversion output (tiktoken cl100k_base)
    error: str = ""


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:  # noqa: C901
    parser = argparse.ArgumentParser(
        description="Multi-LLM QA evaluation: AksharaMD vs MarkItDown vs Unstructured vs Docling",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("docs", nargs="*", metavar="FILE",
                        help="Document files to evaluate (used for auto Q&A generation)")
    parser.add_argument("--qa", metavar="YAML",
                        help="Pre-written Q&A YAML file (skips auto-generation)")
    parser.add_argument("--tools", nargs="+", default=["aksharamd", "markitdown"],
                        choices=list(_CONVERTERS),
                        help="Conversion tools to compare (default: aksharamd markitdown)")
    parser.add_argument("--llms", nargs="+", default=None,
                        choices=list(_LLM_FNS),
                        help="LLMs to use (default: all available)")
    parser.add_argument("--no-llm", action="store_true",
                        help="Conversion stats only — no LLM calls, no API keys needed")
    parser.add_argument("--n-qa", type=int, default=4,
                        help="Questions to auto-generate per document (default: 4)")
    parser.add_argument("--save-qa", metavar="PATH",
                        help="Save auto-generated Q&A pairs to YAML for reuse")
    parser.add_argument("--out", default=str(RESULTS_PATH),
                        help=f"JSON results output path (default: {RESULTS_PATH})")
    args = parser.parse_args()

    # ── resolve active LLMs ──────────────────────────────────────────────────
    if args.no_llm:
        active_llms: list[str] = []
    elif args.llms:
        active_llms = args.llms
    else:
        active_llms = _detect_available_llms()

    if not active_llms and not args.no_llm:
        print(
            "No LLM backends detected. Install at least one and set its API key:\n"
            "\n"
            "  Claude (recommended):  pip install anthropic   →  ANTHROPIC_API_KEY\n"
            "  OpenAI:                pip install openai       →  OPENAI_API_KEY\n"
            "  Gemini:                pip install google-genai →  GEMINI_API_KEY\n"
            "\n"
            "Run with --no-llm to see conversion stats without any API calls.",
            file=sys.stderr,
        )
        sys.exit(1)

    # ── build document/QA list ───────────────────────────────────────────────
    qa_docs: list[dict] = []

    if args.qa:
        qa_path = Path(args.qa)
        if not qa_path.exists():
            print(f"ERROR: Q&A file not found: {qa_path}", file=sys.stderr)
            sys.exit(1)
        with open(qa_path, encoding="utf-8") as fh:
            qa_docs = yaml.safe_load(fh).get("documents", [])

        needs_gen = [d for d in qa_docs if not d.get("qa") and Path(d.get("path", "")).exists()]
        if needs_gen and not args.no_llm:
            print(f"Auto-generating Q&A pairs for {len(needs_gen)} document(s) with Claude Haiku…\n")
            for doc_entry in needs_gen:
                p = Path(doc_entry["path"])
                print(f"  {p.name} … ", end="", flush=True)
                try:
                    text, _ = _convert_aksharamd(p)
                    pairs = _generate_qa(text, p.name, n=args.n_qa)
                    doc_entry["qa"] = pairs
                    print(f"{len(pairs)} questions")
                except Exception as exc:
                    doc_entry["qa"] = []
                    print(f"ERROR: {exc}")
            if args.save_qa:
                save_path = Path(args.save_qa)
                save_path.parent.mkdir(parents=True, exist_ok=True)
                with open(save_path, "w", encoding="utf-8") as fh:
                    yaml.dump({"documents": qa_docs}, fh, allow_unicode=True,
                              default_flow_style=False, sort_keys=False)
                print(f"\n  Q&A pairs saved → {args.save_qa}")
            print()

    elif args.docs:
        paths = [Path(p) for p in args.docs]
        missing = [p for p in paths if not p.exists()]
        if missing:
            for p in missing:
                print(f"ERROR: file not found: {p}", file=sys.stderr)
            sys.exit(1)

        if args.no_llm:
            qa_docs = [{"path": str(p), "qa": []} for p in paths]
        else:
            print("Generating Q&A pairs with Claude Haiku…\n")
            for p in paths:
                print(f"  {p.name} … ", end="", flush=True)
                try:
                    text, _ = _convert_aksharamd(p)
                    pairs = _generate_qa(text, p.name, n=args.n_qa)
                    if pairs:
                        qa_docs.append({"path": str(p), "qa": pairs})
                        print(f"{len(pairs)} questions")
                    else:
                        print("no questions generated (document too short?)")
                except Exception as exc:
                    print(f"ERROR: {exc}")

            if args.save_qa and qa_docs:
                save_path = Path(args.save_qa)
                save_path.parent.mkdir(parents=True, exist_ok=True)
                with open(save_path, "w", encoding="utf-8") as fh:
                    yaml.dump({"documents": qa_docs}, fh, allow_unicode=True,
                              default_flow_style=False, sort_keys=False)
                print(f"\n  Q&A pairs saved → {save_path}")
    else:
        parser.print_help()
        sys.exit(0)

    if not qa_docs:
        print("No documents to evaluate.", file=sys.stderr)
        sys.exit(1)

    # ── print run config ─────────────────────────────────────────────────────
    tool_labels = [TOOL_DISPLAY.get(t, t) for t in args.tools]
    print(f"\nEvaluation config")
    print(f"  Documents : {len(qa_docs)}")
    print(f"  Tools     : {', '.join(tool_labels)}")
    if active_llms:
        print(f"  LLMs      : {', '.join(LLM_DISPLAY.get(l, l) for l in active_llms)}")
        print(f"  Judge     : {_JUDGE_MODEL}")
    else:
        print(f"  LLMs      : none (conversion stats only)")
    print()

    # ── evaluation loop ──────────────────────────────────────────────────────
    all_results: list[QAResult] = []
    # token_tally tracks counts for --no-llm summary: {tool: [token_counts]}
    token_tally: dict[str, list[int]] = defaultdict(list)

    for doc_entry in qa_docs:
        doc_path = Path(doc_entry["path"])
        qa_pairs = doc_entry.get("qa", [])

        if not doc_path.exists():
            print(f"  SKIP (not found): {doc_path}")
            continue

        print(f"  {doc_path.name}")

        tool_texts: dict[str, tuple[str, float]] = {}
        for tool in args.tools:
            label = TOOL_DISPLAY.get(tool, tool)
            print(f"    {label:<15} converting … ", end="", flush=True)
            try:
                text, elapsed = _CONVERTERS[tool](doc_path)  # type: ignore[operator]
                is_error = text.startswith("[") and "error" in text.lower()
                token_count = 0 if is_error else _count_tokens(text)
                tool_texts[tool] = (text, elapsed)
                if token_count > 0:
                    token_tally[tool].append(token_count)
                if is_error:
                    print(f"  UNSUPPORTED  {elapsed:.2f}s  {text[:60]}")
                else:
                    print(f"{token_count:>8,} tokens   {elapsed:.2f}s")
            except Exception as exc:
                tool_texts[tool] = ("", 0.0)
                print(f"FAILED: {exc}")

        if not active_llms or not qa_pairs:
            print()
            continue

        for qa in qa_pairs:
            question = (qa.get("q") or "").strip()
            expected = (qa.get("a") or "").strip()
            if not question:
                continue

            q_preview = question if len(question) <= 72 else question[:69] + "…"
            print(f"\n    Q: {q_preview}")
            if expected:
                print(f"       Expected: {expected[:65]}")

            for tool in args.tools:
                text, _ = tool_texts.get(tool, ("", 0.0))
                is_error = text.startswith("[") and "error" in text.lower()
                context = text[:6000]
                doc_tokens = 0 if is_error else _count_tokens(text)
                label = TOOL_DISPLAY.get(tool, tool)

                for llm_name in active_llms:
                    llm_fn = _LLM_FNS[llm_name]  # type: ignore[index]

                    if is_error:
                        score = 0
                        answer_preview = text[:65]
                        score_str = " n/a"
                        print(f"       {label:<15} {LLM_DISPLAY.get(llm_name, llm_name):<22} {score_str:>5}  {answer_preview!r}")
                        all_results.append(QAResult(
                            document=str(doc_path), question=question, expected=expected,
                            tool=tool, llm=llm_name, answer="", score=-1,
                            doc_tokens=0, error=text,
                        ))
                        continue

                    prompt = (
                        "Answer the question below using ONLY the document text "
                        "provided. Be concise — give just the answer value.\n\n"
                        f"Question: {question}\n\n"
                        f"Document:\n{context}"
                    )
                    resp: LLMResponse = llm_fn(prompt, max_tokens=128)  # type: ignore[operator]
                    time.sleep(0.25)

                    score = -1
                    if expected and not resp.error:
                        score = _judge(question, expected, resp.text)
                        time.sleep(0.25)

                    score_str = f"{score}/10" if score >= 0 else " n/a"
                    answer_preview = (
                        resp.text[:65] if resp.text
                        else f"[error: {resp.error[:50]}]"
                    )
                    print(f"       {label:<15} {LLM_DISPLAY.get(llm_name, llm_name):<22} {score_str:>5}  {answer_preview!r}")

                    all_results.append(QAResult(
                        document=str(doc_path), question=question, expected=expected,
                        tool=tool, llm=llm_name, answer=resp.text, score=score,
                        doc_tokens=doc_tokens, error=resp.error,
                    ))

        print()

    # ── token-only summary (--no-llm mode) ───────────────────────────────────
    if not active_llms and token_tally:
        _print_summary(all_results, args.tools, active_llms,
                       token_avgs_override={t: sum(v)/len(v) for t, v in token_tally.items()})

    # ── save results (before summary so a crash can't lose data) ─────────────
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "config": {"tools": args.tools, "llms": active_llms},
                "results": [
                    {
                        "document": r.document,
                        "question": r.question,
                        "expected": r.expected,
                        "tool": r.tool,
                        "llm": r.llm,
                        "answer": r.answer,
                        "score": r.score,
                        "doc_tokens": r.doc_tokens,
                    }
                    for r in all_results
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Full results → {out_path}\n")

    # ── summary ──────────────────────────────────────────────────────────────
    if all_results:
        _print_summary(all_results, args.tools, active_llms)


def _print_summary(results: list[QAResult], tools: list[str],
                   llms: list[str],
                   token_avgs_override: dict[str, float] | None = None) -> None:
    scored = [r for r in results if r.score >= 0]
    col = max(len(TOOL_DISPLAY.get(t, t)) for t in tools) + 2

    # ── token comparison ─────────────────────────────────────────────────────
    token_map: dict[str, list[int]] = defaultdict(list)
    for r in results:
        if r.doc_tokens > 0:
            token_map[r.tool].append(r.doc_tokens)

    print("=" * 68)
    print("RESULTS SUMMARY")
    print("=" * 68)
    print(f"\n  {'Tool':<{col}} {'Avg tokens':>12}   Supported")
    print(f"  {'-'*col} {'-'*12}   ---------")

    token_avgs: dict[str, float] = token_avgs_override or {}
    total_docs = len(set(r.document for r in results)) if results else 0

    for tool in tools:
        label = TOOL_DISPLAY.get(tool, tool)
        if token_avgs_override:
            avg = token_avgs_override.get(tool, 0.0)
            # can't derive supported count without results in --no-llm mode
            print(f"  {label:<{col}} {avg:>12,.0f}")
        else:
            toks = token_map.get(tool, [])
            avg = sum(toks) / len(toks) if toks else 0.0
            token_avgs[tool] = avg
            supported = len(set(r.document for r in results if r.tool == tool and r.doc_tokens > 0))
            print(f"  {label:<{col}} {avg:>12,.0f}   {supported}/{total_docs} docs")

    # baseline tool for comparison (prefer aksharamd if present)
    base_tool = "aksharamd" if "aksharamd" in tools else tools[0]
    base_avg = token_avgs.get(base_tool, 0)
    base_label = TOOL_DISPLAY.get(base_tool, base_tool)

    print()
    for tool in tools:
        if tool == base_tool:
            continue
        label = TOOL_DISPLAY.get(tool, tool)
        other_avg = token_avgs.get(tool, 0)
        if other_avg > 0 and base_avg > 0:
            reduction = (1 - base_avg / other_avg) * 100
            if reduction > 0:
                print(f"  {base_label} uses {reduction:.0f}% fewer tokens than {label}")
            else:
                print(f"  {label} uses {abs(reduction):.0f}% fewer tokens than {base_label}")

    # ── answer quality ───────────────────────────────────────────────────────
    if not scored:
        print("\n  (No scored Q&A pairs — provide expected answers to see quality scores.)")
        print()
    else:
        print(f"\n  Answer quality (0–10, higher is better)\n")
        llm_labels = [LLM_DISPLAY.get(l, l) for l in llms] if llms else []
        avg_col = max((len(l) for l in llm_labels), default=8) + 2

        score_map: dict[tuple[str, str], list[int]] = defaultdict(list)
        for r in scored:
            score_map[(r.tool, r.llm)].append(r.score)

        header = f"  {'Tool':<{col}}" + "".join(f"{l:>{avg_col}}" for l in llm_labels) + f"{'Overall':>{avg_col}}"
        print(header)
        print("  " + "-" * (len(header) - 2))

        tool_overall: dict[str, float] = {}
        for tool in tools:
            label = TOOL_DISPLAY.get(tool, tool)
            row = f"  {label:<{col}}"
            per_llm: list[float] = []
            for llm in llms:
                scores = score_map.get((tool, llm), [])
                avg = sum(scores) / len(scores) if scores else 0.0
                per_llm.append(avg)
                row += f"{avg:>{avg_col}.1f}"
            overall = sum(per_llm) / len(per_llm) if per_llm else 0.0
            tool_overall[tool] = overall
            row += f"{overall:>{avg_col}.1f}"
            print(row)

        print()
        base_overall = tool_overall.get(base_tool, 0)
        for tool in tools:
            if tool == base_tool:
                continue
            label = TOOL_DISPLAY.get(tool, tool)
            other_overall = tool_overall.get(tool, 0)
            delta = base_overall - other_overall
            sign = "+" if delta >= 0 else ""
            pct = 100 * delta / other_overall if other_overall else 0
            print(f"  {base_label} {sign}{delta:.1f} pts ({sign}{pct:.1f}%) vs {label}")

    # ── cost projection ──────────────────────────────────────────────────────
    _print_cost_summary(token_avgs, tools)


def _print_cost_summary(token_avgs: dict[str, float], tools: list[str]) -> None:
    """Print API cost projection at three document-volume tiers."""
    col = max(len(TOOL_DISPLAY.get(t, t)) for t in tools) + 2
    volumes = [10_000, 100_000, 1_000_000]
    llm_keys = list(_LLM_COST_PER_TOKEN.keys())
    llm_labels = [LLM_DISPLAY[k] for k in llm_keys]
    llm_col = max(len(l) for l in llm_labels) + 2

    print("\n  API cost projection — input tokens only")
    print("  Pricing (verify at vendor sites): " +
          "  |  ".join(f"{LLM_DISPLAY[k]} ${_LLM_COST_PER_TOKEN[k]*1_000_000:.2f}/1M" for k in llm_keys))
    print()

    for vol in volumes:
        vol_label = f"{vol:,} docs"
        print(f"  {vol_label}")
        print(f"  {'Tool':<{col}}" + "".join(f"{l:>{llm_col}}" for l in llm_labels))
        print("  " + "-" * (col + llm_col * len(llm_keys)))
        for tool in tools:
            avg = token_avgs.get(tool, 0)
            label = TOOL_DISPLAY.get(tool, tool)
            if avg == 0:
                row = f"  {label:<{col}}" + "".join(f"{'N/A':>{llm_col}}" for _ in llm_keys)
            else:
                row = f"  {label:<{col}}"
                for k in llm_keys:
                    cost = avg * vol * _LLM_COST_PER_TOKEN[k]
                    row += f"  ${cost:>{llm_col-3},.0f}"
            print(row)

        # savings row vs baseline
        base_tool = "aksharamd" if "aksharamd" in tools else tools[0]
        base_label = TOOL_DISPLAY.get(base_tool, base_tool)
        base_avg = token_avgs.get(base_tool, 0)
        if base_avg > 0:
            for tool in tools:
                if tool == base_tool:
                    continue
                other_avg = token_avgs.get(tool, 0)
                if other_avg > 0 and other_avg > base_avg:
                    label = TOOL_DISPLAY.get(tool, tool)
                    savings_parts = []
                    for k in llm_keys:
                        saving = (other_avg - base_avg) * vol * _LLM_COST_PER_TOKEN[k]
                        savings_parts.append(f"{LLM_DISPLAY[k]} saves ${saving:,.0f}")
                    print(f"  vs {label}: " + "  |  ".join(savings_parts))
        print()


if __name__ == "__main__":
    main()
