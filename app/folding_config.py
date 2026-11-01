"""Conservative bounds for the shared public folding server, independent of web routes."""

FOLDING_URL = "https://api.esmatlas.com/foldSequence/v1/pdb/"
FOLDING_PROVIDER = "esm-atlas"
FOLDING_MODEL = "esmfold_v1"
MIN_FOLDING_RESIDUES = 3
MAX_FOLDING_RESIDUES = 120
MAX_FOLDING_BYTES = 1_000_000
FOLDING_TIMEOUT_SECONDS = 30.0
FOLDING_REQUESTS_PER_MINUTE = 1
FOLDING_PROCESS_REQUESTS_PER_MINUTE = 3
FOLDING_WINDOW_SECONDS = 60.0
MAX_FOLDING_CALLERS = 1024
MAX_ATOMS_PER_RESIDUE = 64
MAX_COORDINATE_MAGNITUDE = 100_000
FOLDING_CONTENT_TYPES = ("chemical/x-pdb", "text/plain", "application/octet-stream")
