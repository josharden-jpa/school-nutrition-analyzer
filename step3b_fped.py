# -*- coding: utf-8 -*-
"""
step3b_fped.py
Food group equivalent lookup using USDA Food Patterns Equivalents Database (FPED).

MATCHING PRIORITY
-----------------
  1. FPED_SKIP         -- skip entirely (seasonings, items with no food group)
  2. FPED_NO_MATCH     -- known to have no FPED entry; Claude finds best proxy
  3. DIRECT_FPED_MAP   -- hardcoded verified codes (instant, no API)
  4. fped_learned.json -- codes learned in previous runs (instant, no API)
  5. USDA API + Claude -- search FNDDS, Claude picks best candidate
  6. Claude proxy      -- no API match found; Claude searches FPED directly
  7. Fuzzy fallback    -- keyword scan, flagged for human review

FPED_NO_MATCH HANDLING
----------------------
Some ingredients (tempeh, nutritional yeast, seitan, etc.) exist in USDA
nutrient databases but not in FPED because they were too niche to appear
in NHANES dietary surveys. For these we don't waste time searching --
instead Claude is given the full list of FPED descriptions and asked to
pick the closest food group proxy. This gets saved to fped_learned.json
permanently. One Claude call per novel ingredient, ever.

LEARNED CACHE
-------------
Every Claude-verified match is saved to fped_learned.json. Next run that
ingredient goes straight to step 4 with no API or Claude call. The cache
grows through use and covers more ingredients over time automatically.

LIMITATIONS
-----------
- Niche ingredients may use proxies that approximate but don't perfectly
  represent their food group contribution. This is documented per-ingredient
  in the learned cache and flagged in output.
- FPED 2017-2018 is the most recent publicly available version.
- Fatty Acids HEI component requires MUFA/PUFA data not currently in
  config.NUTRIENT_MAP -- add to capture full HEI-2020 score.

Download FPED
-------------
https://www.ars.usda.gov/ARSUserFiles/80400530/apps/FPED_1718.xls
Save as FPED_1718.xlsx in your project directory.
"""

from __future__ import annotations
import json
import os
import re
import time
import requests
import pandas as pd
import config


FNDDS_SEARCH_URL   = "https://api.nal.usda.gov/fdc/v1/foods/search"
ANTHROPIC_API_URL  = "https://api.anthropic.com/v1/messages"
LEARNED_CACHE_PATH = "fped_learned.json"


# -----------------------------------------------------------------------------
# FPED_SKIP
# Ingredients with no meaningful food group contribution.
# Fuzzy matching on these always lands on something wrong.
# -----------------------------------------------------------------------------
FPED_SKIP = {
    "salt", "pepper", "black pepper", "sea salt", "kosher salt",
    "cumin", "chili powder", "garlic powder", "onion powder", "oregano",
    "paprika", "cayenne", "coriander", "turmeric", "bay leaf",
    "water", "ice", "baking powder", "baking soda",
    "lime juice", "lemon juice", "hot sauce", "vinegar",
    "vinegar, cider", "vinegar, white", "vinegar, balsamic", "vinegar, apple cider",
    "worcestershire sauce",   # tiny gram weights, negligible food group contribution
    "soy sauce", "fish sauce", "oyster sauce",
    "sugar", "brown sugar", "honey", "maple syrup", "agave",  # tracked via Added Sugars nutrient
}


# -----------------------------------------------------------------------------
# FPED_NO_MATCH
# Ingredients that exist in USDA nutrient databases but NOT in FPED.
# These skip API search entirely and go straight to Claude proxy finding,
# which searches the FPED description index directly.
# -----------------------------------------------------------------------------
FPED_NO_MATCH = {
    # Plant proteins -- too niche for NHANES surveys
    "tempeh, cooked", "tempeh",
    "seitan, cooked", "seitan",
    "nutritional yeast",
    "textured soy protein, cooked", "textured vegetable protein, cooked",

    # Specialty items
    "cashew cream", "oat milk", "almond milk", "soy milk",
    "coconut cream", "coconut milk",
    "vegan cheese", "vegan sour cream", "vegan cream cheese",

    # Bread/flour coatings and thickeners -- skip entirely
    # Negligible food group contribution at typical recipe serving sizes
    "bread crumbs", "bread crumbs, dry", "breadcrumbs, dry", "breadcrumbs, plain",
    "wheat flour", "wheat flour, all-purpose", "wheat flour, white",
    "wheat flour, enriched", "wheat flour, white, all-purpose",
    "flour, all-purpose", "flour, white, all-purpose, enriched", "flour, all purpose",
    "cornstarch",
    # Syrup and sweetener coatings -- skip, tracked via Added Sugars nutrient
    "syrup, maple", "syrup, pancake", "maple syrup",
    "vanilla extract", "cinnamon, ground",
    # Vinegars -- negligible food group contribution  
    "apple cider vinegar", "vinegar, distilled", "vinegar, apple cider",

    # Potatoes -- no standalone FPED entry as raw ingredient
    "potatoes, cooked", "potatoes, cooked, boiled", "potatoes, baked",
    "potatoes, mashed", "potatoes, frozen, oven-baked", "potatoes, frozen, prepared",
    "potato, baked",

    # Tomato-based sauces -- no standalone FPED entry
    "tomato sauce, canned", "tomato paste",
    "marinara sauce, canned", "pizza sauce, canned", "enchilada sauce, canned",

    # Broths -- minimal food group contribution
    "chicken broth", "chicken broth, canned",
    "beef broth", "beef broth, canned",
}


