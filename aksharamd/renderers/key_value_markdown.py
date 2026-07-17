"""Deterministic renderers for KeyValueGroup."""
from __future__ import annotations

from ..models.key_value import KeyValueGroup


def render_key_value_group(group: KeyValueGroup) -> str:
    """Render a KeyValueGroup as Markdown bullet list.

    Single record:
        #### Title
        - **Key:** Value
        - **Key:** Value

    Multiple records (duplicate keys present):
        #### Title

        **Record 1**
        - **Key:** Value

        **Record 2**
        - **Key:** Value
    """
    lines: list[str] = []

    if group.title:
        lines.append(f"#### {group.title}")
        lines.append("")

    # Detect whether this group has repeated keys (multiple records)
    seen_keys: set[str] = set()
    has_repeated_keys = False
    for entry in group.entries:
        if entry.key in seen_keys:
            has_repeated_keys = True
            break
        seen_keys.add(entry.key)

    if has_repeated_keys:
        # Group entries into records by repeated key boundaries
        records = _split_into_records(group.entries)
        for i, record in enumerate(records, 1):
            lines.append(f"**Record {i}**")
            for entry in record:
                lines.append(f"- **{_escape_md(entry.key)}:** {_escape_md(entry.value)}")
            lines.append("")
    else:
        for entry in group.entries:
            lines.append(f"- **{_escape_md(entry.key)}:** {_escape_md(entry.value)}")

    return "\n".join(lines).rstrip()


def render_key_value_tsv(group: KeyValueGroup) -> str:
    """Render a KeyValueGroup as tab-separated key\\tvalue pairs.

    Record boundaries marked with blank lines.
    Title emitted as [Title] header.
    """
    lines: list[str] = []

    if group.title:
        lines.append(f"[{group.title}]")

    seen_keys: set[str] = set()
    has_repeated_keys = False
    for entry in group.entries:
        if entry.key in seen_keys:
            has_repeated_keys = True
            break
        seen_keys.add(entry.key)

    if has_repeated_keys:
        records = _split_into_records(group.entries)
        for i, record in enumerate(records, 1):
            if i > 1:
                lines.append("")
            lines.append(f"[Record {i}]")
            for entry in record:
                key = entry.key.replace("\t", " ")
                val = entry.value.replace("\t", " ").replace("\n", " ")
                lines.append(f"{key}\t{val}")
    else:
        for entry in group.entries:
            key = entry.key.replace("\t", " ")
            val = entry.value.replace("\t", " ").replace("\n", " ")
            lines.append(f"{key}\t{val}")

    return "\n".join(lines)


def _split_into_records(entries) -> list[list]:
    """Split entries into records at repeated-key boundaries."""
    if not entries:
        return []
    records: list[list] = []
    current: list = []
    seen: set[str] = set()
    for entry in entries:
        if entry.key in seen:
            # Start new record
            records.append(current)
            current = [entry]
            seen = {entry.key}
        else:
            current.append(entry)
            seen.add(entry.key)
    if current:
        records.append(current)
    return records


def _escape_md(text: str) -> str:
    """Escape minimal Markdown special chars in key/value text."""
    return text.replace("\\", "\\\\").replace("|", "\\|")
