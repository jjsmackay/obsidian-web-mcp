"""Tests for the L2 write-boundary validator (M2 spec §10.6 acceptance).

Covers: one pass + one fail per error code E001-E011, the 60_Archive override,
an exempt path, a vault_move coherence case, warn-vs-block behaviour, and the
unparseable-schema fallback. Unit tests drive the Validator directly against the
shipped resources/SYS_Schema_Vocab.yaml; integration tests drive the real tool
functions end-to-end.
"""

import json
from pathlib import Path

import pytest

from obsidian_vault_mcp.validation import Validator
from obsidian_vault_mcp.validation.vocab import Vocab, VocabStore

VOCAB_FILE = Path(__file__).resolve().parent.parent / "resources" / "SYS_Schema_Vocab.yaml"

VALID = {
    "contributors": '["[[@Chris]]"]',
    "created": "2026-07-05",
    "modified": "2026-07-05",
    "type": "atomic",
    "status": '"#draft"',
    "ai_flags": '"#ai-ready"',
}
ATOMIC_PATH = "20_Knowledge/25_Atomic/an-idea.md"


def note(fields: dict, body: str = "Body.\n") -> str:
    lines = ["---"]
    for k, v in fields.items():
        lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append("")
    lines.append(body)
    return "\n".join(lines)


@pytest.fixture
def make_validator(tmp_path):
    def _make(mode="block", vocab_file=VOCAB_FILE):
        return Validator(mode=mode, vocab_file=Path(vocab_file), log_file=tmp_path / "log.jsonl")
    return _make


def codes(outcome):
    return {e["code"] for e in outcome.errors}


# --- baseline pass ----------------------------------------------------------

def test_valid_note_passes(make_validator):
    v = make_validator()
    outcome = v.validate_write(ATOMIC_PATH, note(VALID), tool="vault_write")
    assert not outcome.has_violations, outcome.errors
    assert not outcome.blocked


# --- one pass + one fail per error code ------------------------------------

def test_e001_yaml_parse(make_validator):
    v = make_validator()
    bad = note({**VALID, "extra": "[1, 2"})  # unterminated flow sequence
    assert "E001_YAML_PARSE" in codes(v.validate_write("x.md", bad, tool="t"))
    assert "E001_YAML_PARSE" not in codes(v.validate_write(ATOMIC_PATH, note(VALID), tool="t"))


def test_e002_bare_wikilink(make_validator):
    v = make_validator()
    bad = note({**VALID, "contributors": "[[@Chris]]"})  # unquoted wikilink
    assert "E002_BARE_WIKILINK" in codes(v.validate_write(ATOMIC_PATH, bad, tool="t"))
    assert "E002_BARE_WIKILINK" not in codes(v.validate_write(ATOMIC_PATH, note(VALID), tool="t"))


def test_e003_type_unknown(make_validator):
    v = make_validator()
    bad = note({**VALID, "type": "widget"})
    assert "E003_TYPE_UNKNOWN" in codes(v.validate_write(ATOMIC_PATH, bad, tool="t"))
    assert "E003_TYPE_UNKNOWN" not in codes(v.validate_write(ATOMIC_PATH, note(VALID), tool="t"))


def test_e004_core_field_missing(make_validator):
    v = make_validator()
    missing = dict(VALID)
    del missing["status"]
    out = v.validate_write(ATOMIC_PATH, note(missing), tool="t")
    assert "E004_CORE_FIELD_MISSING" in codes(out)
    assert any(e["field"] == "status" for e in out.errors)
    assert "E004_CORE_FIELD_MISSING" not in codes(v.validate_write(ATOMIC_PATH, note(VALID), tool="t"))


def test_e005_status_illegal(make_validator):
    v = make_validator()
    bad = note({**VALID, "status": '"#active"'})
    assert "E005_STATUS_ILLEGAL" in codes(v.validate_write(ATOMIC_PATH, bad, tool="t"))
    assert "E005_STATUS_ILLEGAL" not in codes(v.validate_write(ATOMIC_PATH, note(VALID), tool="t"))


def test_e006_ai_flags_illegal(make_validator):
    v = make_validator()
    bad = note({**VALID, "ai_flags": '"#ai-maybe"'})
    assert "E006_AI_FLAGS_ILLEGAL" in codes(v.validate_write(ATOMIC_PATH, bad, tool="t"))
    assert "E006_AI_FLAGS_ILLEGAL" not in codes(v.validate_write(ATOMIC_PATH, note(VALID), tool="t"))


