"""Runtime vocabulary loader for the L2 validator.

The validator's vocabulary (types, statuses, ai_flags, per-type required fields,
the §4 path<->type table, exemptions, naming rules, collapse map) lives in a
machine-readable companion file IN THE VAULT -- ``SYS_Schema_Vocab.yaml`` --
which mirrors SYS_Schema and the M2 governance spec §4. Editing the vocabulary
is a vault edit, not a redeploy (M2 spec §1, §10.2).

Contract (M2 spec §10.2):
- Parse on first use; re-parse when the file changes (mtime) or on reload().
- Cache the parsed vocab; do NOT re-parse on every write.
- If the file is unparseable, keep the last-good vocab and log loudly.
- NEVER fail open to "no validation".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Vocab:
    """Parsed, validated vocabulary. All rule data the checks need."""
    core_fields: list[str]
    types: set[str]
    statuses: list[str]
    ai_flags: list[str]
    date_fields: list[str]
    per_type_required: dict[str, list[str]]
    legacy_fields: dict[str, str]
    collapse_map: dict[str, str]
    path_type_table: dict[str, list[str]]
    archive_override_paths: list[str]
    exempt_paths: list[str]
    naming_rules: list[dict]
    schema_version: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "Vocab":
        """Build a Vocab from parsed YAML, failing loudly on a malformed shape.

        A structurally-wrong file (e.g. empty ``types``) is treated the same as
        a parse error by the loader: last-good is kept, never fail-open."""
        if not isinstance(data, dict):
            raise ValueError("vocab root is not a mapping")
        core = data.get("core_fields")
        types = data.get("types")
        if not core or not types:
            raise ValueError("vocab missing required 'core_fields' or 'types'")
        return cls(
            core_fields=list(core),
            types=set(types),
            statuses=list(data.get("statuses", [])),
            ai_flags=list(data.get("ai_flags", [])),
            date_fields=list(data.get("date_fields", [])),
            per_type_required={k: list(v or []) for k, v in (data.get("per_type_required") or {}).items()},
            legacy_fields=dict(data.get("legacy_fields") or {}),
            collapse_map={str(k): str(v) for k, v in (data.get("collapse_map") or {}).items()},
            path_type_table={k: list(v or []) for k, v in (data.get("path_type_table") or {}).items()},
            archive_override_paths=list(data.get("archive_override_paths", [])),
            exempt_paths=list(data.get("exempt_paths", [])),
            naming_rules=[dict(r) for r in (data.get("naming_rules") or [])],
            schema_version=str(data.get("schema_version", "")),
        )


class VocabStore:
    """Holds the cached vocab and reloads it from disk when the file changes."""

    def __init__(self, vocab_file: Path):
        self._vocab_file = Path(vocab_file)
        self._vocab: Vocab | None = None
        self._mtime: float | None = None
        self._load_error: str | None = None

    @property
    def load_error(self) -> str | None:
        return self._load_error

    def _read_and_parse(self) -> Vocab:
        import yaml
        text = self._vocab_file.read_text(encoding="utf-8")
        return Vocab.from_dict(yaml.safe_load(text))

    def reload(self) -> bool:
        """Force a re-parse (admin reload call). Returns True on success.
        On failure keeps last-good and logs loudly."""
        try:
            self._vocab = self._read_and_parse()
            self._mtime = self._vocab_file.stat().st_mtime
            self._load_error = None
            logger.info(
                "L2 validator: loaded vocab from %s (schema %s, %d types)",
                self._vocab_file, self._vocab.schema_version, len(self._vocab.types),
            )
            return True
        except Exception as e:  # noqa: BLE001 -- last-good fallback, never fail-open
            self._load_error = str(e)
            if self._vocab is None:
                logger.error(
                    "L2 validator: vocab file %s is unavailable/unparseable and there "
                    "is NO last-good vocab (%s). Validation cannot run until this is fixed.",
                    self._vocab_file, e,
                )
            else:
                logger.error(
                    "L2 validator: vocab file %s failed to parse (%s). KEEPING last-good "
                    "vocab (schema %s). Fix the file; validation continues on last-good.",
                    self._vocab_file, e, self._vocab.schema_version,
                )
            return False

    def get(self) -> Vocab | None:
        """Return the current vocab, reloading if the file changed on disk.

        A stat() per call is negligible; the YAML is only re-parsed when its
        mtime changes (M2 spec §10.2: cached, not re-read per write)."""
        try:
            mtime = self._vocab_file.stat().st_mtime
        except OSError:
            # File missing: keep last-good (may be None -> engine fails closed in block).
            if self._vocab is None and self._load_error is None:
                self.reload()  # produce the loud "no vocab" log once
            return self._vocab
        if self._vocab is None or mtime != self._mtime:
            if not self.reload():
                # Debounce: don't re-parse a broken file on every write; retry
                # only when its mtime changes again (e.g. Chris fixes it).
                self._mtime = mtime
        return self._vocab
