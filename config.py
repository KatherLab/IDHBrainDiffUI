"""
config.py — App-level constants for the Brain annotation platform.
"""

import os

# Load .env if present
_env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

# Max new (non-password-protected) accounts that can be created per calendar day
MAX_NEW_ACCOUNTS_PER_DAY = 20

# Flask secret key — change before deploying publicly
SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "brain-study-2026-change-me")

# Admin download key — set ADMIN_KEY env var on the server, never commit a real value
ADMIN_KEY = os.environ.get("ADMIN_KEY", "")

# Password-protected rater accounts (others may log in freely).
# IDs and passwords are loaded from .env so nothing is hardcoded here.
RATER_PASSWORDS = {}
for _i in range(1, 20):
    _id = os.environ.get(f"RATER_ID_{_i}", "")
    _pwd = os.environ.get(f"RATER_PASSWORD_{_id.upper().replace('-', '_')}", "")
    if _id and _pwd:
        RATER_PASSWORDS[_id] = _pwd

# Path to the pre-generated study configuration (build with build_config.py)
STUDY_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "study_config.json")

# Directory where per-rater result JSON files are saved
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")

# Task 2 morphological features for brain (glioma / GBM) tissue.
# Each entry is (key, display label).
BRAIN_FEATURES = [
    ("hypercellularity",            "Hypercellularity"),
    ("nuclear_pleomorphism",        "Nuclear pleomorphism / atypia"),
    ("mitotic_activity",            "Mitotic activity"),
    ("microvascular_proliferation", "Microvascular proliferation"),
    ("necrosis",                    "Necrosis"),
    ("gemistocytes",                "Gemistocytic / astrocytic cells"),
    ("multinucleated_giant_cells",  "Multinucleated giant cells"),
    ("artifact",                    "Artifact (synthetic or imaging)"),
    ("other",                       "Other (specify below)"),
]

# Task 2 graded aspects, rated 1-10 for both the original and the
# counterfactual image. Grouped as (group label, [(key, label), ...]).
BRAIN_SCALE_ASPECTS = [
    ("Tumour cell", [
        ("tc_number",         "Number"),
        ("tc_size",           "Size"),
        ("tc_pleomorphism",   "Pleomorphism"),
        ("tc_hyperchromasia", "Hyperchromasia"),
    ]),
    ("Background", [
        ("bg_vascularity",    "Vascularity"),
        ("bg_fibrous_tissue", "Fibrous tissue"),
        ("bg_neuropil",       "Background neuropil"),
    ]),
]
