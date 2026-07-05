"""L2 write-boundary validator (M2 Frontmatter Governance).

Generic validation engine that refuses illegal frontmatter writes. The rules
live in the vault (SYS_Schema_Vocab.yaml); this package is the engine only.
"""

from .engine import (
    Outcome,
    Validator,
    get_validator,
    reset_validator,
    validate_move,
    validate_write,
)

__all__ = [
    "Outcome",
    "Validator",
    "get_validator",
    "reset_validator",
    "validate_write",
    "validate_move",
]
