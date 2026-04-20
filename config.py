# -*- coding: utf-8 -*-
"""
config.py
Central configuration: API keys and Daily Value (DV) reference amounts.
DV values are based on FDA 2020-2025 Dietary Guidelines (2000 kcal diet).
"""

# ── API Keys ──────────────────────────────────────────────────────────────────
# Set your USDA FoodData Central API key here (or load from file)
USDA_API_KEY_FILE = "usdaapikey.txt"   # path to key file, or set USDA_API_KEY directly
USDA_API_KEY      = None               # filled at runtime by load_keys()

ANTHROPIC_MODEL   = "claude-sonnet-4-20250514"

# ── USDA API ──────────────────────────────────────────────────────────────────
USDA_BASE_URL     = "https://api.nal.usda.gov/fdc/v1"
USDA_DATA_TYPES   = ["SR Legacy", "Foundation", "Survey (FNDDS)"]

# ── Nutrients to track ────────────────────────────────────────────────────────
# Maps the nutrient name as it appears in USDA responses → our internal label
NUTRIENT_MAP = {
    "Energy":                     "Calories (kcal)",
    "Protein":                    "Protein (g)",
    "Total lipid (fat)":          "Total Fat (g)",
    "Fatty acids, total saturated": "Saturated Fat (g)",
    "Cholesterol":                "Cholesterol (mg)",
    "Carbohydrate, by difference": "Carbohydrates (g)",
    "Fiber, total dietary":       "Dietary Fiber (g)",
    "Sugars, total including NLEA": "Total Sugars (g)",
    "Sodium, Na":                 "Sodium (mg)",
    "Calcium, Ca":                "Calcium (mg)",
    "Iron, Fe":                   "Iron (mg)",
    "Potassium, K":               "Potassium (mg)",
    "Vitamin C, total ascorbic acid": "Vitamin C (mg)",
    "Vitamin D (D2 + D3)":        "Vitamin D (mcg)",
}

# ── Daily Values (FDA reference, per day) ─────────────────────────────────────
DAILY_VALUES = {
    "Calories (kcal)":    2000,
    "Protein (g)":          50,
    "Total Fat (g)":        78,
    "Saturated Fat (g)":    20,
    "Cholesterol (mg)":    300,
    "Carbohydrates (g)":   275,
    "Dietary Fiber (g)":    28,
    "Total Sugars (g)":     50,   # added sugars DV; used as rough guide
    "Sodium (mg)":        2300,
    "Calcium (mg)":       1300,
    "Iron (mg)":            18,
    "Potassium (mg)":     4700,
    "Vitamin C (mg)":       90,
    "Vitamin D (mcg)":      20,
}

# Nutrients where LOWER is better (affects color coding in charts)
LOWER_IS_BETTER = {"Saturated Fat (g)", "Cholesterol (mg)", "Sodium (mg)", "Total Sugars (g)"}

# ── Chart styling ─────────────────────────────────────────────────────────────
COLOR_ORIGINAL   = "#e07b54"   # warm orange  – original meal
COLOR_SUBSTITUTE = "#6aab6e"   # soft green   – plant-based version
COLOR_OK         = "#5b9bd5"   # blue         – within DV
COLOR_HIGH       = "#e05c5c"   # red          – over DV (bad nutrient)
COLOR_LOW        = "#e05c5c"   # red          – under DV (good nutrient over limit)

# ── Report ────────────────────────────────────────────────────────────────────
REPORT_OUTPUT_DIR = "."        # where the final PDF is saved


def load_keys(key_file: str = USDA_API_KEY_FILE) -> str:
    """Read USDA API key from file and cache in module-level variable."""
    global USDA_API_KEY
    try:
        with open(key_file) as f:
            USDA_API_KEY = f.readline().strip()
    except FileNotFoundError:
        raise FileNotFoundError(
            f"USDA API key file '{key_file}' not found. "
            "Place your key in that file or set config.USDA_API_KEY directly."
        )
    return USDA_API_KEY
