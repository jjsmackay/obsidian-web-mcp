import os
from pathlib import Path

# Vault configuration
VAULT_PATH = Path(os.environ.get("VAULT_PATH", os.path.expanduser("~/Obsidian/MyVault")))
VAULT_MCP_TOKEN = os.environ.get("VAULT_MCP_TOKEN", "")
VAULT_MCP_PORT = int(os.environ.get("VAULT_MCP_PORT", "8420"))

# OAuth 2.0 client credentials (for Claude app integration)
VAULT_OAUTH_CLIENT_ID = os.environ.get("VAULT_OAUTH_CLIENT_ID", "vault-mcp-client")
VAULT_OAUTH_CLIENT_SECRET = os.environ.get("VAULT_OAUTH_CLIENT_SECRET", "")

# Safety limits
MAX_CONTENT_SIZE = 1_000_000  # 1MB max write size
MAX_BATCH_SIZE = 20           # Max files per batch operation
MAX_SEARCH_RESULTS = 50       # Max results per search
DEFAULT_SEARCH_RESULTS = 20
MAX_LIST_DEPTH = 5            # Max directory recursion depth
CONTEXT_LINES = 2             # Default lines of context in search results

# Directories to never expose or modify
EXCLUDED_DIRS = {".obsidian", ".trash", ".git", ".DS_Store"}

# Frontmatter index refresh interval (seconds)
FRONTMATTER_INDEX_DEBOUNCE = 5.0

# Rate limiting (requests per minute) -- track in-memory, enforce per-token
RATE_LIMIT_READ = 100
RATE_LIMIT_WRITE = 30

# --- L2 write-boundary validator (M2 Frontmatter Governance) ---
# All settings are runtime config, never code changes (M2 spec §6).
# Mode: "warn" (write anyway + log + return warnings) or "block" (refuse).
# Default is warn -- the live vault is largely non-conforming until cutover, so
# a blocking validator would break live ops (M2 spec §6 bootstrap safety).
VALIDATOR_MODE = os.environ.get("VALIDATOR_MODE", "warn").strip().lower()

# Master kill-switch. When false the write path is completely untouched.
VALIDATOR_ENABLED = os.environ.get("VALIDATOR_ENABLED", "true").strip().lower() in (
    "1", "true", "yes", "on",
)

# Vocabulary companion file (machine-readable mirror of SYS_Schema + the M2 §4
# path<->type table). Loaded from the vault at runtime; edit-then-write round
# trips without a redeploy. Path is relative to the vault root (or absolute).
VALIDATOR_VOCAB_PATH = os.environ.get(
    "VALIDATOR_VOCAB_PATH", "90_System/91_Bobsidian/SYS_Schema_Vocab.yaml"
)

# Violation log (JSONL). Written in BOTH modes (M2 spec §7). Relative to the
# vault root (or absolute). Its default home is itself an exempt path.
VALIDATOR_LOG_PATH = os.environ.get(
    "VALIDATOR_LOG_PATH", "90_System/99_Graph/validator_log.jsonl"
)
