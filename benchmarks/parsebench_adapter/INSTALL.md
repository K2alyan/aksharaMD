# ParseBench Adapter — Installation

This directory contains the AksharaMD provider for [run-llama/ParseBench](https://github.com/run-llama/ParseBench).

## Prerequisites

- ParseBench cloned locally
- AksharaMD installed in the same Python environment

```bash
git clone https://github.com/run-llama/ParseBench.git /path/to/parsebench
pip install aksharamd
pip install -e "/path/to/parsebench[runners]"
```

## Installation steps

### 1. Copy the provider

```bash
cp benchmarks/parsebench_adapter/aksharamd.py \
   /path/to/parsebench/src/parse_bench/inference/providers/parse/aksharamd.py
```

### 2. Register the provider in `providers/parse/__init__.py`

Add `"aksharamd"` as the first entry in `_PROVIDER_MODULES`:

```python
_PROVIDER_MODULES = [
    "aksharamd",     # <-- add this line
    "anthropic",
    # ... rest of the list
]
```

### 3. Register the pipeline in `pipelines/parse.py`

Append to the end of the file:

```python
def _register_aksharamd_pipelines(register_fn) -> None:
    """Register AksharaMD pipelines."""
    register_fn(
        PipelineSpec(
            pipeline_name="aksharamd_parse",
            provider_name="aksharamd",
            product_type=ProductType.PARSE,
            config={},
        )
    )
```

### 4. Import and call in `pipelines/__init__.py`

In `_register_builtin_pipelines()`, add the import and call:

```python
def _register_builtin_pipelines() -> None:
    from parse_bench.inference.pipelines.extract import register_extract_pipelines
    from parse_bench.inference.pipelines.layout import register_layout_pipelines
    from parse_bench.inference.pipelines.parse import (
        _register_aksharamd_pipelines,   # <-- add this
        register_parse_pipelines,
    )

    register_parse_pipelines(register_pipeline)
    _register_aksharamd_pipelines(register_pipeline)  # <-- add this
    register_layout_pipelines(register_pipeline)
    register_extract_pipelines(register_pipeline)
```

## Running

```bash
cd /path/to/parsebench

# Smoke test (no download required)
PYTHONUTF8=1 python -m parse_bench.pipeline.cli run aksharamd_parse --test --open_report false

# Single dimensions (~500 MB each)
PYTHONUTF8=1 python -m parse_bench.pipeline.cli run aksharamd_parse --group text_content --open_report false
PYTHONUTF8=1 python -m parse_bench.pipeline.cli run aksharamd_parse --group text_formatting --open_report false

# Full benchmark (~2 GB)
PYTHONUTF8=1 python -m parse_bench.pipeline.cli run aksharamd_parse --open_report false
```

`PYTHONUTF8=1` is required on Windows to avoid cp1252 encoding errors.

Results are written to `output/aksharamd_parse/` as HTML reports, JSON, and CSV.
