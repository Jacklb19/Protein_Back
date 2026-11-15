"""Shared spending budgets counted in PostgreSQL; every value here is repeated in 20261113000001_shared_budget.sql."""

SPEND_RPC = "rpc/pf_spend"
SPEND_REMAINING_RPC = "rpc/pf_spend_remaining"

SCOPE_ASSISTANT = "assistant"
SCOPE_UNIPROT = "uniprot"
SCOPE_FOLDING = "folding"

MINUTE_SECONDS = 60
DAY_SECONDS = 86_400
# Highest per-account limit PostgreSQL accepts per window, whatever the environment says. A signed-in person
# can call pf_spend directly; the ceiling bounds what that adds to a global counter. Settings above it are
# clamped, so keep ASSISTANT_REQUESTS_PER_MINUTE and UNIPROT_REQUESTS_PER_MINUTE at or below these.
SPEND_CEILINGS = {SCOPE_ASSISTANT: 60, SCOPE_UNIPROT: 120, SCOPE_FOLDING: 3}
GLOBAL_SPEND_CEILING = 100_000
# The in-process fallback keeps one window for "everyone" under this name.
GLOBAL_FALLBACK_SUBJECT = "*"
