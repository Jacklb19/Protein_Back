"""Bounds and storage contract for project listings and their compact previews."""

PROJECT_NAME_LENGTH = 120
PROJECT_PREVIEW_RESIDUES = 40
PROJECT_LIST_RPC = "rpc/pf_list_projects"
PROJECT_ORDERS = ("recent", "name")
DEFAULT_PROJECT_ORDER = PROJECT_ORDERS[0]
# Earlier contents kept per project; repeated in 20261110000000_project_versions.sql.
PROJECT_VERSION_LIMIT = 5
# Versions with whether each sequence equals the current one (20261114000001_version_changes.sql).
PROJECT_VERSIONS_RPC = "rpc/pf_list_project_versions_compared"
PROJECT_RESTORE_RPC = "rpc/pf_restore_project_version"
# Fixed message of pf_restore_project_version; never contains submitted data.
VERSION_NOT_FOUND_MESSAGE = "Version not found"
# Read-only links: random bytes per token (43 URL-safe characters) and days a link lasts, repeated in
# 20261111000000_project_shares.sql.
PROJECT_SHARE_TOKEN_BYTES = 32
PROJECT_SHARE_TOKEN_PATTERN = r"[A-Za-z0-9_-]{43}"
PROJECT_SHARE_TOKEN_MAX_LENGTH = 64
PROJECT_SHARE_LIFETIME_DAYS = 7
