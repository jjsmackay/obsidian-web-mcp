"""End-to-end wiring tests: drive the real tool functions with the validator
active, and confirm block refuses (no disk change) while warn writes + warns.
"""

import json
from pathlib import Path

import pytest

import obsidian_vault_mcp.config as config
from obsidian_vault_mcp.validation import reset_validator
from obsidian_vault_mcp.tools import write as write_tools
from obsidian_vault_mcp.tools import manage as manage_tools

VOCAB_FILE = Path(__file__).resolve().parent.parent / "resources" / "SYS_Schema_Vocab.yaml"

VALID_ATOMIC = (
    "---\n"
    'contributors: ["[[@Chris]]"]\n'
    "created: 2026-07-05\n"
    "modified: 2026-07-05\n"
    "type: atomic\n"
    'status: "#draft"\n'
    'ai_flags: "#ai-ready"\n'
    "---\n\nBody.\n"
)
BAD_ATOMIC = VALID_ATOMIC.replace('"#draft"', '"#active"')  # E005
ATOMIC_PATH = "20_Knowledge/25_Atomic/idea.md"


@pytest.fixture
def vault(tmp_path, monkeypatch):
    v = tmp_path / "vault"
    v.mkdir()
    monkeypatch.setattr(config, "VAULT_PATH", v)
    monkeypatch.setattr(config, "VALIDATOR_ENABLED", True)
    monkeypatch.setattr(config, "VALIDATOR_VOCAB_PATH", str(VOCAB_FILE))
    monkeypatch.setattr(config, "VALIDATOR_LOG_PATH", str(tmp_path / "log.jsonl"))

    def _set_mode(mode):
        monkeypatch.setattr(config, "VALIDATOR_MODE", mode)
        reset_validator()
        return v

    reset_validator()
    yield _set_mode
    reset_validator()


def test_vault_write_block_refuses_and_does_not_write(vault):
    v = vault("block")
    resp = json.loads(write_tools.vault_write(ATOMIC_PATH, BAD_ATOMIC))
    assert "errors" in resp and resp["mode"] == "block"
    assert not (v / ATOMIC_PATH).exists()  # nothing written


def test_vault_write_warn_writes_with_warnings(vault):
    v = vault("warn")
    resp = json.loads(write_tools.vault_write(ATOMIC_PATH, BAD_ATOMIC))
    assert resp["created"] is True
    assert "warnings" in resp and resp["mode"] == "warn"
    assert (v / ATOMIC_PATH).exists()  # written anyway


def test_vault_write_valid_note_writes_clean(vault):
    v = vault("block")
    resp = json.loads(write_tools.vault_write(ATOMIC_PATH, VALID_ATOMIC))
    assert resp["created"] is True and "warnings" not in resp
    assert (v / ATOMIC_PATH).exists()


def test_batch_frontmatter_partial_success(vault):
    v = vault("block")
    (v / "20_Knowledge/25_Atomic").mkdir(parents=True)
    (v / ATOMIC_PATH).write_text(VALID_ATOMIC)
    other = "20_Knowledge/25_Atomic/other.md"
    (v / other).write_text(VALID_ATOMIC)

    resp = json.loads(write_tools.vault_batch_frontmatter_update([
        {"path": ATOMIC_PATH, "fields": {"modified": "2026-07-06"}},   # stays valid
        {"path": other, "fields": {"status": "#active"}},              # becomes illegal
    ]))
    results = {r["path"]: r for r in resp["results"]}
    assert results[ATOMIC_PATH]["updated"] is True
    assert results[other]["updated"] is False
    assert "E005_STATUS_ILLEGAL" in {e["code"] for e in results[other]["errors"]}


def test_vault_patch_frontmatter_blocked_body_allowed(vault):
    v = vault("block")
    (v / "20_Knowledge/25_Atomic").mkdir(parents=True)
    (v / ATOMIC_PATH).write_text(VALID_ATOMIC)

    # frontmatter patch to an illegal status -> blocked, file unchanged
    resp = json.loads(write_tools.vault_patch(ATOMIC_PATH, '"#draft"', '"#active"'))
    assert "errors" in resp and resp.get("patched") is not True
    assert '"#draft"' in (v / ATOMIC_PATH).read_text()

    # body-only patch -> not gated even in block mode
    resp2 = json.loads(write_tools.vault_patch(ATOMIC_PATH, "Body.", "Edited body."))
    assert resp2["patched"] is True
    assert "Edited body." in (v / ATOMIC_PATH).read_text()


def test_vault_append_new_file_blocked_existing_allowed(vault):
    v = vault("block")
    # new file via create_if_missing with bad frontmatter -> blocked
    resp = json.loads(write_tools.vault_append(ATOMIC_PATH, BAD_ATOMIC, create_if_missing=True))
    assert "errors" in resp and resp.get("appended") is not True
    assert not (v / ATOMIC_PATH).exists()

    # append to an existing (already non-conforming) file is body-only -> allowed
    (v / "20_Knowledge/25_Atomic").mkdir(parents=True)
    (v / ATOMIC_PATH).write_text(BAD_ATOMIC)
    resp2 = json.loads(write_tools.vault_append(ATOMIC_PATH, "extra line"))
    assert resp2["appended"] is True
    assert "extra line" in (v / ATOMIC_PATH).read_text()


def test_vault_move_blocked_and_source_preserved(vault):
    v = vault("block")
    (v / "20_Knowledge/25_Atomic").mkdir(parents=True)
    (v / ATOMIC_PATH).write_text(VALID_ATOMIC)

    resp = json.loads(manage_tools.vault_move(ATOMIC_PATH, "10_Projects/moved.md"))
    assert "errors" in resp and resp["moved"] is False
    assert (v / ATOMIC_PATH).exists()          # source untouched
    assert not (v / "10_Projects/moved.md").exists()


def test_vault_move_legal_destination_succeeds(vault):
    v = vault("block")
    (v / "20_Knowledge/25_Atomic").mkdir(parents=True)
    (v / ATOMIC_PATH).write_text(VALID_ATOMIC)

    resp = json.loads(manage_tools.vault_move(ATOMIC_PATH, "20_Knowledge/25_Atomic/renamed.md"))
    assert resp["moved"] is True
    assert (v / "20_Knowledge/25_Atomic/renamed.md").exists()


def test_disabled_validator_is_transparent(vault, monkeypatch):
    v = vault("block")
    monkeypatch.setattr(config, "VALIDATOR_ENABLED", False)
    reset_validator()
    resp = json.loads(write_tools.vault_write(ATOMIC_PATH, BAD_ATOMIC))
    assert resp["created"] is True and "errors" not in resp  # kill-switch honoured
