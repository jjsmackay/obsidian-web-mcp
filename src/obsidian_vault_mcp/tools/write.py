"""Write tools for the Obsidian vault MCP server."""

import json
import logging

import frontmatter

from ..vault import resolve_vault_path, read_file, write_file_atomic
from ..validation import validate_write
from ..validation.checks import split_frontmatter

logger = logging.getLogger(__name__)


def _rejection(fields: dict, outcome) -> str:
    """Block-mode response: the M2 §5 errors array instead of a write."""
    return json.dumps({"errors": outcome.errors, "mode": outcome.mode, **fields})


def _with_warnings(response: dict, outcome) -> dict:
    """Warn-mode: attach the same objects under 'warnings' (M2 §5)."""
    if outcome is not None and outcome.has_violations:
        response["warnings"] = outcome.errors
        response["mode"] = outcome.mode
    return response


def vault_write(path: str, content: str, create_dirs: bool = True, merge_frontmatter: bool = False) -> str:
    """Write a file to the vault, optionally merging frontmatter with existing content."""
    try:
        resolve_vault_path(path)

        if merge_frontmatter:
            try:
                existing_content, _ = read_file(path)
                existing_post = frontmatter.loads(existing_content)
                new_post = frontmatter.loads(content)

                merged_meta = dict(existing_post.metadata)
                merged_meta.update(new_post.metadata)

                new_post.metadata = merged_meta
                content = frontmatter.dumps(new_post)
            except FileNotFoundError:
                pass
            except Exception as e:
                logger.warning(f"Frontmatter merge failed for {path}, writing as-is: {e}")

        # L2 write-boundary validation on the resulting file (M2 spec §2, §3).
        outcome = validate_write(path, content, tool="vault_write")
        if outcome.blocked:
            return _rejection({"path": path, "created": False}, outcome)

        is_new, size = write_file_atomic(path, content, create_dirs=create_dirs)

        return json.dumps(_with_warnings(
            {"path": path, "created": is_new, "size": size}, outcome))
    except ValueError as e:
        return json.dumps({"error": str(e), "path": path})
    except Exception as e:
        logger.error(f"vault_write error for {path}: {e}")
        return json.dumps({"error": str(e), "path": path})


def vault_batch_frontmatter_update(updates: list[dict]) -> str:
    """Update frontmatter fields on multiple files without changing body content."""
    results = []

    for update in updates:
        file_path = update.get("path", "")
        fields = update.get("fields", {})

        try:
            content, _ = read_file(file_path)
            post = frontmatter.loads(content)

            for key, value in fields.items():
                post.metadata[key] = value

            new_content = frontmatter.dumps(post)

            # Full check per file on the resulting state; apply passes, reject
            # failures, report all (M2 spec §2).
            outcome = validate_write(file_path, new_content, tool="vault_batch_frontmatter_update")
            if outcome.blocked:
                results.append({
                    "path": file_path, "updated": False,
                    "errors": outcome.errors, "mode": outcome.mode,
                })
                continue

            write_file_atomic(file_path, new_content, create_dirs=False)

            results.append(_with_warnings({"path": file_path, "updated": True}, outcome))
        except FileNotFoundError:
            results.append({"path": file_path, "updated": False, "error": "File not found"})
        except ValueError as e:
            results.append({"path": file_path, "updated": False, "error": str(e)})
        except Exception as e:
            results.append({"path": file_path, "updated": False, "error": str(e)})

    return json.dumps({"results": results})


def vault_patch(path: str, old_text: str, new_text: str) -> str:
    """Replace a unique text occurrence in a file. Fails if old_text is not found or matches multiple times."""
    try:
        content, _ = read_file(path)
        count = content.count(old_text)
        if count == 0:
            return json.dumps({"error": "old_text not found in file", "path": path})
        if count > 1:
            return json.dumps({
                "error": f"old_text matches {count} times — provide more context to make it unique",
                "path": path,
            })
        new_content = content.replace(old_text, new_text, 1)

        # Gate only when the patch touches frontmatter (M2 spec §2); body-only
        # patches are not checked.
        if split_frontmatter(content)[0] != split_frontmatter(new_content)[0]:
            outcome = validate_write(path, new_content, tool="vault_patch")
            if outcome.blocked:
                return _rejection({"path": path, "patched": False}, outcome)
            _, size = write_file_atomic(path, new_content, create_dirs=False)
            return json.dumps(_with_warnings(
                {"path": path, "patched": True, "size": size}, outcome))

        _, size = write_file_atomic(path, new_content, create_dirs=False)
        return json.dumps({"path": path, "patched": True, "size": size})
    except FileNotFoundError:
        return json.dumps({"error": f"File not found: {path}", "path": path})
    except ValueError as e:
        return json.dumps({"error": str(e), "path": path})
    except Exception as e:
        logger.error(f"vault_patch error for {path}: {e}")
        return json.dumps({"error": str(e), "path": path})


def vault_append(path: str, content: str, create_if_missing: bool = False) -> str:
    """Append content to the end of a file. Adds a newline before appending if the file doesn't end with one."""
    try:
        creating_new = False
        try:
            existing, _ = read_file(path)
            if existing and not existing.endswith("\n"):
                content = "\n" + content
            new_content = existing + content
        except FileNotFoundError:
            if not create_if_missing:
                return json.dumps({"error": f"File not found: {path}", "path": path})
            new_content = content
            creating_new = True

        # Gate only when create_if_missing creates a NEW file (M2 spec §2);
        # appends to an existing file are body-only and not checked.
        outcome = None
        if creating_new:
            outcome = validate_write(path, new_content, tool="vault_append")
            if outcome.blocked:
                return _rejection({"path": path, "appended": False}, outcome)

        _, size = write_file_atomic(path, new_content, create_dirs=create_if_missing)
        return json.dumps(_with_warnings(
            {"path": path, "appended": True, "size": size}, outcome))
    except ValueError as e:
        return json.dumps({"error": str(e), "path": path})
    except Exception as e:
        logger.error(f"vault_append error for {path}: {e}")
        return json.dumps({"error": str(e), "path": path})
