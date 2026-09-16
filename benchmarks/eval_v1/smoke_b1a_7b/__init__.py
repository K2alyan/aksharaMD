"""B1a-7b.2a smoke harness.

Instrument construction for the B1a-7b.2 infrastructure smoke. This
package deliberately has no import-time side effects: importing it
does NOT invoke a parser, does NOT create a firewall rule, does NOT
touch the network, and does NOT read any V2 smoke payload.

Real execution requires calling ``entrypoint.main()`` explicitly from
the CLI entry point.
"""
from __future__ import annotations

__all__ = [
    "SMOKE_SPEC_VERSION",
    "PARSER_EXECUTION_CONTRACT_VERSION",
    "NORMALIZATION_VERSION",
]

SMOKE_SPEC_VERSION = "v1"
PARSER_EXECUTION_CONTRACT_VERSION = "v1"
NORMALIZATION_VERSION = "2"
