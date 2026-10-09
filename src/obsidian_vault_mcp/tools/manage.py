"""Management tools for the Obsidian vault MCP server."""

import logging

from ..serialization import dumps
from ..vault import list_directory, move_path, delete_path, read_file, resolve_vault_path
from ..write_events import fire_write
from ..write_guards import WriteRejected, run_write_guards

logger = logging.getLogger(__name__)


def vault_list(
    path: str = "",
    depth: int = 1,
    include_files: bool = True,
    include_dirs: bool = True,
    pattern: str | None = None,
) -> str:
    """List directory contents in the vault."""
    try:
        items = list_directory(
            path,
            depth=depth,
            include_files=include_files,
            include_dirs=include_dirs,
            pattern=pattern,
        )
        return dumps({"items": items, "total": len(items)})
    except ValueError as e:
        return dumps({"error": str(e)})
    except FileNotFoundError:
        return dumps({"error": f"Directory not found: {path}"})
    except Exception as e:
        logger.error(f"vault_list error: {e}")
        return dumps({"error": str(e)})


def _moved_content(source: str):
    """The text a move carries, for the write guards: a .md file's content, else None."""
    if not source.endswith(".md"):
        return None
    try:
        content, _ = read_file(source)
        return content
    except (FileNotFoundError, ValueError, UnicodeDecodeError):
        return None


def vault_move(source: str, destination: str, create_dirs: bool = True) -> str:
    """Move a file or directory within the vault."""
    try:
        resolve_vault_path(destination)  # validate before any guard sees the path
        run_write_guards("moved", destination, _moved_content(source))
        moved = move_path(source, destination, create_dirs=create_dirs)
        if moved:
            fire_write("moved", [source, destination])
        return dumps({"source": source, "destination": destination, "moved": moved})
    except WriteRejected as e:
        return dumps({**e.response(destination), "source": source, "destination": destination, "moved": False})
    except ValueError as e:
        return dumps({"error": str(e), "source": source, "destination": destination})
    except Exception as e:
        logger.error(f"vault_move error: {e}")
        return dumps({"error": str(e), "source": source, "destination": destination})


def vault_delete(path: str, confirm: bool = False) -> str:
    """Delete a file by moving it to .trash/ in the vault."""
    if not confirm:
        return dumps({
            "error": "Set confirm=true to execute deletion. Files are moved to .trash/, not hard deleted.",
            "path": path,
        })

    try:
        deleted = delete_path(path)
        if deleted:
            fire_write("deleted", [path])
        return dumps({"path": path, "deleted": deleted})
    except ValueError as e:
        return dumps({"error": str(e), "path": path})
    except Exception as e:
        logger.error(f"vault_delete error: {e}")
        return dumps({"error": str(e), "path": path})
