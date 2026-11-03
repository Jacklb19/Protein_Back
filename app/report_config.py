"""Bounds and storage contract for reports generated in the browser and stored in Supabase Storage.

supabase/migrations/20261101000000_report_storage.sql repeats these values, because PostgreSQL and
Storage must enforce them even for direct calls; tests/test_reports.py keeps both in step.
"""

REPORT_BUCKET = "reports"
# Kind -> (content type sent on upload and stored by Storage, file extension, download file name).
REPORT_KINDS = {
    "pdf": ("application/pdf", "pdf", "proteinforge-report.pdf"),
    "json": ("application/json", "json", "proteinforge-report.json"),
    "profile-csv": ("text/csv", "csv", "proteinforge-profile.csv"),
    "alignment-csv": ("text/csv", "csv", "proteinforge-alignment.csv"),
}
# Per file: below the 50 MB Free plan ceiling; larger reports stay local downloads.
MAX_REPORT_BYTES = 5_000_000
# Per owner, pending reservations included: 40 owners at the limit fill the 1 GB Free plan storage.
MAX_REPORTS_PER_OWNER = 20
MAX_OWNER_REPORT_BYTES = 25_000_000
# Supabase signed upload URLs last two hours and cannot be shortened; reservations expire with them.
UPLOAD_URL_SECONDS = 7_200
DOWNLOAD_URL_SECONDS = 300
DEFAULT_REPORT_PAGE_SIZE = 20
MAX_REPORT_PAGE_SIZE = 50
MAX_REPORT_OFFSET = 1_000
REPORT_RPC = {
    "reserve": "rpc/pf_reserve_report",
    "complete": "rpc/pf_complete_report",
    "list": "rpc/pf_list_reports",
    "object": "rpc/pf_report_object",
    "delete": "rpc/pf_delete_report",
}