# -----------------------------------------------------------------------------
# PROXY_HINTS
# For ingredients where Claude's default food group reasoning leads to wrong
# proxy categories, provide explicit search guidance.
# Format: "ingredient (lowercase)": ("search term for FPED", "reason note")
# -----------------------------------------------------------------------------
PROXY_HINTS = {
    # Plant-based dairy alternatives -- these are NOT dairy.
    # They should map to nuts/seeds/oils for food group purposes.
    "cashew cream":        ("cashew", "nut-based fat, not dairy"),
    "oat milk":            ("oat",    "grain-based beverage, not dairy"),
    "almond milk":         ("almond", "nut-based beverage, not dairy"),
    "soy milk":            ("soy",    "legume-based beverage, not dairy"),
    "coconut cream":       ("coconut","plant fat, not dairy"),
    "coconut milk":        ("coconut","plant fat, not dairy"),
    "vegan cheese":        ("tofu",   "plant protein, not dairy"),
    "vegan sour cream":    ("cashew", "nut-based fat, not dairy"),

    # Plant proteins -- map to soy/legume protein foods
    "tempeh, cooked":      ("tofu",   "fermented soy, proxy as tofu protein food"),
    "tempeh":              ("tofu",   "fermented soy, proxy as tofu protein food"),
    "seitan, cooked":      ("tofu",   "wheat protein, proxy as soy protein food"),
    "seitan":              ("tofu",   "wheat protein, proxy as soy protein food"),
    "nutritional yeast":   ("yeast",  "protein/B12 supplement, not dairy"),

    # Textured soy protein
    "textured soy protein, cooked": ("tofu", "soy protein food"),
    "textured vegetable protein, cooked": ("tofu", "soy protein food"),

    # Bread and flour products used as coatings/thickeners -- skip in FPED
    # These are cooking ingredients, not food group contributors at typical serving sizes
    # They are in FPED_SKIP so this hint block is only a safety fallback
    "bread crumbs":                    ("bread, white", "refined grain coating"),
    "bread crumbs, dry":               ("bread, white", "refined grain coating"),
    "breadcrumbs, dry":                ("bread, white", "refined grain coating"),
    "breadcrumbs, plain":              ("bread, white", "refined grain coating"),
    "wheat flour":                     ("bread, white", "refined grain thickener"),
    "wheat flour, all-purpose":        ("bread, white", "refined grain thickener"),
    "wheat flour, white":              ("bread, white", "refined grain thickener"),
    "wheat flour, enriched":           ("bread, white", "refined grain thickener"),
    "wheat flour, white, all-purpose": ("bread, white", "refined grain thickener"),
    "flour, all-purpose":              ("bread, white", "refined grain thickener"),
    "flour, white, all-purpose, enriched": ("bread, white", "refined grain thickener"),
    "flour, all purpose":              ("bread, white", "refined grain thickener"),
    "cornmeal":                        ("corn bread", "refined grain"),
    "cornstarch":                      ("corn", "starchy thickener"),

    # Potatoes -- no raw ingredient entry in FPED; search whole-dish potato entries
    "potatoes, cooked":                ("potato, NFS", "starchy vegetable, use NFS entry"),
    "potatoes, cooked, boiled":        ("potato, NFS", "starchy vegetable, use NFS entry"),
    "potatoes, baked":                 ("potato, NFS", "starchy vegetable, use NFS entry"),
    "potatoes, mashed":                ("mashed potato", "starchy vegetable"),
    "potatoes, frozen, oven-baked":    ("french fries", "starchy vegetable"),
    "potatoes, frozen, prepared":      ("french fries", "starchy vegetable"),
    "potatoes, frozen french fries, baked": ("french fries", "starchy vegetable"),
    "potato, baked":                   ("potato, NFS", "starchy vegetable"),

    # Tomato-based sauces -- search "tomatoes, raw" to get vegetable classification
    # Do NOT search "tomato sauce" -- FPED maps that to mixed dishes not vegetables
    "tomato sauce, canned":            ("tomatoes, raw", "vegetable component"),
    "tomato paste":                    ("tomatoes, raw", "vegetable component"),
    "tomato paste, canned":            ("tomatoes, raw", "vegetable component"),
    "marinara sauce, canned":          ("tomatoes, raw", "vegetable component"),
    "pizza sauce, canned":             ("tomatoes, raw", "vegetable component"),
    "enchilada sauce, canned":         ("enchilada sauce", "vegetable component"),

    # Broths -- proxy as closest available
    "chicken broth":                   ("chicken broth", "minimal food group contribution"),
    "chicken broth, canned":           ("chicken broth", "minimal food group contribution"),
    "beef broth":                      ("beef broth", "minimal food group contribution"),
    "beef broth, canned":              ("beef broth", "minimal food group contribution"),
}