def test_e007_type_field_missing(make_validator):
    v = make_validator()
    # meeting requires `date`; supply a valid meeting except for date.
    path = "50_Meetings/2026-07-05_standup.md"
    base = {**VALID, "type": "meeting"}
    without_date = v.validate_write(path, note(base), tool="t")
    assert "E007_TYPE_FIELD_MISSING" in codes(without_date)
    with_date = v.validate_write(path, note({**base, "date": "2026-07-05"}), tool="t")
    assert "E007_TYPE_FIELD_MISSING" not in codes(with_date)


def test_e007_ratified_optional_fields(make_validator):
    # Ratified 2026-07-05: people and tool have NO required fields beyond the
    # core; a note with only core fields must not trip E007.
    v = make_validator()
    people = v.validate_write("20_Knowledge/21_People/jane.md", note({**VALID, "type": "people"}), tool="t")
    assert "E007_TYPE_FIELD_MISSING" not in codes(people)
    tool = v.validate_write("20_Knowledge/23_Tools/thing.md", note({**VALID, "type": "tool"}), tool="t")
    assert "E007_TYPE_FIELD_MISSING" not in codes(tool)
    # but a resource with no resource_kind still fails (ratified required)
    res = v.validate_write("30_Resources/doc.md", note({**VALID, "type": "resource"}), tool="t")
    assert "E007_TYPE_FIELD_MISSING" in codes(res)


def test_e008_date_format(make_validator):
    v = make_validator()
    bad = note({**VALID, "created": '"not-a-date"'})
    assert "E008_DATE_FORMAT" in codes(v.validate_write(ATOMIC_PATH, bad, tool="t"))
    assert "E008_DATE_FORMAT" not in codes(v.validate_write(ATOMIC_PATH, note(VALID), tool="t"))


def test_e009_legacy_field(make_validator):
    v = make_validator()
    bad = note({**VALID, "last_modified": "2026-07-05"})
    out = v.validate_write(ATOMIC_PATH, bad, tool="t")
    assert "E009_LEGACY_FIELD" in codes(out)
    assert "E009_LEGACY_FIELD" not in codes(v.validate_write(ATOMIC_PATH, note(VALID), tool="t"))


def test_e009_retired_value_takes_precedence_over_e003(make_validator):
    # `person` is a retired type value (-> people). It should surface as E009
    # with a remap hint, NOT as a bare E003_TYPE_UNKNOWN.
    v = make_validator()
    out = v.validate_write("20_Knowledge/21_People/jane.md", note({**VALID, "type": "person"}), tool="t")
    assert "E009_LEGACY_FIELD" in codes(out)
    assert "E003_TYPE_UNKNOWN" not in codes(out)
    assert any("people" in (e.get("hint") or "") for e in out.errors)


def test_e010_path_type_mismatch(make_validator):
    v = make_validator()
    bad = v.validate_write("10_Projects/misplaced.md", note(VALID), tool="t")  # atomic under Projects
    assert "E010_PATH_TYPE_MISMATCH" in codes(bad)
    assert "E010_PATH_TYPE_MISMATCH" not in codes(v.validate_write(ATOMIC_PATH, note(VALID), tool="t"))


def test_e011_naming_convention(make_validator):
    v = make_validator()
    meeting = {**VALID, "type": "meeting", "date": "2026-07-05"}
    bad = v.validate_write("50_Meetings/standup.md", note(meeting), tool="t")  # no YYYY-MM-DD_
    assert "E011_NAMING_CONVENTION" in codes(bad)
    good = v.validate_write("50_Meetings/2026-07-05_standup.md", note(meeting), tool="t")
    assert "E011_NAMING_CONVENTION" not in codes(good)


# --- overrides & exemptions -------------------------------------------------

def test_archive_override_suppresses_e010_only(make_validator):
    v = make_validator()
    # atomic under 60_Archive: E010 suppressed, but other checks still apply.
    out = v.validate_write("60_Archive/old-idea.md", note(VALID), tool="t")
    assert "E010_PATH_TYPE_MISMATCH" not in codes(out)
    assert not out.has_violations  # otherwise valid
    # a genuinely broken archived note still fails on non-path checks
    broken = v.validate_write("60_Archive/x.md", note({**VALID, "status": '"#active"'}), tool="t")
    assert "E005_STATUS_ILLEGAL" in codes(broken)


