# Sample Code Listing

The following is a representative snippet from the library documentation:

```python
def transform_batch(records, *, delimiter="\t", quote_char='"'):
    """Yield normalized records from a batch, splitting on delimiter."""
    for raw in records:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = [f.strip(quote_char) for f in stripped.split(delimiter)]
        yield {"id": fields[0], "payload": fields[1:], "raw": raw}
```

Callers should treat any raw line beginning with `#` as a comment and
skip it silently. Empty lines are also ignored. The function tolerates
trailing whitespace on any field.

## Notes

Non-ASCII delimiters (for example `|` or `│`) are permitted so
long as they are single-character strings. Multi-character delimiters
are rejected with `ValueError`.