# -----------------------------------------------------------------------------
# DIRECT FPED MAP
# Hardcoded verified FOODCODEs for the most common school lunch ingredients.
# Verified = confirmed by searching FPED_1718.xlsx directly.
# UNVERIFIED = reasonable guess, not yet confirmed in your file.
#
# To verify or add:
#   import pandas as pd
#   df = pd.read_excel("FPED_1718.xlsx", dtype={"FOODCODE": str})
#   df.columns = [c.strip() for c in df.columns]
#   df = df.set_index("FOODCODE")
#   print(df[df["DESCRIPTION"].str.lower().str.contains("your term")]["DESCRIPTION"])
# -----------------------------------------------------------------------------
DIRECT_FPED_MAP = {
    # ── Proteins -- verified ─────────────────────────────────────────────────
    "ground beef, cooked":          "21500100",
    "ground beef":                  "21500100",
    # Chicken -- verified from FPED search
    "chicken breast, cooked":       "24122130",   # Chicken breast, baked/broiled, skin not eaten
    "chicken breast":               "24122130",
    "chicken breast, roasted":      "24122130",
    "chicken breast, grilled":      "24122130",
    "chicken, cooked":              "24122130",
    "chicken, roasted":             "24122130",
    # Eggs -- verified from FPED search
    # Note: FPED has no plain scrambled egg entry; use egg protein food equivalent
"eggs, scrambled":              "31102000",
    "egg, scrambled":               "31102000",
    "egg, whole":                   "31102000",
    "egg, whole, raw":              "31102000",
    "egg, whole, cooked":           "31102000",
    "egg, whole, cooked, scrambled":"31102000",
    "eggs, whole, cooked, scrambled":"31102000",
    "egg":                          "31102000",
    # Other proteins
    "tuna, canned":                 "26101000",
    "tofu, extra firm, cooked":     "42101000",
    "tofu, extra firm":             "42101000",

    # ── Grains -- verified ────────────────────────────────────────────────────
    "flour tortilla":               "52215200",
    "tortillas, flour":             "52215200",
    "flour tortillas":              "52215200",
    # Bread -- verified from FPED search
    "white bread":                  "51101000",   # Bread, white
    "bread, white":                 "51101000",
    "bread, white, commercially prepared": "51101000",
    "whole wheat bread":            "51300110",   # Bread, whole wheat -- verified (G_WHOLE 3.63)
    "bread, whole wheat":           "51300110",
    # Rice -- verified from FPED search
    "white rice, cooked":           "56205001",   # Rice, white, cooked, NS as to fat
    "rice, white, cooked":          "56205001",
    "rice, cooked":                 "56205001",
    "rice, white":                  "56205001",
    "brown rice, cooked":       "56205018",   # Rice, brown, cooked, no added fat -- whole grain
    "mushrooms, cooked":        "99997515",   # Mushrooms, cooked, as ingredient
    "dumpling wrapper, cooked": "56112000",   # Noodles, cooked -- refined-grain wrapper proxy
  
    # Other grains
    "hamburger bun":                "52101000",
    # Bread crumbs -- no good FPED entry; small quantity, minimal food group impact
    # Added to FPED_NO_MATCH below with grain proxy hint

    # ── Dairy -- verified ─────────────────────────────────────────────────────
    "cheddar cheese":               "14104100",
    "cheddar cheese, shredded":     "14104100",
    "cheese, cheddar":              "14104100",
    "cheese, cheddar, shredded":    "14104100",
    "sour cream":                   "12310100",
    "mozzarella cheese":            "14107010",   # Cheese, Mozzarella, NFS -- verified
    "mozzarella cheese, part skim": "14107030",   # Cheese, Mozzarella, part skim -- verified
    "mozzarella cheese, shredded":  "14107010",
    "american cheese":              "14100100",
    "butter":                       "81100500",   # Butter, NFS -- verified
    "butter, unsalted":             "81100500",
    "butter, salted":               "81100500",
    "milk, whole":                  "11111000",
    "milk, 2%":                     "11112000",
    "milk, skim":                   "11113000",
    "milk, chocolate":              "11120000",
    "milk":                         "11111000",   # generic milk -> whole milk
    "milk, low fat":                "11112000",
    "milk, reduced fat":            "11112000",
    "milk, fat free":               "11113000",

    # ── Vegetables -- verified ────────────────────────────────────────────────
    "lettuce, iceberg, shredded":   "75113000",
    "lettuce, iceberg, raw":        "75113000",
    "lettuce, raw":                 "75113000",
    "romaine lettuce, raw":         "72116000",
    "lettuce, cos or romaine, raw": "72116000",
    "tomatoes, fresh, diced":       "74101000",
    "tomatoes, raw":                "74101000",
    "tomato, raw":                  "74101000",
    "tomatoes, red, ripe, raw":     "74101000",
    "tomatoes, fresh":              "74101000",
    "tomatoes, diced":              "74101000",
    "tomatoes, canned, diced":      "74101000",   # close enough for food group
    "tomatoes, canned, crushed":    "74101000",
    "corn, canned":                 "75216113",
    "corn, cooked":                 "75216113",
    "corn kernels, canned":         "75216113",
    "corn, sweet, cooked":          "75216113",
    "onions, raw":                  "75117020",
    "onion, raw":                   "75117020",
    "onion, cooked":                "99997510",
    "carrots, raw":                 "73101010",   # Carrots, raw -- verified
    "carrots, cooked":              "73102190",   # Carrots, cooked, from restaurant -- verified
    "broccoli, raw":                "72201100",   # Broccoli, raw -- verified (dark green)
    "broccoli, cooked":             "72201190",   # Broccoli, cooked, from restaurant -- verified (dark green)
    "broccoli, steamed":            "72201190",
    "broccoli":                     "72201190",
    "cabbage, raw":                 "71301010",   # (unverified — not yet seen in runs)
    "cabbage, cooked":              "99997530",   # Cabbage, cooked, as ingredient -- verified
    "peas, cooked":                 "75224000",   # Green peas, cooked, from restaurant -- verified
    "green peas, cooked":           "75224000",
    "snap peas, cooked":            "75224000",
    # Potatoes -- verified from FPED search
    "potato, nfs":                  "71000100",   # Potato, NFS -- verified
    "potato, raw":                  "71000100",
    "potatoes, raw":                "71000100",
    "potato, cooked":               "71000100",
    "potatoes, cooked":             "71000100",
    "potato, boiled":               "71000100",
    "potatoes, boiled":             "71000100",
    "potato, baked":                "71000100",
    "potatoes, baked":              "71000100",
    "potatoes, mashed":             "71000100",
    # French fries and fried potato products -- verified
    "french fries":                 "71400990",   # Potato, french fries, NFS -- verified
    "potato, french fries":         "71400990",
    "potatoes, frozen, oven-baked": "71401020",   # Potato, french fries, from frozen, baked
    "potatoes, frozen, french fried": "71401020",
    "potatoes, frozen, prepared":   "71401020",
    "potatoes, frozen french fries, baked": "71401020",
    "potatoes, frozen, fried":      "71401020",
    "potatoes, frozen, baked":      "71401020",
    # Hash browns / tater tots / smiley potatoes
    "hash browns":                  "71404000",   # Potato, hash brown, NFS -- verified
    "hash browns, frozen, cooked":  "71404000",
    "tater tots":                   "71400990",   # proxy as french fries -- same food group
    "smiley potatoes":              "71400990",
    "potato wedges":                "71401020",
    "seasoned potato wedges":       "71401020",
    "crispy tater tots":            "71400990",
    "crispy crinkle cut fries":     "71400990",
    "seasoned crinkle cut fries":   "71400990",

    # ── Legumes -- verified ───────────────────────────────────────────────────
    "black beans, canned":          "41102080",
    "black beans, cooked":          "41102020",
    "pinto beans, canned":          "41104080",
    "kidney beans, canned":         "41103080",
    "lentils, cooked":              "41301000",
    "green beans, cooked":   "75205021",
    "green beans, steamed":  "75205021",
    "green beans, canned":   "75205023",

    # ── Fats / condiments -- verified ─────────────────────────────────────────
    "vegetable oil":                "82101000",
    "oil, vegetable, sunflower, linoleic, (partially hydrogenated)": "82101000",
    "ketchup":                      "74401010",
    "olive oil":                    "82102000",
    "salsa":                        "83200000",

# ── Fruits -- verified from FPED search ──────────────────────────────────
    "apple, raw":                   "63101000",   # Apple, raw -- verified
    "apples, raw":                  "63101000",
    "apple":                        "63101000",
    "apples":                       "63101000",
    "orange, raw":                  "61119010",   # Orange, raw -- verified
    "orange":                       "61119010",
    "oranges, raw":                 "61119010",
    "oranges":                      "61119010",
    "grapes, raw":                  "63123000",   # Grapes, raw -- verified
    "grapes":                       "63123000",
    "grapes, red or green":         "63123000",
    "pear, raw":                    "63137010",   # Pear, raw -- verified
    "pear":                         "63137010",
    "pears, raw":                   "63137010",
    "watermelon, raw":              "63149010",   # Watermelon, raw -- verified
    "watermelon":                   "63149010",
    "pineapple, raw":               "63141010",   # Pineapple, raw -- verified
    "pineapple":                    "63141010",
    # Banana -- IS a standalone FPED entry (was wrongly proxied to apple 63101000)
    "banana, raw":                  "63107010",   # Banana, raw -- verified (F_TOTAL 0.67)
    "banana":                       "63107010",
    "bananas, raw":                 "63107010",
    "bananas":                      "63107010",
    # Strawberries -- IS a standalone FPED entry (was wrongly proxied to apple)
    "strawberries, raw":            "63223020",   # Strawberries, raw -- verified (F_TOTAL 0.69)
    "strawberries":                 "63223020",
    "strawberry, raw":              "63223020",
    "strawberry slices":            "63223020",
    # Peaches -- raw and canned both standalone entries (were wrongly proxied to apple)
    "peach, raw":                   "63135010",   # Peach, raw -- verified (F_TOTAL 0.65)
    "peaches, raw":                 "63135010",
    "peaches, canned":              "63135110",   # Peach, canned, NFS -- verified (F_TOTAL 0.26)
    "peaches, canned in juice":     "63135170",   # Peach, canned, juice pack -- verified
    "chilled diced peaches":        "63135110",

    # ── Misc ──────────────────────────────────────────────────────────────────
    "nutritional yeast":            "75236000",
    # Floor-cleanup -- verified from FPED search
    "chickpeas, canned":                       "41302080",  # Chickpeas, from canned, no added fat -- legume
    "mandarin oranges, canned in light syrup": "61122330",  # Orange, canned, in syrup -- fruit
    "yogurt, plain":                           "11411010",  # Yogurt, NS as to type of milk, plain -- dairy
    "eggs, cooked":                            "31102000",  # Egg, whole, cooked, NS as to method
    "pasta, cooked":                           "56130000",  # Pasta, cooked -- refined grain
    "onions, cooked":                          "99997510",  # Onions, cooked, as ingredient
    "bread, white, cooked":                    "51101000",  # Bread, white -- refined grain
}


