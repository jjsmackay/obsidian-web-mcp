"""Management tools for the Obsidian vault MCP server."""

import json
import logging

from ..vault import list_directory, move_path, delete_path, read_file, resolve_vault_path
from ..validation import validate_move

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
        return json.dumps({"items": items, "total": len(items)})
    except ValueError as e:
        return json.dumps({"error": str(e)})
    except FileNotFoundError:
        return json.dumps({"error": f"Directory not found: {path}"})
    except Exception as e:
        logger.error(f"vault_list error: {e}")
        return json.dumps({"error": str(e)})


def vault_move(source: str, destination: str, create_dirs: bool = True) -> str:
    """Move a file or directory within the vault."""
    try:
        # L2 path<->type coherence against the destination (M2 spec §2).
        # Content is untouched, so only check 10 runs. Directory moves and
        # unreadable sources are skipped (nothing to coordinate).
        outcome = None
        if destination.endswith(".md"):
            try:
                source_content, _ = read_file(source)
                outcome = validate_move(source_content, destination, tool="vault_move")
            except (FileNotFoundError, ValueError):
                outcome = None
        if outcome is not None and outcome.blocked:
            return json.dumps({
                "errors": outcome.errors, "mode": outcome.mode, "moved": False,
                "source": source, "destination": destination,
            })

        moved = move_path(source, destination, create_dirs=create_dirs)
        response = {"source": source, "destination": destination, "moved": moved}
        if outcome is not None and outcome.has_violations:
            response["warnings"] = outcome.errors
            response["mode"] = outcome.mode
        return json.dumps(response)
    except ValueError as e:
        return json.dumps({"error": str(e), "source": source, "destination": destination})
    except Exception as e:
        logger.error(f"vault_move error: {e}")
        return json.dumps({"error": str(e), "source": source, "destination": destination})


def vault_delete(path: str, confirm: bool = False) -> str:
    """Delete a file by moving it to .trash/ in the vault."""
    if not confirm:
        return json.dumps({
            "error": "Set confirm=true to execute deletion. Files are moved to .trash/, not hard deleted.",
            "path": path,
        })

    try:
        deleted = delete_path(path)
        return json.dumps({"path": path, "deleted": deleted})
    except ValueError as e:
        return json.dumps({"error": str(e), "path": path})
    except Exception as e:
        logger.error(f"vault_delete error: {e}")
        return json.dumps({"error": str(e), "path": path})