def test_exempt_path_skips_all_checks(make_validator):
    v = make_validator()
    garbage = note({"type": "widget", "status": '"#nope"'})  # would fail hard elsewhere
    out = v.validate_write("00_Shotgun/capture.md", garbage, tool="t")
    assert out.exempt
    assert not out.has_violations


def test_non_markdown_is_exempt(make_validator):
    v = make_validator()
    out = v.validate_write("90_System/99_Graph/data.json", "{}", tool="t")
    assert out.exempt


# --- vault_move coherence ---------------------------------------------------

def test_vault_move_coherence_blocks_illegal_destination(make_validator):
    v = make_validator(mode="block")
    src = note(VALID)  # type: atomic
    out = v.validate_move(src, "10_Projects/moved.md", tool="vault_move")
    assert out.blocked
    assert "E010_PATH_TYPE_MISMATCH" in codes(out)


def test_vault_move_coherence_allows_legal_destination(make_validator):
    v = make_validator(mode="block")
    out = v.validate_move(note(VALID), "20_Knowledge/25_Atomic/moved.md", tool="vault_move")
    assert not out.has_violations


# --- warn vs block ----------------------------------------------------------

def test_block_mode_blocks(make_validator):
    v = make_validator(mode="block")
    out = v.validate_write(ATOMIC_PATH, note({**VALID, "status": '"#active"'}), tool="t")
    assert out.blocked
    assert out.errors[0]["mode"] == "block"


def test_warn_mode_writes_with_warnings(make_validator):
    v = make_validator(mode="warn")
    out = v.validate_write(ATOMIC_PATH, note({**VALID, "status": '"#active"'}), tool="t")
    assert not out.blocked
    assert out.has_violations
    assert out.errors[0]["mode"] == "warn"


# --- violation log written in both modes ------------------------------------

@pytest.mark.parametrize("mode", ["warn", "block"])
def test_violation_log_written(make_validator, tmp_path, mode):
    v = make_validator(mode=mode)
    v.validate_write(ATOMIC_PATH, note({**VALID, "status": '"#active"'}), tool="vault_write")
    log = tmp_path / "log.jsonl"
    assert log.exists()
    line = json.loads(log.read_text().strip().splitlines()[-1])
    assert line["mode"] == mode
    assert "E005_STATUS_ILLEGAL" in line["codes"]
    assert line["written"] is (mode == "warn")


# --- unparseable-schema fallback (never fail open) --------------------------

def test_unparseable_schema_keeps_last_good(tmp_path):
    vocab_copy = tmp_path / "vocab.yaml"
    vocab_copy.write_text(VOCAB_FILE.read_text())
    v = Validator(mode="block", vocab_file=vocab_copy, log_file=tmp_path / "log.jsonl")
    # prime last-good
    assert not v.validate_write(ATOMIC_PATH, note(VALID), tool="t").has_violations
    # corrupt the file
    vocab_copy.write_text("::: not yaml :::\n\t- broken")
    # validation still runs on last-good vocab, not fail-open
    out = v.validate_write(ATOMIC_PATH, note({**VALID, "status": '"#active"'}), tool="t")
    assert "E005_STATUS_ILLEGAL" in codes(out)


def test_no_vocab_block_mode_fails_closed(tmp_path):
    bad_vocab = tmp_path / "vocab.yaml"
    bad_vocab.write_text("::: not yaml :::")
    v = Validator(mode="block", vocab_file=bad_vocab, log_file=tmp_path / "log.jsonl")
    out = v.validate_write(ATOMIC_PATH, note(VALID), tool="t")
    assert out.blocked  # never fail open
    assert "E000_VOCAB_UNAVAILABLE" in codes(out)


def test_no_vocab_warn_mode_writes_but_warns(tmp_path):
    bad_vocab = tmp_path / "vocab.yaml"
    bad_vocab.write_text("::: not yaml :::")
    v = Validator(mode="warn", vocab_file=bad_vocab, log_file=tmp_path / "log.jsonl")
    out = v.validate_write(ATOMIC_PATH, note(VALID), tool="t")
    assert not out.blocked
    assert "E000_VOCAB_UNAVAILABLE" in codes(out)


def test_reload_returns_true_on_good_file(tmp_path):
    vocab_copy = tmp_path / "vocab.yaml"
    vocab_copy.write_text(VOCAB_FILE.read_text())
    v = Validator(mode="block", vocab_file=vocab_copy, log_file=tmp_path / "log.jsonl")
    assert v.reload() is True