# -----------------------------------------------------------------------------
# FPED column names as they appear in FPED_1718.xlsx
# -----------------------------------------------------------------------------
FPED_COLUMNS = {
    "F_TOTAL (cup eq.)":         "fruit_total_cup",
    "F_CITMLB (cup eq.)":        "fruit_citrus_cup",
    "V_TOTAL (cup eq.)":         "veg_total_cup",
    "V_DRKGR (cup eq.)":         "veg_darkgreen_cup",
    "V_REDOR_TOTAL (cup eq.)":   "veg_redorange_cup",
    "V_STARCHY_TOTAL (cup eq.)": "veg_starchy_cup",
    "V_LEGUMES (cup eq.)":       "veg_legumes_cup",
    "V_OTHER (cup eq.)":         "veg_other_cup",
    "G_WHOLE (oz. eq.)":         "grains_whole_oz",
    "G_REFINED (oz. eq.)":       "grains_refined_oz",
    "D_TOTAL (cup eq.)":         "dairy_total_cup",
    "PF_TOTAL (oz. eq.)":        "protein_total_oz",
    "PF_MPS_TOTAL (oz. eq.)":    "protein_meat_oz",
    "PF_SEAFD_HI (oz. eq.)":     "protein_seafood_hi_oz",
    "PF_SEAFD_LOW (oz. eq.)":    "protein_seafood_lo_oz",
    "PF_EGGS (oz. eq.)":         "protein_eggs_oz",
    "PF_NUTSDS (oz. eq.)":       "protein_nutsseeds_oz",
    "PF_SOY (oz. eq.)":          "protein_soy_oz",
    "PF_LEGUMES (oz. eq.)":      "protein_legumes_oz",
    "OILS (grams)":              "oils_g",
    "SOLID_FATS (grams)":        "solid_fats_g",
    "ADD_SUGARS (tsp. eq.)":     "added_sugars_tsp",
    "A_DRINKS (no. of drinks)":  "alcohol_drinks",
}

HEI_COMPONENTS_FROM_FPED = {
    "Total Fruits":               ["fruit_total_cup"],
    "Whole Fruits":               ["fruit_citrus_cup"],
    "Total Vegetables":           ["veg_total_cup"],
    "Greens and Beans":           ["veg_darkgreen_cup", "veg_legumes_cup"],
    "Whole Grains":               ["grains_whole_oz"],
    "Dairy":                      ["dairy_total_cup"],
    "Total Protein Foods":        ["protein_total_oz"],
    "Seafood and Plant Proteins": [
        "protein_seafood_hi_oz", "protein_seafood_lo_oz",
        "protein_nutsseeds_oz",  "protein_soy_oz",
        "protein_legumes_oz",
    ],
    "Refined Grains":             ["grains_refined_oz"],
    "Added Sugars":               ["added_sugars_tsp"],
    "Oils":                       ["oils_g"],
    "Solid Fats":                 ["solid_fats_g"],
}


# -----------------------------------------------------------------------------
# Persistent learned cache helpers
# -----------------------------------------------------------------------------
def _load_learned_cache(path: str = LEARNED_CACHE_PATH) -> dict:
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_learned_cache(cache: dict, path: str = LEARNED_CACHE_PATH) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[step3b] Warning: could not save learned cache: {e}")


