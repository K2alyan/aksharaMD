# Static security review — `baidu/Unlimited-OCR` @ `d549bb9d`

**Repository:** `baidu/Unlimited-OCR` on HuggingFace
**Revision:** `d549bb9d6a055dbe291408916d66acc2cd5920f6` (initial commit, 2026-06-22)
**Reviewed at:** 2026-07-20
**Reviewed against:** the exact snapshot downloaded during Phase A0 (14-file inventory).
**Scope:** the five `.py` files under `trust_remote_code=True` — `configuration_deepseek_v2.py`, `conversation.py`, `deepencoder.py`, `modeling_deepseekv2.py`, `modeling_unlimitedocr.py`. All executed only after `verify_trusted_code_files` returns True.

## Method

Files were resolved from HuggingFace symlinks to a scratch directory and inspected as text. Reviewer did NOT import or execute any of the files. Categories checked (grep + surrounding context read):

- Network calls
- Subprocess / shell execution
- Dynamic imports (`__import__`, `importlib`, `compile(...)`)
- `eval` / `exec`
- Unsafe deserialization (`pickle`, `torch.load`, `joblib`, `dill`, `marshal`, `shelve`, `cloudpickle`)
- Arbitrary file writes
- Environment / credential access
- Imports of the bundled `sglang` wheel
- Conditional heavy deps (`flash_attn`, `flashinfer`, `vllm`, `xformers`, `triton`)
- Hard-coded CUDA behavior

## Findings

### Real risks

- **7 `eval()` calls on model-generated text** in `modeling_unlimitedocr.py:66, 1099, 1101, 1104, 1112, 1113, 1128`.

  - Line 66 (`extract_coordinates_and_label`): `cor_list = eval(ref_text[2])` — parses a text token like `"[1.0, 2.0, 3.0, 4.0]"` from model output.
  - Lines 1099-1128 (inside `if 'line_type' in outputs:`): `eval(outputs)`, `eval(line.split(' -- ')[0])`, `eval(endpoint.split(': ')[1])` — parse dict-literal / tuple-literal strings from model output for a "Line" detection visualization path.

  All 7 sites operate on strings that ultimately originate in the model's generated text. With `trust_remote_code=True`, the model's own output passes through `eval()` inside the calling process. A model prompted or hallucinating into emitting a payload such as `__import__('os').system('...')` would execute arbitrary code.

  **Baidu should have used `ast.literal_eval`. They did not.** This is the primary reason execution must be blocked until A1c ships the module-local eval override.

- **1 `torch.load(checkpoint)`** at `deepencoder.py:1049`. Pickle-based, therefore unsafe by construction.

  Guarded by `if checkpoint is not None:` inside `build_sam_vit_b(...)`. The `AutoModel.from_pretrained` code path does NOT pass a `checkpoint=` argument — model weights flow through HuggingFace's safetensors loader instead. **Dormant unless directly invoked; A1c forces `use_safetensors=True` to make this concrete.**

- **16 hard-coded `.cuda()` calls** across `modeling_unlimitedocr.py:582, 1003-1005, 1028-1030, 1049, 1059, 1070, 1241-1243, 1259` and `torch.compile` at `deepencoder.py:1017`.

  Confirms the CUDA-only lock recorded in the plan (open decision D-6). MPS / CPU device selection is impossible without patching Baidu's file on disk. Not a security issue per se — a portability constraint that is already documented.

### Neutral / dead code

- **`import requests`** at `modeling_unlimitedocr.py:6` — imported but never called anywhere in the 5 files. Dead import (probably copy-paste). Not a network-call risk at this revision. Re-check on any future revision.
- **`import os`** at `modeling_unlimitedocr.py:14` — used only for `os.makedirs(output_path)` and `os.makedirs(f'{output_path}/images')` at lines 790, 791, 1157, 1158. No `os.system`, no `os.environ`, no credential access.
- **`import torch.distributed`** at `modeling_deepseekv2.py:29` — only exercised when `dist.is_initialized()`; not reached in single-GPU inference.
- **`from flash_attn import ...`** at `modeling_deepseekv2.py:66-67` — properly guarded by `if is_flash_attn_2_available():`. No import failure if the dependency is missing.

### Clean

- **No subprocess / shell execution.** Zero occurrences of `subprocess`, `os.system`, `os.popen`, `Popen`, `check_output`, `check_call`, `shell=True`.
- **No dynamic imports.** Zero occurrences of `__import__`, `importlib`, `compile(`.
- **No `exec()`.**
- **No credentials / environment reads beyond `os.makedirs`.**
- **No import of the bundled `sglang` wheel.** Nothing in the 5 files references `sglang`, `vllm`, `flashinfer`, `xformers`, or `triton` (except the guarded `flash_attn` above).
- **No other unsafe deserialization** — no `pickle`, `joblib`, `dill`, `marshal`, `shelve`, `cloudpickle`.
- **No arbitrary file writes beyond caller-supplied `output_path`.** `open(...)` writes are limited to `f'{output_path}/result.md'` at lines 1094 and 1296; `output_path` comes from the caller (our adapter passes a `TemporaryDirectory`).

## Corroborating data

- **`config.json` `auto_map`** references `modeling_unlimitedocr.UnlimitedOCRConfig` and `modeling_unlimitedocr.UnlimitedOCRForCausalLM` — these are the two classes loaded by `AutoModel.from_pretrained`. No other module is auto-loaded.
- **`model.safetensors.index.json`** declares 2,710 tensors, all pointing to the single approved shard `model-00001-of-000001.safetensors` (metadata `total_size` = 6,672,212,480 bytes matches the shard file size minus safetensors header overhead). No stray `.bin` / `.pt` / `.pkl` / alternate-format entries.
- **`config.json` does NOT mention `sglang`, `vllm`, or `flash_attn`.** The Transformers `AutoModel` path does not require the bundled wheel.
- **`sglang` wheel** metadata: pure-Python (`py3-none-any`), no native binaries (`.so` / `.pyd` / `.dll`), no install scripts. Kept quarantined; not installed.

## Verdict

**Conditionally cleared for loading, subject to A1c mitigation.**

- All 7 `eval()` sites MUST be neutralized before `AutoModel.from_pretrained` runs. A1c ships a module-local override (`modeling_unlimitedocr.eval = ast.literal_eval`) installed immediately after import and asserted active before any model instantiation.
- `use_safetensors=True` MUST be forced on every load path. A1c re-asserts and locks with a test.
- Every file in the 12-file runtime manifest MUST pass SHA-256 verification (A1a).
- Every model load MUST re-hash the 11 non-weight runtime files (A1b fast mode). The single 6.67 GB safetensors shard is covered by full verification at install and by `aksharamd models verify` (A1b + Phase E).

If the upstream revision changes, this review is invalidated and must be re-performed. The manifest's `manifest_id` (`unlimited-ocr-d549bb9d-v1`) is the stable identifier tying this review to the exact snapshot state.
