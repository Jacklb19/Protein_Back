"""Bounds and storage contract for project listings and their compact previews."""

PROJECT_NAME_LENGTH = 120
PROJECT_PREVIEW_RESIDUES = 40
# Listing rows with the expiry of each project's unexpired link (20261114000000_share_duration.sql).
PROJECT_LIST_RPC = "rpc/pf_list_projects_with_links"
PROJECT_ORDERS = ("recent", "name")
DEFAULT_PROJECT_ORDER = PROJECT_ORDERS[0]
# Projects one account may create; repeated in 20261113000000_project_quota.sql. Accounts already above it
# keep and edit what they have; only a new project is refused.
PROJECT_LIMIT = 50
# Fixed message of pf_project_before_insert_quota; never contains submitted data.
PROJECT_QUOTA_MESSAGE = "Project quota exceeded"
PROJECT_QUOTA_CODE = "project-quota-exceeded"
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
# Durations an owner may choose for a new link, in days; repeated in 20261114000000_share_duration.sql.
PROJECT_SHARE_DURATIONS_DAYS = (1, PROJECT_SHARE_LIFETIME_DAYS, 30)