class FPEDLookup:
    """
    Loads FPED_1718.xlsx once and provides ingredient-level food group lookups.

    Matching priority:
      skip -> no_match proxy -> direct map -> learned cache ->
      API + Claude -> Claude proxy -> fuzzy fallback
    """

    def __init__(
        self,
        fped_path:      str  = "FPED_1718.xlsx",
        api_key:        str  = None,
        anthropic_key:  str  = None,
        use_claude:     bool = True,
        cache_path:     str  = LEARNED_CACHE_PATH,
    ):
        if not os.path.exists(fped_path):
            raise FileNotFoundError(
                f"FPED file not found at '{fped_path}'.\n"
                "Download from:\n"
                "  https://www.ars.usda.gov/ARSUserFiles/80400530/apps/FPED_1718.xls\n"
                "Save as FPED_1718.xlsx in your project directory."
            )

        self.api_key    = api_key or config.USDA_API_KEY or config.load_keys()
        self.use_claude = use_claude
        self.cache_path = cache_path
        self._df        = self._load_fped(fped_path)
        self._session_cache: dict[str, tuple[str | None, str]] = {}
        self._learned: dict = _load_learned_cache(cache_path)
        self._learned_dirty = False

        self._anthropic_key = anthropic_key
        if use_claude and not anthropic_key:
            try:
                if os.path.exists("anthropicapikey.txt"):
                    with open("anthropicapikey.txt") as f:
                        self._anthropic_key = f.readline().strip()
                else:
                    self._anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
            except Exception:
                self._anthropic_key = ""
            if not self._anthropic_key:
                print("[step3b] No Anthropic key -- Claude disambiguation disabled")
                self.use_claude = False

        print(f"[step3b] FPED loaded: {len(self._df):,} food codes, "
              f"{len(self._df.columns)} columns matched")
        print(f"[step3b] Learned cache: {len(self._learned)} ingredients")
        if self.use_claude:
            print("[step3b] Claude disambiguation: ON (Haiku)")

    def _load_fped(self, path: str) -> pd.DataFrame:
        df = pd.read_excel(path, dtype={"FOODCODE": str})
        df.columns = [c.strip() for c in df.columns]
        if "FOODCODE" not in df.columns:
            raise ValueError(f"Expected 'FOODCODE' column. Got: {list(df.columns[:8])}")
        keep = ["DESCRIPTION"] + [c for c in FPED_COLUMNS.keys() if c in df.columns]
        missing = set(FPED_COLUMNS.keys()) - set(df.columns)
        if missing:
            print(f"[step3b] Note: {len(missing)} columns not in file: {missing}")
        return df.set_index("FOODCODE")[keep].fillna(0)

    def save(self) -> None:
        if self._learned_dirty:
            _save_learned_cache(self._learned, self.cache_path)
            self._learned_dirty = False
            print(f"[step3b] Learned cache saved: {len(self._learned)} ingredients "
                  f"-> {self.cache_path}")

    # -------------------------------------------------------------------------
    # Matching chain
    # -------------------------------------------------------------------------

    def find_fped_code(self, ingredient_name: str) -> tuple[str | None, str]:
        """
        Return (FOODCODE, source).
        source: 'direct' | 'proxy' | 'learned_claude' | 'learned_proxy' |
                'learned_fuzzy' | 'claude' | 'api' | 'fuzzy' | 'not_found'
        """
        key = ingredient_name.lower().strip()

        if key in self._session_cache:
            return self._session_cache[key]

        # 1. Skip list
        if key in FPED_SKIP:
            result = None, "skipped"
            self._session_cache[key] = result
            return result

        # 2. Direct map -- hardcoded verified codes
        if key in DIRECT_FPED_MAP:
            result = DIRECT_FPED_MAP[key], "direct"
            self._session_cache[key] = result
            return result

        # 3. Learned cache -- Claude-verified in a previous run
        if key in self._learned:
            entry = self._learned[key]
            entry["runs"] = entry.get("runs", 0) + 1
            self._learned_dirty = True
            source = "learned_" + entry.get("source", "claude")
            result = entry["foodcode"], source
            self._session_cache[key] = result
            return result

        # 4. Known no-match -- skip API search, go straight to Claude proxy
        if key in FPED_NO_MATCH:
            print(f"  [step3b] '{ingredient_name}' not in FPED -- finding proxy ...")
            code, desc = self._claude_find_proxy(ingredient_name)
            if code:
                self._persist_learned(key, code, desc, "proxy")
                result = code, "proxy"
            else:
                result = None, "not_found"
            self._session_cache[key] = result
            return result

        # 5. USDA API search for FNDDS candidates
        candidates = self._get_fndds_candidates(ingredient_name)

        # 6. Claude picks best candidate from API results
        if candidates and self.use_claude:
            code = self._claude_pick(ingredient_name, candidates)
            if code:
                desc = next((d for c, d in candidates if c == code), "")
                self._persist_learned(key, code, desc, "claude")
                result = code, "claude"
                self._session_cache[key] = result
                return result

        # 6b. API candidates without Claude -- validate before accepting
        if candidates:
            for code, desc in candidates:
                if code in self._df.index:
                    # Auto-validate: ask Claude if this match makes sense
                    if self.use_claude and self._claude_validate(ingredient_name, desc):
                        self._persist_learned(key, code, desc, "claude")
                        result = code, "claude"
                    else:
                        # Validation failed -- try proxy finder instead
                        print(f"  [step3b] API match rejected for '{ingredient_name}' "
                              f"-- finding better proxy ...")
                        code2, desc2 = self._claude_find_proxy(ingredient_name)
                        if code2:
                            self._persist_learned(key, code2, desc2, "proxy")
                            result = code2, "proxy"
                        else:
                            result = code, "api"   # accept anyway, flag for review
                    self._session_cache[key] = result
                    return result

        # 7. No API results -- Claude searches FPED descriptions directly
        if self.use_claude:
            print(f"  [step3b] No API results for '{ingredient_name}' -- "
                  f"asking Claude to find proxy in FPED ...")
            code, desc = self._claude_find_proxy(ingredient_name)
            if code:
                self._persist_learned(key, code, desc, "proxy")
                result = code, "proxy"
                self._session_cache[key] = result
                return result

        # 8. Fuzzy fallback -- validate before accepting
        code = self._fuzzy_match(ingredient_name)
        if code:
            desc = str(self._df.loc[code].get("DESCRIPTION", "")) \
                   if code in self._df.index else ""
            # Auto-validate fuzzy match
            if self.use_claude and not self._claude_validate(ingredient_name, desc):
                print(f"  [step3b] Fuzzy match rejected for '{ingredient_name}' "
                      f"-- finding proxy ...")
                code2, desc2 = self._claude_find_proxy(ingredient_name)
                if code2:
                    self._persist_learned(key, code2, desc2, "proxy")
                    result = code2, "proxy"
                    self._session_cache[key] = result
                    return result
            # Accept fuzzy match (validated or no Claude)
            self._persist_learned(key, code, desc, "fuzzy")
            result = code, "fuzzy"
            self._session_cache[key] = result
            return result

        result = None, "not_found"
        self._session_cache[key] = result
        return result

    def _persist_learned(self, key, code, description, source):
        self._learned[key] = {
            "foodcode":    code,
            "description": description,
            "source":      source,
            "runs":        1,
        }
        self._learned_dirty = True

    # -------------------------------------------------------------------------
    # Claude methods
    # -------------------------------------------------------------------------

    def _claude_call(self, prompt: str, max_tokens: int = 50) -> str:
        """Make a Claude Haiku API call. Returns response text or empty string."""
        try:
            resp = requests.post(
                ANTHROPIC_API_URL,
                headers={
                    "Content-Type":      "application/json",
                    "x-api-key":         self._anthropic_key,
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model":      config.ANTHROPIC_MODEL_FAST,
                    "max_tokens": max_tokens,
                    "messages":   [{"role": "user", "content": prompt}],
                },
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json()["content"][0]["text"].strip()
        except Exception as e:
            print(f"  [step3b] Claude call failed: {e}")
            return ""

    def _claude_pick(
        self,
        ingredient_name: str,
        candidates: list[tuple[str, str]],
    ) -> str | None:
        """
        Ask Claude Haiku to pick the best FPED match from API candidates.
        Returns FOODCODE or None.
        """
        lines = "\n".join(f"  {c}: {d}" for c, d in candidates)
        prompt = (
            f'Match this recipe ingredient to a USDA food database entry.\n\n'
            f'Ingredient: "{ingredient_name}"\n\n'
            f'Candidates:\n{lines}\n\n'
            f'Pick the entry that best represents this ingredient as a '
            f'standalone component in a recipe -- not a complex dish that '
            f'merely contains it.\n'
            f'Reply with only the 8-digit FOODCODE, or NONE if no entry fits.'
        )
        answer = self._claude_call(prompt, max_tokens=20)
        if not answer or answer.upper() == "NONE":
            return None
        match = re.search(r"\d{8}", answer)
        if match:
            code = match.group(0)
            return code if code in self._df.index else None
        return None

    def _claude_validate(
        self,
        ingredient_name: str,
        matched_description: str,
    ) -> bool:
        """
        Ask Claude Haiku whether a fuzzy/API match is actually reasonable
        for food group classification purposes.

        Returns True if the match is acceptable, False if it should be rejected.
        This is called automatically after any non-direct match to catch bad
        results before they contaminate food group scores.

        Examples of matches that would be rejected:
        - "carrots, raw" matched to "Beef stew with carrots" (wrong food group)
        - "butter" matched to "Blue cheese dressing" (wrong food group)
        - "ketchup" matched to "Mayonnaise" (wrong food group)
        """
        if not self.use_claude or not self._anthropic_key:
            return True   # can't validate without Claude -- accept and flag

        prompt = (
            f'For food group classification in HEI-2020 scoring, I need to '
            f'match "{ingredient_name}" to a USDA FPED database entry.\n\n'
            f'The matched entry is: "{matched_description}"\n\n'
            f'Is this match reasonable? The match is acceptable if the FPED '
            f'entry would assign the ingredient to approximately the correct '
            f'food group (protein foods, dairy, vegetables, grains, oils, etc.)\n'
            f'The match is NOT acceptable if it is a complex dish that merely '
            f'contains the ingredient, or if it would assign the wrong food group.\n\n'
            f'Reply with only YES or NO.'
        )
        answer = self._claude_call(prompt, max_tokens=5)
        return answer.upper().startswith("Y")

    def _claude_find_proxy(
        self, ingredient_name: str
    ) -> tuple[str | None, str]:
        """
        When an ingredient is not in FPED, find the closest food group proxy.

        Strategy:
        1. Check PROXY_HINTS for explicit search guidance (avoids wrong category)
        2. If no hint, ask Claude what food group + search term to use
        3. Search FPED descriptions for candidates matching that term
        4. Ask Claude to pick the best proxy from those candidates
        5. Return (FOODCODE, description)

        PROXY_HINTS prevent common errors like plant-based dairy alternatives
        being classified as dairy instead of nuts/oils.
        """
        if not self.use_claude or not self._anthropic_key:
            return None, ""

        key = ingredient_name.lower().strip()

        # Step 1: Check PROXY_HINTS for explicit guidance
        hint = PROXY_HINTS.get(key)
        if hint:
            search_term, reason = hint
            print(f"  [step3b] Using proxy hint: search '{search_term}' ({reason})")
        else:
            # Step 1b: Ask Claude what food group and search term to use
            prompt1 = (
                f'The ingredient "{ingredient_name}" is not in the USDA FPED food '
                f'database used for HEI-2020 scoring.\n\n'
                f'IMPORTANT: Plant-based dairy alternatives (oat milk, cashew cream, '
                f'almond milk, vegan cheese, etc.) are NOT dairy -- they belong to '
                f'nuts/seeds/oils or grains for food group purposes.\n\n'
                f'For food group classification purposes:\n'
                f'1. What HEI food group does this belong to? '
                f'(protein foods, dairy, vegetables, grains, oils, nuts/seeds, etc.)\n'
                f'2. What 1-3 word search term would find the best FPED proxy?\n\n'
                f'Reply in this exact format:\n'
                f'GROUP: [food group]\n'
                f'SEARCH: [search term]'
            )
            answer1 = self._claude_call(prompt1, max_tokens=60)
            if not answer1:
                return None, ""

            search_term = ""
            for line in answer1.split("\n"):
                if line.startswith("SEARCH:"):
                    search_term = line.replace("SEARCH:", "").strip().lower()
                    break

            if not search_term:
                return None, ""

        # Step 2: Find FPED entries matching that search term
        if "DESCRIPTION" not in self._df.columns:
            return None, ""

        mask = self._df["DESCRIPTION"].str.lower().str.contains(
            search_term, na=False, regex=False)
        matches = self._df[mask]

        if len(matches) == 0:
            # Try first word only
            search_term = search_term.split()[0] if search_term.split() else ""
            if search_term:
                mask = self._df["DESCRIPTION"].str.lower().str.contains(
                    search_term, na=False, regex=False)
                matches = self._df[mask]

        if len(matches) == 0:
            return None, ""

        # Step 3: Ask Claude to pick the best proxy from matches
        candidates = list(zip(
            matches.index[:12].tolist(),
            matches["DESCRIPTION"].head(12).tolist()
        ))
        lines = "\n".join(f"  {c}: {d}" for c, d in candidates)

        prompt2 = (
            f'I need a food group proxy for "{ingredient_name}" in the USDA '
            f'FPED database for HEI-2020 scoring.\n\n'
            f'This ingredient has no direct FPED entry. Choose the entry '
            f'below whose food group contribution (protein oz eq, vegetable '
            f'cup eq, etc.) would best approximate "{ingredient_name}".\n\n'
            f'Available entries:\n{lines}\n\n'
            f'Reply with only the 8-digit FOODCODE of your choice, or NONE.'
        )
        answer2 = self._claude_call(prompt2, max_tokens=20)
        if not answer2 or answer2.upper() == "NONE":
            return None, ""

        match = re.search(r"\d{8}", answer2)
        if match:
            code = match.group(0)
            if code in self._df.index:
                desc = str(self._df.loc[code].get("DESCRIPTION", ""))
                return code, desc

        return None, ""

    # -------------------------------------------------------------------------
    # USDA API and fuzzy matching
    # -------------------------------------------------------------------------

    def _get_fndds_candidates(
        self, ingredient_name: str, n: int = 8
    ) -> list[tuple[str, str]]:
        query = ingredient_name.split(",")[0].strip()
        params = {"query": query, "pageSize": 15, "api_key": self.api_key}
        try:
            resp = requests.get(FNDDS_SEARCH_URL, params=params, timeout=10)
            resp.raise_for_status()
            foods = resp.json().get("foods", [])
        except Exception:
            return []

        candidates = []
        for food in foods:
            if food.get("dataType", "") != "Survey (FNDDS)":
                continue
            desc = food.get("description", "")
            code = self._description_to_foodcode(desc)
            if code and len(candidates) < n:
                candidates.append((code, desc))
        return candidates

    def _description_to_foodcode(self, description: str) -> str | None:
        if "DESCRIPTION" not in self._df.columns:
            return None
        fragment = description[:30].lower()
        if len(fragment) < 4:
            return None
        mask = self._df["DESCRIPTION"].str.lower().str.contains(
            fragment, na=False, regex=False)
        matches = self._df[mask]
        return str(matches.index[0]) if len(matches) > 0 else None

    def _fuzzy_match(self, ingredient_name: str) -> str | None:
        if "DESCRIPTION" not in self._df.columns:
            return None
        base = ingredient_name.lower().split(",")[0].strip()
        for word in ["cooked", "raw", "canned", "baked", "fresh",
                     "shredded", "diced", "sliced", "frozen", "ground"]:
            base = base.replace(word, "").strip()
        base = " ".join(base.split())
        for n in range(len(base.split()), 0, -1):
            query = " ".join(base.split()[:n])
            if len(query) < 3:
                continue
            mask = self._df["DESCRIPTION"].str.lower().str.contains(
                query, na=False, regex=False)
            matches = self._df[mask]
            if len(matches) > 0:
                return str(matches.index[0])
        return None

    # -------------------------------------------------------------------------
    # Per-ingredient lookup
    # -------------------------------------------------------------------------

    def lookup_ingredient(
        self, ingredient_name: str, grams: float
    ) -> tuple[dict | None, str]:
        key = ingredient_name.lower().strip()
        if key in FPED_SKIP:
            return None, "skipped"

        code, source = self.find_fped_code(ingredient_name)
        if code is None or code not in self._df.index:
            return None, source if source != "skipped" else "not_found"

        row    = self._df.loc[code]
        factor = grams / 100.0
        result = {}
        for fped_col, our_label in FPED_COLUMNS.items():
            if fped_col in row.index:
                result[our_label] = round(float(row[fped_col]) * factor, 4)
        result["_matched_description"] = str(row.get("DESCRIPTION", ""))
        result["_foodcode"]            = code
        return result, source

    # -------------------------------------------------------------------------
    # Meal and tray summation
    # -------------------------------------------------------------------------

    def lookup_meal(self, ingredients: list[dict]) -> dict:
        result = self._sum_ingredients(ingredients, label="meal")
        self.save()
        return result

    def lookup_tray(self, items: list[dict]) -> dict:
        """
        Pool all tray items (main + sides + drink) into combined food group
        totals for HEI scoring as one full lunch.

        Parameters
        ----------
        items : list of {"meal_name": str, "ingredients": list[dict]}
        """
        all_ingredients = []
        item_results    = []

        for item in items:
            name = item.get("meal_name", "Item")
            ings = item.get("ingredients", [])
            print(f"\n[step3b] Processing: {name}")
            result = self._sum_ingredients(ings, label=name)
            item_results.append({"meal_name": name, "result": result})
            all_ingredients.extend(ings)

        print(f"\n[step3b] Combining {len(items)} items "
              f"({len(all_ingredients)} total ingredients)")
        combined = self._sum_ingredients(all_ingredients, label="full tray")
        combined["tray_items"] = item_results
        self.save()
        return combined

    def _sum_ingredients(self, ingredients: list[dict], label: str = "") -> dict:
        totals:  dict[str, float] = {v: 0.0 for v in FPED_COLUMNS.values()}
        found    = []
        missing  = []
        skipped  = []
        flagged  = []

        for ing in ingredients:
            name  = ing.get("name", ing.get("ingredient_name", ""))
            grams = float(ing.get("grams", 0))

            result, source = self.lookup_ingredient(name, grams)
            time.sleep(0.05)

            if source == "skipped":
                skipped.append(name)
                print(f"  [FPED] '{name}' ... skipped")
                continue

            if result is None:
                missing.append(name)
                print(f"  [FPED] '{name}' ... NOT FOUND")
                continue

            desc = result.pop("_matched_description", "")
            code = result.pop("_foodcode", "")

            source_labels = {
                "direct":         "[direct]",
                "proxy":          "[proxy]",
                "learned_claude": "[learned]",
                "learned_proxy":  "[learned-proxy]",
                "learned_fuzzy":  "[learned-fuzzy] ***REVIEW***",
                "claude":         "[claude]",
                "api":            "[api] ***REVIEW***",
                "fuzzy":          "[fuzzy] ***REVIEW***",
            }
            label_str = source_labels.get(source, f"[{source}]")
            print(f"  [FPED] '{name}' ... {label_str} {desc[:55]}")

            if "REVIEW" in source_labels.get(source, ""):
                flagged.append({
                    "ingredient": name,
                    "matched":    desc,
                    "foodcode":   code,
                    "source":     source,
                })

            for lbl, val in result.items():
                totals[lbl] = totals.get(lbl, 0) + val
            found.append(name)

        totals = {k: round(v, 4) for k, v in totals.items()}

        hei_components = {
            hei_label: round(sum(totals.get(col, 0) for col in cols), 4)
            for hei_label, cols in HEI_COMPONENTS_FROM_FPED.items()
        }

        searchable = len(ingredients) - len(skipped)
        pct = round(len(found) / max(searchable, 1) * 100, 1)

        if flagged:
            print(f"\n  [step3b] *** {len(flagged)} match(es) need review:")
            for f in flagged:
                print(f"    '{f['ingredient']}' -> [{f['foodcode']}] "
                      f"'{f['matched']}' (via {f['source']})")
            print("  Add verified entries to DIRECT_FPED_MAP.\n")

        if label:
            print(f"[step3b] {label}: {len(found)} found, "
                  f"{len(skipped)} skipped, {len(missing)} missing "
                  f"({pct}% of searchable ingredients matched)")

        return {
            "food_groups":     totals,
            "hei_components":  hei_components,
            "flagged_matches": flagged,
            "coverage": {
                "found":        len(found),
                "skipped":      len(skipped),
                "missing":      len(missing),
                "total":        len(ingredients),
                "pct":          pct,
                "missing_list": missing,
            },
        }

    # -------------------------------------------------------------------------
    # Pretty print and stats
    # -------------------------------------------------------------------------

    def explain_food_groups(self, result: dict, meal_name: str = "Meal") -> None:
        cov = result["coverage"]
        hei = result["hei_components"]
        print(f"\n{'─'*60}")
        print(f"  Food Group Equivalents -- {meal_name}")
        print(f"  Found: {cov['found']}  Skipped: {cov['skipped']}  "
              f"Missing: {cov['missing']}")
        if cov["missing_list"]:
            print(f"  Not matched: {', '.join(cov['missing_list'])}")
        if result.get("flagged_matches"):
            print(f"  *** {len(result['flagged_matches'])} match(es) flagged "
                  f"for review -- see {self.cache_path} ***")
        print(f"{'─'*60}")
        print(f"  {'HEI Component':<32} {'Amount':>8}  Unit")
        print(f"  {'─'*56}")
        units = {
            "Total Fruits": "cup eq.", "Whole Fruits": "cup eq.",
            "Total Vegetables": "cup eq.", "Greens and Beans": "cup eq.",
            "Whole Grains": "oz eq.", "Dairy": "cup eq.",
            "Total Protein Foods": "oz eq.",
            "Seafood and Plant Proteins": "oz eq.",
            "Refined Grains": "oz eq.", "Added Sugars": "tsp eq.",
            "Oils": "g", "Solid Fats": "g",
        }
        zero_flag = {"Total Fruits", "Total Vegetables", "Whole Grains", "Dairy"}
        for lbl, val in hei.items():
            unit = units.get(lbl, "")
            flag = "  <- ZERO" if val == 0 and lbl in zero_flag else ""
            print(f"  {lbl:<32} {val:>8.3f}  {unit}{flag}")
        print(f"{'─'*60}\n")

    def cache_stats(self) -> None:
        if not self._learned:
            print("[step3b] Learned cache is empty.")
            return
        by_source = {}
        for v in self._learned.values():
            s = v.get("source", "unknown")
            by_source[s] = by_source.get(s, 0) + 1
        total_uses = sum(v.get("runs", 0) for v in self._learned.values())
        print(f"\n[step3b] Learned cache ({self.cache_path}):")
        print(f"  Total entries      : {len(self._learned)}")
        for source, count in sorted(by_source.items()):
            print(f"  {source:<20}: {count}")
        print(f"  API/Claude calls saved by cache: {total_uses}")
        fuzzy = [k for k, v in self._learned.items()
                 if v.get("source") == "fuzzy"]
        if fuzzy:
            print(f"  Fuzzy entries needing review: {fuzzy}")
        print()


# -----------------------------------------------------------------------------
# Standalone test
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    config.load_keys()
    fped = FPEDLookup("FPED_1718.xlsx")

    taco_ingredients = [
        {"name": "ground beef, cooked",        "grams": 60},
        {"name": "flour tortilla",             "grams": 45},
        {"name": "cheddar cheese, shredded",   "grams": 20},
        {"name": "lettuce, iceberg, shredded", "grams": 15},
        {"name": "tomatoes, fresh, diced",     "grams": 20},
        {"name": "sour cream",                 "grams": 15},
        {"name": "vegetable oil",              "grams":  5},
    ]

    plant_ingredients = [
        {"name": "tempeh, cooked",     "grams": 60},
        {"name": "flour tortilla",     "grams": 45},
        {"name": "nutritional yeast",  "grams": 15},
        {"name": "lettuce, raw",       "grams": 15},
        {"name": "tomatoes, raw",      "grams": 20},
        {"name": "vegetable oil",      "grams":  5},
    ]

    salsa_ingredients = [
        {"name": "corn, canned",        "grams": 40},
        {"name": "black beans, canned", "grams": 30},
        {"name": "tomatoes, raw",       "grams": 20},
        {"name": "lime juice",          "grams":  5},
    ]

    print("\n" + "="*60)
    print("  TEST 1 -- original taco (all should be [direct])")
    print("="*60)
    result = fped.lookup_meal(taco_ingredients)
    fped.explain_food_groups(result, "Soft Shell Taco")

    print("\n" + "="*60)
    print("  TEST 2 -- plant-based (tempeh + nutritional yeast = proxy)")
    print("="*60)
    result2 = fped.lookup_meal(plant_ingredients)
    fped.explain_food_groups(result2, "Plant-Based Taco")

    print("\n" + "="*60)
    print("  TEST 3 -- full tray")
    print("="*60)
    tray = fped.lookup_tray([
        {"meal_name": "Soft Shell Taco",   "ingredients": taco_ingredients},
        {"meal_name": "Corn & Bean Salsa", "ingredients": salsa_ingredients},
    ])
    fped.explain_food_groups(tray, "Full Tray")

    fped.cache_stats()
