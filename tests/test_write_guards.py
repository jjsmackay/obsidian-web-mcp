"""Tests for the write-guard seam (register_write_guard + run_write_guards + WriteRejected).

The pre-write counterpart of the write-event seam: a process-global registry that is a
true no-op with zero guards, consults guards in registration order, lets a guard refuse
by raising WriteRejected, and FAILS CLOSED when a guard crashes.
"""

import pytest

from obsidian_vault_mcp import write_guards
from obsidian_vault_mcp.write_guards import WriteRejected


@pytest.fixture(autouse=True)
def _clear_guards():
    """Each test starts with an empty registry (it is process-global)."""
    write_guards._write_guards.clear()
    yield
    write_guards._write_guards.clear()


def test_run_with_no_guards_is_a_noop():
    assert not write_guards.has_write_guards()
    write_guards.run_write_guards("created", "a.md", "body")  # must not raise


def test_guard_receives_operation_path_and_content():
    seen = []
    write_guards.register_write_guard(lambda op, path, content: seen.append((op, path, content)))

    write_guards.run_write_guards("updated", "note.md", "new text")

    assert write_guards.has_write_guards()
    assert seen == [("updated", "note.md", "new text")]


def test_guards_run_in_registration_order_and_first_refusal_wins():
    order = []

    def first(op, path, content):
        order.append("first")

    def second(op, path, content):
        order.append("second")
        raise WriteRejected({"errors": ["nope"]})

    def third(op, path, content):
        order.append("third")

    for g in (first, second, third):
        write_guards.register_write_guard(g)

    with pytest.raises(WriteRejected) as exc:
        write_guards.run_write_guards("created", "x.md", "")

    assert order == ["first", "second"]
    assert exc.value.payload == {"errors": ["nope"]}


def test_crashing_guard_fails_closed(caplog):
    def broken(op, path, content):
        raise RuntimeError("boom")

    write_guards.register_write_guard(broken)

    with pytest.raises(WriteRejected) as exc:
        write_guards.run_write_guards("created", "x.md", "")

    assert exc.value.payload == {"reason": "write guard failed: RuntimeError"}
    assert isinstance(exc.value.__cause__, RuntimeError)
    assert "boom" in caplog.text


def test_rejected_response_shape():
    exc = WriteRejected({"errors": [{"code": "E1"}], "mode": "block"})
    assert exc.response("a.md") == {
        "error": "write rejected by guard",
        "path": "a.md",
        "rejected": True,
        "errors": [{"code": "E1"}],
        "mode": "block",
    }


def test_rejected_accepts_a_plain_reason():
    exc = WriteRejected("protected note")
    assert str(exc) == "protected note"
    assert exc.response("a.md")["reason"] == "protected note"
    assert WriteRejected().payload == {}
