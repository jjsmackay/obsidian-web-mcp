"""L2 write-boundary validator engine (M2 Frontmatter Governance Spec).

Orchestrates exemptions, the check sequence, warn/block mode, the archive
override, and the violation log. Runs BEFORE the atomic write. The engine is
generic; all Crucible rules arrive from the vault-loaded Vocab.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from .checks import (
    matches_any,
    run_frontmatter_checks,
    run_path_type_check,
    split_frontmatter,
)
from .log import append_violation
from .vocab import Vocab, VocabStore

logger = logging.getLogger(__name__)


@dataclass
class Outcome:
    """Result of validating one write. ``errors`` carry the M2 §5 contract."""
    errors: list[dict] = field(default_factory=list)
    mode: str = "warn"
    exempt: bool = False

    @property
    def has_violations(self) -> bool:
        return bool(self.errors)

    @property
    def blocked(self) -> bool:
        """True when the write must NOT proceed (block mode + violations)."""
        return self.mode == "block" and self.has_violations


def _vocab_unavailable_error(mode: str, detail: str | None) -> dict:
    return {
        "code": "E000_VOCAB_UNAVAILABLE",
        "field": None,
        "got": detail,
        "allowed": None,
        "rule_ref": "M2 Governance Spec §10.2",
        "hint": "Validator vocabulary could not be loaded; fix SYS_Schema_Vocab.yaml.",
        "mode": mode,
    }


class Validator:
    """The write-boundary validator. One instance per server process."""

    def __init__(self, mode: str, vocab_file: Path, log_file: Path, enabled: bool = True):
        self.mode = mode if mode in ("warn", "block") else "warn"
        self.enabled = enabled
        self.log_file = Path(log_file)
        self._store = VocabStore(vocab_file)

    def reload(self) -> bool:
        """Admin reload of the vocabulary (M2 spec §1)."""
        return self._store.reload()

    # --- public entry points ------------------------------------------------

    def validate_write(self, path: str, content: str, tool: str) -> Outcome:
        """Full 11-check validation on the resulting file content."""
        if not self.enabled:
            return Outcome(mode=self.mode, exempt=True)
        if not path.endswith(".md"):
            return Outcome(mode=self.mode, exempt=True)

        vocab = self._store.get()
        if vocab is None:
            return self._no_vocab(path, tool)

        if matches_any(path, vocab.exempt_paths):
            return Outcome(mode=self.mode, exempt=True)

        errors = run_frontmatter_checks(path, content, vocab)

        # §4 archive override: 60_Archive keeps any type -> drop E010 only.
        if matches_any(path, vocab.archive_override_paths):
            errors = [e for e in errors if e["code"] != "E010_PATH_TYPE_MISMATCH"]

        return self._finish(path, tool, errors)

    def validate_move(self, source_content: str, dest_path: str, tool: str) -> Outcome:
        """Path<->type coherence only, against the destination path (M2 §2)."""
        if not self.enabled:
            return Outcome(mode=self.mode, exempt=True)
        if not dest_path.endswith(".md"):
            return Outcome(mode=self.mode, exempt=True)

        vocab = self._store.get()
        if vocab is None:
            return self._no_vocab(dest_path, tool)

        if matches_any(dest_path, vocab.exempt_paths):
            return Outcome(mode=self.mode, exempt=True)
        # Archive keeps any type -> coherence is a pass.
        if matches_any(dest_path, vocab.archive_override_paths):
            return Outcome(mode=self.mode, exempt=True)

        note_type = self._extract_type(source_content)
        if note_type not in vocab.types:
            # Cannot determine a known type -> nothing to check (M2 §2: no field
            # checks; don't block moves of already-nonconforming notes).
            return Outcome(mode=self.mode)

        errors = run_path_type_check(dest_path, note_type, vocab)
        return self._finish(dest_path, tool, errors)

    # --- helpers ------------------------------------------------------------

    @staticmethod
    def _extract_type(content: str):
        import yaml
        raw, _ = split_frontmatter(content)
        if raw is None:
            return None
        try:
            parsed = yaml.safe_load(raw)
        except yaml.YAMLError:
            return None
        return parsed.get("type") if isinstance(parsed, dict) else None

    def _no_vocab(self, path: str, tool: str) -> Outcome:
        """Vocab could not be loaded. NEVER fail open (M2 §10.2): block mode
        refuses; warn mode writes but logs and warns loudly."""
        error = _vocab_unavailable_error(self.mode, self._store.load_error)
        written = self.mode != "block"
        append_violation(
            self.log_file, tool=tool, path=path,
            codes=["E000_VOCAB_UNAVAILABLE"], mode=self.mode, written=written,
        )
        return Outcome(errors=[error], mode=self.mode)

    def _finish(self, path: str, tool: str, errors: list[dict]) -> Outcome:
        if not errors:
            return Outcome(mode=self.mode)
        for e in errors:
            e["mode"] = self.mode
        written = self.mode != "block"
        append_violation(
            self.log_file, tool=tool, path=path,
            codes=[e["code"] for e in errors], mode=self.mode, written=written,
        )
        return Outcome(errors=errors, mode=self.mode)


# --- process-wide singleton, built lazily from config -----------------------

_validator: Validator | None = None


def _resolve(rel_or_abs: str) -> Path:
    from .. import config
    p = Path(rel_or_abs)
    return p if p.is_absolute() else (config.VAULT_PATH / rel_or_abs)


def get_validator() -> Validator:
    global _validator
    if _validator is None:
        from .. import config
        _validator = Validator(
            mode=config.VALIDATOR_MODE,
            vocab_file=_resolve(config.VALIDATOR_VOCAB_PATH),
            log_file=_resolve(config.VALIDATOR_LOG_PATH),
            enabled=config.VALIDATOR_ENABLED,
        )
    return _validator


def reset_validator() -> None:
    """Drop the singleton (tests, or after a config change)."""
    global _validator
    _validator = None


def validate_write(path: str, content: str, tool: str) -> Outcome:
    return get_validator().validate_write(path, content, tool)


def validate_move(source_content: str, dest_path: str, tool: str) -> Outcome:
    return get_validator().validate_move(source_content, dest_path, tool)
