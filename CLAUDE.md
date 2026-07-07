# Claude Code — AksharaMD Project Instructions

## Test execution (Windows)

This project runs on a Windows machine. Do not treat it as a full CI runner.

### Workflow

1. **Run targeted tests first** — only files related to changed code:
   ```powershell
   python -m pytest tests/test_cli.py tests/test_compiler.py -q --tb=short
   ```

2. **Run the full suite at most once** if targeted tests pass:
   ```powershell
   python -m pytest tests/ -q --tb=short
   ```
   If it backgrounds, hangs, or times out — stop. Do not retry.

3. **Before starting any test run**, check for existing Python processes:
   ```powershell
   Get-Process python -ErrorAction SilentlyContinue | Select-Object Id, CPU, WorkingSet, StartTime
   ```
   If stale processes exist, stop and ask before continuing.

4. **GitHub Actions is the source of truth** for full CI. Preferred workflow:
   ```
   targeted local tests → push PR → GitHub Actions CI → merge when green
   ```

5. **Kill stale processes only when safe:**
   ```powershell
   Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
   ```

### PR test reporting

Only claim checks that actually completed with a final result.

**Correct:**
```
pytest tests/test_cli.py tests/test_compiler.py -q — passed
GitHub Actions CI — green (4/4)
```

**Never write:**
```
Full pytest suite running in background
```

If the full local suite does not complete cleanly, say so and rely on GitHub Actions.
