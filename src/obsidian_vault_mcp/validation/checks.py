"""The 11 frontmatter checks (M2 Frontmatter Governance Spec §3).

Pure functions over (path, resulting content, vocab). No I/O, no disk writes,
no knowledge of warn/block mode -- the engine owns those. Every Crucible-specific
value (types, statuses, required fields, path table, naming rules) arrives via
the ``vocab`` object, which is loaded from the vault at runtime. This module is
the generic engine; the vault holds the rules.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from .vocab import Vocab

# Rule references for the error contract (M2 spec §5). Point the calling LLM at
# the clause it violated so it can self-correct in one retry.
RULE_REFS = {
    "E001_YAML_PARSE": "SYS_Schema §5 (strict YAML)",
    "E002_BARE_WIKILINK": "SYS_Schema §5 rule 1",
    "E003_TYPE_UNKNOWN": "SYS_Schema §2",
    "E004_CORE_FIELD_MISSING": "SYS_Schema §1",
    "E005_STATUS_ILLEGAL": "SYS_Schema §3",
    "E006_AI_FLAGS_ILLEGAL": "SYS_Schema §4",
    "E007_TYPE_FIELD_MISSING": "SYS_Schema §6",
    "E008_DATE_FORMAT": "SYS_Schema §5 rule 3",
    "E009_LEGACY_FIELD": "SYS_Schema §9",
    "E010_PATH_TYPE_MISMATCH": "M2 Governance Spec §4",
    "E011_NAMING_CONVENTION": "SYS_Schema §7",
}


def make_error(code, field=None, got=None, allowed=None, hint=None):
    """Build one error object. ``code``/``field``/``got``/``allowed`` are always
    present (M2 spec §5); ``mode`` is stamped by the engine; ``hint`` is included
    only when a collapse-map suggestion exists."""
    error = {
        "code": code,
        "field": field,
        "got": got,
        "allowed": allowed,
        "rule_ref": RULE_REFS[code],
    }
    if hint:
        error["hint"] = hint
    return error


# --- Path matching (shared by exemptions, §4 coherence, §11 naming) ---------

def path_matches(rel_path: str, pattern: str) -> bool:
    """Match a vault-relative path against a §4-style pattern.

    - ``dir/**``  -> anywhere at or under ``dir`` (any depth)
    - ``dir/``    -> a file directly inside ``dir`` only (not sub-directories)
    - ``exact``   -> exact path equality
    """
    p = rel_path.strip("/")
    if pattern.endswith("/**"):
        base = pattern[:-3].strip("/")
        return p == base or p.startswith(base + "/")
    if pattern.endswith("/"):
        base = pattern.rstrip("/")
        parent = p.rsplit("/", 1)[0] if "/" in p else ""
        return parent == base
    return p == pattern


def matches_any(rel_path: str, patterns) -> bool:
    return any(path_matches(rel_path, pat) for pat in (patterns or []))


# --- Frontmatter extraction -------------------------------------------------

def split_frontmatter(content: str) -> tuple[str | None, str]:
    """Return (raw_frontmatter_text, body).

    raw_frontmatter_text is None when the document has no leading ``---`` block.
    Only the leading block is treated as frontmatter (mirrors how Obsidian and
    python-frontmatter read it)."""
    # Tolerate a UTF-8 BOM on the first line.
    text = content.lstrip("﻿")
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None, content
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            raw = "\n".join(lines[1:i])
            body = "\n".join(lines[i + 1:])
            return raw, body
    # Opened but never closed -> not a valid frontmatter block.
    return None, content


_QUOTED = re.compile(r'"(?:[^"\\]|\\.)*"' + r"|'(?:[^'\\]|\\.)*'")


def find_bare_wikilinks(raw_fm: str) -> list[tuple[str, str]]:
    """Return (field, offending_line) for every line carrying a ``[[...]]`` that
    is NOT inside a quoted string (SYS_Schema §5 rule 1)."""
    offenders = []
    for line in raw_fm.split("\n"):
        without_quotes = _QUOTED.sub("", line)
        if "[[" in without_quotes:
            field = line.split(":", 1)[0].strip() if ":" in line else None
            offenders.append((field, line.strip()))
    return offenders


def _is_iso8601(value) -> bool:
    # YAML may already have parsed a date/datetime -- those are valid by construction.
    if isinstance(value, (datetime, date)):
        return True
    if not isinstance(value, str):
        return False
    s = value.strip()
    if not s:
        return False
    try:
        datetime.fromisoformat(s[:-1] + "+00:00" if s.endswith("Z") else s)
        return True
    except ValueError:
        return False


# --- The check sequence -----------------------------------------------------

def run_frontmatter_checks(path: str, content: str, vocab: Vocab) -> list[dict]:
    """Run checks 1-11 over the resulting file content. Returns ALL violations
    (M2 spec §5), ordered by check number (which is the severity order)."""
    import yaml

    errors: list[dict] = []
    raw_fm, _body = split_frontmatter(content)

    # Checks 1-2 are structural and always run (M2 spec §3).
    # 1. YAML parses strictly.
    parsed: dict | None = {}
    if raw_fm is not None:
        try:
            loaded = yaml.safe_load(raw_fm)
            parsed = loaded if isinstance(loaded, dict) else ({} if loaded is None else None)
            if parsed is None:
                errors.append(make_error(
                    "E001_YAML_PARSE", field=None, got="frontmatter is not a mapping",
                    hint="Frontmatter must be a YAML mapping of key: value pairs.",
                ))
        except yaml.YAMLError as e:
            parsed = None
            errors.append(make_error(
                "E001_YAML_PARSE", field=None, got=str(e).split("\n")[0],
                hint="Frontmatter must be strict YAML; quote any [[wikilinks]] (§5 rule 1).",
            ))

        # 2. No bare wikilinks in YAML (runs on raw text even if E001 failed).
        for field, line in find_bare_wikilinks(raw_fm):
            errors.append(make_error(
                "E002_BARE_WIKILINK", field=field, got=line, allowed=None,
                hint='Wrap wikilinks in quotes, e.g. contributors: ["[[@Chris]]"].',
            ))

    # Checks 3-11 need a parsed mapping. If YAML did not parse, stop here
    # (nothing downstream can run) but every collected error is still returned.
    if parsed is None:
        return errors
    if raw_fm is None:
        parsed = {}

    note_type = parsed.get("type")

    # 9 (evaluated early so it can claim retired values before 3/5/6). Legacy
    # field keys + retired values with a collapse-map replacement (§9).
    collapse_claimed: set = set()
    for legacy_key, why in vocab.legacy_fields.items():
        if legacy_key in parsed:
            errors.append(make_error(
                "E009_LEGACY_FIELD", field=legacy_key, got=parsed.get(legacy_key),
                allowed=None, hint=why,
            ))
    for field_name in ("type", "status", "ai_flags"):
        val = parsed.get(field_name)
        if isinstance(val, str) and val in vocab.collapse_map:
            collapse_claimed.add((field_name, val))
            errors.append(make_error(
                "E009_LEGACY_FIELD", field=field_name, got=val, allowed=None,
                hint=f"Retired value; map to {vocab.collapse_map[val]}.",
            ))

    # 3. type known (present-but-unknown; absence is reported by check 4).
    if note_type is not None and note_type not in vocab.types:
        if ("type", note_type) not in collapse_claimed:
            errors.append(make_error(
                "E003_TYPE_UNKNOWN", field="type", got=note_type,
                allowed=sorted(vocab.types),
            ))

    # 4. Core fields present.
    for field_name in vocab.core_fields:
        if parsed.get(field_name) in (None, ""):
            errors.append(make_error(
                "E004_CORE_FIELD_MISSING", field=field_name, got=None,
                allowed=list(vocab.core_fields),
            ))

    # 5. status legal.
    status = parsed.get("status")
    if status is not None and status not in vocab.statuses:
        if ("status", status) not in collapse_claimed:
            errors.append(make_error(
                "E005_STATUS_ILLEGAL", field="status", got=status,
                allowed=list(vocab.statuses),
                hint="Remap strays to the nearest lifecycle state (usually #draft or #approved).",
            ))

    # 6. ai_flags legal.
    ai_flags = parsed.get("ai_flags")
    if ai_flags is not None and ai_flags not in vocab.ai_flags:
        if ("ai_flags", ai_flags) not in collapse_claimed:
            errors.append(make_error(
                "E006_AI_FLAGS_ILLEGAL", field="ai_flags", got=ai_flags,
                allowed=list(vocab.ai_flags),
            ))

    # 7. Per-type required fields (only when the type is known).
    if note_type in vocab.types:
        for field_name in vocab.per_type_required.get(note_type, []):
            if parsed.get(field_name) in (None, ""):
                errors.append(make_error(
                    "E007_TYPE_FIELD_MISSING", field=field_name, got=None,
                    allowed=None,
                    hint=f"type '{note_type}' requires '{field_name}' (SYS_Schema §6).",
                ))

    # 8. Dates ISO 8601 (only the date fields that are present).
    for field_name in vocab.date_fields:
        if field_name in parsed and parsed.get(field_name) is not None:
            if not _is_iso8601(parsed.get(field_name)):
                errors.append(make_error(
                    "E008_DATE_FORMAT", field=field_name, got=str(parsed.get(field_name)),
                    allowed=None,
                    hint="Use ISO 8601, e.g. 2026-07-05 or 2026-07-05T09:00:00+10:00.",
                ))

    # 10. Path<->type coherence (only when the type is known).
    if note_type in vocab.types:
        errors.extend(run_path_type_check(path, note_type, vocab))

    # 11. Naming convention (zone-specific).
    errors.extend(run_naming_checks(path, parsed, vocab))

    return errors


def run_path_type_check(path: str, note_type: str, vocab: Vocab) -> list[dict]:
    """Check 10 in isolation (also used by vault_move). Assumes exemptions and
    the archive override have already been handled by the engine."""
    allowed_paths = vocab.path_type_table.get(note_type)
    if not allowed_paths:
        # Type has no declared home in the table -> cannot evaluate; do not block.
        return []
    if matches_any(path, allowed_paths):
        return []
    return [make_error(
        "E010_PATH_TYPE_MISMATCH", field="type", got=path, allowed=allowed_paths,
        hint=f"type '{note_type}' belongs under: {', '.join(allowed_paths)}.",
    )]


def run_naming_checks(path: str, parsed: dict, vocab: Vocab) -> list[dict]:
    """Check 11. Data-driven from vocab.naming_rules."""
    errors = []
    filename = path.rsplit("/", 1)[-1]
    note_type = parsed.get("type")
    for rule in vocab.naming_rules:
        if not path_matches(path, rule["path"]):
            continue
        if rule.get("if_type") and note_type != rule["if_type"]:
            continue
        if not re.search(rule["regex"], filename):
            errors.append(make_error(
                "E011_NAMING_CONVENTION", field="filename", got=filename,
                allowed=rule.get("description"),
                hint=rule.get("description"),
            ))
    return errors
