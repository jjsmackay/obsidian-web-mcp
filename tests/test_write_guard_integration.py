"""The core mutation tools consult write guards BEFORE touching disk.

Semantics under test: a guard sees (operation, path, resulting content); a refusal
returns the tool's error shape with the guard's payload merged in and leaves the file
untouched; a refused write fires no write event; a batch reports the refusal per file
and still writes the others; a move passes the destination path and the source text;
vault_edit does not consult guards on a dry run or a no-change edit; with no guards
registered every tool behaves exactly as before.
"""

import base64
import json

import pytest

from obsidian_vault_mcp import write_events, write_guards
from obsidian_vault_mcp.write_guards import WriteRejected
from obsidian_vault_mcp.tools.canvas import vault_canvas_add_edge, vault_canvas_add_node
from obsidian_vault_mcp.tools.daily import vault_daily_note_append
from obsidian_vault_mcp.tools.manage import vault_move
from obsidian_vault_mcp.tools.write import (
    vault_append,
    vault_batch_frontmatter_update,
    vault_edit,
    vault_write,
    vault_write_binary,
)

PNG_1X1 = base64.b64encode(
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
).decode()


@pytest.fixture
def seen():
    """Record every (operation, path, content) a guard is consulted with; isolate the registry."""
    write_guards._write_guards.clear()
    calls = []
    write_guards.register_write_guard(lambda op, path, content: calls.append((op, path, content)))
    yield calls
    write_guards._write_guards.clear()


@pytest.fixture
def refuse_all():
    """A guard that refuses everything with a recognisable payload; plus a write-event capture."""
    write_guards._write_guards.clear()
    write_events._write_listeners.clear()

    def guard(op, path, content):
        raise WriteRejected({"errors": [{"code": "E_TEST", "op": op}], "mode": "block"})

    write_guards.register_write_guard(guard)
    events = []
    write_events.register_write_listener(lambda op, paths: events.append((op, paths)))
    yield events
    write_guards._write_guards.clear()
    write_events._write_listeners.clear()


# --- what a guard sees ---

def test_vault_write_new_file_is_created_with_content(vault_dir, seen):
    vault_write("fresh.md", "hello")
    assert seen == [("created", "fresh.md", "hello")]


def test_vault_write_existing_file_is_updated(vault_dir, seen):
    vault_write("test-note.md", "changed")
    assert seen == [("updated", "test-note.md", "changed")]


def test_vault_write_merge_frontmatter_passes_merged_content(vault_dir, seen):
    vault_write("test-note.md", "---\nextra: 1\n---\n\nnew body\n", merge_frontmatter=True)
    op, path, content = seen[0]
    assert (op, path) == ("updated", "test-note.md")
    assert "extra: 1" in content and "status: active" in content and "new body" in content
    assert content == (vault_dir / "test-note.md").read_text()


def test_vault_edit_passes_resulting_content(vault_dir, seen):
    vault_edit("test-note.md", [{"old_text": "test note", "new_text": "edited note"}])
    op, path, content = seen[0]
    assert (op, path) == ("updated", "test-note.md")
    assert "edited note" in content and "test note" not in content


def test_vault_edit_dry_run_and_no_change_do_not_consult_guards(vault_dir, seen):
    vault_edit("test-note.md", [{"old_text": "test note", "new_text": "x"}], dry_run=True)
    vault_edit("test-note.md", [{"old_text": "test note", "new_text": "test note"}])
    assert seen == []


def test_vault_append_passes_full_new_content(vault_dir, seen):
    vault_append("test-note.md", "tail", separator="\n")
    op, path, content = seen[0]
    assert (op, path) == ("updated", "test-note.md")
    assert content.endswith("\ntail")
    vault_append("brand-new.md", "first")
    assert seen[1] == ("created", "brand-new.md", "first")


def test_vault_batch_consults_per_file(vault_dir, seen):
    vault_batch_frontmatter_update([
        {"path": "test-note.md", "fields": {"status": "done"}},
        {"path": "subfolder/nested-note.md", "fields": {"status": "done"}},
    ])
    assert [(op, path) for op, path, _ in seen] == [
        ("updated", "test-note.md"),
        ("updated", "subfolder/nested-note.md"),
    ]
    assert all("status: done" in content for _, _, content in seen)


def test_vault_move_passes_destination_and_source_text(vault_dir, seen):
    vault_move("test-note.md", "moved/test-note.md")
    op, path, content = seen[0]
    assert (op, path) == ("moved", "moved/test-note.md")
    assert "This is a test note" in content


def test_vault_move_of_non_markdown_passes_none(vault_dir, seen):
    (vault_dir / "pic.png").write_bytes(b"\x89PNG")
    vault_move("pic.png", "img/pic.png")
    assert seen == [("moved", "img/pic.png", None)]


def test_vault_write_binary_passes_bytes(vault_dir, seen):
    vault_write_binary("img/a.png", PNG_1X1, "image/png")
    op, path, content = seen[0]
    assert (op, path) == ("created", "img/a.png")
    assert isinstance(content, bytes) and content.startswith(b"\x89PNG")


def test_canvas_tools_pass_serialised_canvas(vault_dir, seen):
    vault_canvas_add_node("board.canvas", {"type": "text", "text": "hi", "x": 0, "y": 0, "width": 10, "height": 10})
    assert seen[0][:2] == ("created", "board.canvas")
    assert json.loads(seen[0][2])["nodes"][0]["text"] == "hi"
    node_id = json.loads(seen[0][2])["nodes"][0]["id"]
    vault_canvas_add_edge("board.canvas", {"fromNode": node_id, "toNode": node_id})
    assert seen[1][:2] == ("updated", "board.canvas")
    assert len(json.loads(seen[1][2])["edges"]) == 1


def test_daily_note_append_goes_through_vault_append(vault_dir, seen):
    vault_daily_note_append("today's note")
    assert len(seen) == 1 and seen[0][0] == "created" and "today's note" in seen[0][2]


# --- a refusal ---

def _assert_rejected(resp, op):
    assert resp["error"] == "write rejected by guard"
    assert resp["rejected"] is True
    assert resp["mode"] == "block"
    assert resp["errors"] == [{"code": "E_TEST", "op": op}]


def test_refused_vault_write_does_not_touch_disk_or_fire(vault_dir, refuse_all):
    before = (vault_dir / "test-note.md").read_text()
    resp = json.loads(vault_write("test-note.md", "changed"))
    _assert_rejected(resp, "updated")
    assert resp["created"] is False
    assert (vault_dir / "test-note.md").read_text() == before

    resp = json.loads(vault_write("new.md", "x"))
    _assert_rejected(resp, "created")
    assert not (vault_dir / "new.md").exists()
    assert refuse_all == []


def test_refused_vault_edit_keeps_edit_shape(vault_dir, refuse_all):
    resp = json.loads(vault_edit("test-note.md", [{"old_text": "test note", "new_text": "x"}]))
    _assert_rejected(resp, "updated")
    assert resp["changed"] is False and resp["dry_run"] is False and resp["edits_applied"] == 0
    assert "test note" in (vault_dir / "test-note.md").read_text()


def test_refused_vault_append_keeps_append_shape(vault_dir, refuse_all):
    resp = json.loads(vault_append("test-note.md", "tail"))
    _assert_rejected(resp, "updated")
    assert resp["changed"] is False and resp["appended"] is False
    assert "tail" not in (vault_dir / "test-note.md").read_text()


def test_refused_batch_reports_per_file(vault_dir, refuse_all):
    resp = json.loads(vault_batch_frontmatter_update([{"path": "test-note.md", "fields": {"a": 1}}]))
    item = resp["results"][0]
    _assert_rejected(item, "updated")
    assert item["updated"] is False and item["path"] == "test-note.md"
    assert "a: 1" not in (vault_dir / "test-note.md").read_text()
    assert refuse_all == []


def test_batch_refusal_of_one_file_still_writes_the_others(vault_dir):
    write_guards._write_guards.clear()

    def guard(op, path, content):
        if path == "test-note.md":
            raise WriteRejected("not this one")

    write_guards.register_write_guard(guard)
    try:
        resp = json.loads(vault_batch_frontmatter_update([
            {"path": "test-note.md", "fields": {"a": 1}},
            {"path": "subfolder/nested-note.md", "fields": {"a": 1}},
        ]))
    finally:
        write_guards._write_guards.clear()

    first, second = resp["results"]
    assert first["updated"] is False and first["reason"] == "not this one"
    assert second["updated"] is True
    assert "a: 1" in (vault_dir / "subfolder/nested-note.md").read_text()


def test_refused_vault_move_leaves_source_in_place(vault_dir, refuse_all):
    resp = json.loads(vault_move("test-note.md", "elsewhere/test-note.md"))
    _assert_rejected(resp, "moved")
    assert resp["moved"] is False and resp["source"] == "test-note.md"
    assert (vault_dir / "test-note.md").exists()
    assert not (vault_dir / "elsewhere/test-note.md").exists()
    assert refuse_all == []


def test_refused_binary_write_and_canvas(vault_dir, refuse_all):
    resp = json.loads(vault_write_binary("img/a.png", PNG_1X1, "image/png"))
    _assert_rejected(resp, "created")
    assert resp["media_type"] == "image/png"
    assert not (vault_dir / "img/a.png").exists()

    resp = json.loads(vault_canvas_add_node("b.canvas", {"type": "text", "text": "hi", "x": 0, "y": 0, "width": 1, "height": 1}))
    _assert_rejected(resp, "created")
    assert not (vault_dir / "b.canvas").exists()


def test_crashing_guard_refuses_the_write(vault_dir):
    write_guards._write_guards.clear()
    write_guards.register_write_guard(lambda op, path, content: 1 / 0)
    try:
        resp = json.loads(vault_write("new.md", "x"))
    finally:
        write_guards._write_guards.clear()

    assert resp["rejected"] is True
    assert resp["reason"] == "write guard failed: ZeroDivisionError"
    assert not (vault_dir / "new.md").exists()


# --- stock server ---

def test_no_guards_is_byte_identical(vault_dir):
    write_guards._write_guards.clear()
    resp = json.loads(vault_write("new.md", "x"))
    assert resp == {"path": "new.md", "created": True, "size": 1}
