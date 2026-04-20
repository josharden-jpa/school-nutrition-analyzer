# -*- coding: utf-8 -*-
"""
step3_usda.py
Read the ingredients CSV produced by step2, look up each ingredient in the
USDA FoodData Central API, scale nutrients to the ingredient's gram weight,
and return a dict of total nutrient amounts for the full meal serving.
"""

import csv
import re
import time
import requests
import config
from config import NUTRIENT_MAP

# ── Endpoints — same pattern as your usda_compare_test.py ────────────────────
SEARCH_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"
DETAIL_URL = "https://api.nal.usda.gov/fdc/v1/food/{}"   # fill in fdc_id


# Ingredients to skip entirely — too generic or too small to search reliably.
# These either return wrong matches (salt → salted butter) or contribute
# negligible nutrition at the gram weights used in recipes.
SKIP_INGREDIENTS = {
    "salt", "pepper", "black pepper", "sea salt", "kosher salt",
    "cumin", "chili powder", "garlic powder", "onion powder", "oregano",
    "paprika", "cayenne", "coriander", "turmeric", "bay leaf",
    "water", "ice", "baking powder", "baking soda",
}

# For ingredients where USDA search returns bad matches, map directly to a known
# FDC ID rather than relying on the search endpoint.
# ⚠️  ONLY add IDs you have personally verified at fdc.nal.usda.gov
# Format: "ingredient name as claude writes it (lowercase)": fdc_id
DIRECT_FDC_MAP = {
    # ── Verified working in actual runs ───────────────────────────────────────
    "tomatoes, fresh, diced":       170457,   # Tomatoes, red, ripe, raw ✅
    "tomato, fresh, diced":         170457,
    "tomatoes, raw":                170457,
    "tomato, raw":                  170457,
    "tomatoes, diced":              170457,
    "ground beef, cooked":          174032,   # Beef, ground, 80% lean, cooked ✅
    "ground beef":                  174032,
    "nutritional yeast":            168875,   # Nutritional yeast ✅
    "tofu, extra firm":             174290,   # Tofu, extra firm, nigari ✅
    "tofu, extra firm, cooked":     174290,
    "tempeh, cooked":               174272,   # Tempeh ✅
    "tempeh":                       174272,
    "vegetable oil":                172370,   # Oil, vegetable, soybean ✅
    "lettuce, raw":                 169248,   # Lettuce, iceberg ✅
    "lettuce, iceberg, raw":        169248,
    "lettuce, iceberg, shredded":   169248,
    "cheddar cheese":               328637,

    # ── Add verified IDs here as you find them ────────────────────────────────
    # Go to fdc.nal.usda.gov, search the ingredient, click the SR Legacy result,
    # copy the number from the URL, paste it here.
    # Example:
    # "cheddar cheese, shredded":   XXXXXX,  # Cheese, cheddar — verified MM/DD/YY
}

# Ingredients whose high nutrient values are correct and should NOT trigger
# the sanity flag — pure fats and oils are legitimately 100% fat.
SANITY_WHITELIST = {
    "vegetable oil", "oil, vegetable",
    "olive oil", "oil, olive",
    "canola oil", "oil, canola",
    "coconut oil", "oil, coconut",
    "butter", "butter, unsalted", "butter, salted",
    "margarine", "shortening",
}

# ── Nutrient sanity thresholds (per 100g of a single ingredient) ─────────────
# If USDA returns a value above these for a single ingredient, the match is
# probably wrong. Values represent realistic upper bounds for whole foods.
# Pure fats/oils are excluded via SANITY_WHITELIST above.
SANITY_THRESHOLDS_PER_100G = {
    "Protein (g)":       45,    # Real meat ~31g max; isolates hit 90g → flag
    "Calories (kcal)":  700,    # Most whole foods under 600; oils excluded by whitelist
    "Sodium (mg)":     2000,    # No whole food ingredient should exceed this
    "Cholesterol (mg)": 400,    # Egg yolk is ~1085mg but typical ingredients well under
}


def find_fdc_id(ingredient_name: str, api_key: str) -> tuple[int | None, str]:
    """
    Search USDA for an ingredient name, return (fdc_id, description).
    Tries SR Legacy first (most complete nutrient data), then Foundation,
    then no filter as a last resort.

    Uses pageSize=5 and picks the result whose description best matches
    the cooking state specified in the ingredient name (cooked, canned, raw).
    """
    # Keywords in the ingredient name that indicate cooking state
    name_lower = ingredient_name.lower()
    prefer_cooked = any(w in name_lower for w in ["cooked", "canned", "baked", "roasted", "boiled"])
    prefer_raw    = "raw" in name_lower

    # For cheese, extract the specific type so we don't get parmesan when we want cheddar
    cheese_types = ["cheddar", "mozzarella", "parmesan", "american", "monterey", "pepper jack",
                    "swiss", "provolone", "colby", "ricotta", "feta", "brie"]
    prefer_cheese_type = next((c for c in cheese_types if c in name_lower), None)

    for data_type in ["SR Legacy", "Foundation", None]:
        params = {
            "query":    ingredient_name,
            "pageSize": 5,
            "api_key":  api_key,
        }
        if data_type:
            params["dataType"] = data_type

        resp = requests.get(SEARCH_URL, params=params, timeout=10)
        resp.raise_for_status()
        foods = resp.json().get("foods", [])
        if not foods:
            continue

        # For specific cheese types, find a result that actually matches the type
        if prefer_cheese_type:
            for food in foods:
                desc = food.get("description", "").lower()
                if prefer_cheese_type in desc:
                    return food["fdcId"], food.get("description", ingredient_name)

        # If ingredient specifies cooked/canned, prefer a result that says so
        if prefer_cooked:
            for food in foods:
                desc = food.get("description", "").lower()
                if any(w in desc for w in ["cooked", "canned", "baked", "roasted", "boiled"]):
                    return food["fdcId"], food.get("description", ingredient_name)

        # If ingredient specifies raw, prefer raw result
        if prefer_raw:
            for food in foods:
                if "raw" in food.get("description", "").lower():
                    return food["fdcId"], food.get("description", ingredient_name)

        # Otherwise just return the top result
        return foods[0]["fdcId"], foods[0].get("description", ingredient_name)

    return None, ""


def fetch_nutrients_per_100g(fdc_id: int, api_key: str) -> dict:
    """
    Pull nutrients for one FDC ID using the detail endpoint —
    exactly like your usda_compare_test.py does it.
    Returns a dict of { our_label: amount_per_100g }.
    """
    resp = requests.get(
        DETAIL_URL.format(fdc_id),
        params={"api_key": api_key},
        timeout=10,
    )
    resp.raise_for_status()
    food = resp.json()

    nutrients = {}
    for item in food["foodNutrients"]:
        usda_name = item["nutrient"]["name"]
        amount    = item.get("amount", 0) or 0

        if usda_name not in NUTRIENT_MAP:
            continue

        # USDA returns "Energy" twice: once in kcal, once in kJ (which is ~4x
        # higher and is what was inflating your calorie numbers).
        # Only keep the kcal entry.
        if usda_name == "Energy":
            unit = item["nutrient"].get("unitName", "").lower()
            if unit != "kcal":
                continue

        nutrients[NUTRIENT_MAP[usda_name]] = amount   # per 100 g

    return nutrients


def nutrients_from_csv(csv_path: str, api_key: str = None) -> dict:
    """
    Full step 3 pipeline:
      1. Read ingredients CSV (ingredient_name, grams)
      2. Search USDA for each ingredient → get FDC ID
      3. Fetch detail endpoint → real per-100g nutrient values (from USDA, not estimated)
      4. Scale to actual gram weight
      5. Sum across all ingredients → meal totals

    Returns
    -------
    {
      "totals":      { "Protein (g)": 18.4, ... },   # whole meal
      "ingredients": [
          {
            "name": "ground beef", "grams": 85,
            "fdc_id": 168608, "usda_description": "Beef, ground, 80% lean...",
            "nutrients_scaled": { "Protein (g)": 15.7, ... }
          }, ...
      ]
    }
    """
    if api_key is None:
        api_key = config.USDA_API_KEY or config.load_keys()

    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    # Initialize totals to zero for every nutrient we track
    totals      = {label: 0.0 for label in NUTRIENT_MAP.values()}
    ingredients = []
    missing     = []

    for row in rows:
        name  = row["ingredient_name"].strip()
        grams = float(row["grams"])

        # Strip any parenthetical notes Claude adds (e.g. "marshmallows (contains gelatin)")
        search_name = re.sub(r'\s*\(.*?\)', '', name).strip()

        # ── Skip ingredients that produce bad USDA matches ────────────────────
        if search_name.lower() in SKIP_INGREDIENTS:
            print(f"  Skipping '{name}' (trace seasoning — negligible nutrition)")
            continue

        # ── Step A: find FDC ID (direct map first, then search) ───────────────
        print(f"  Searching USDA: '{name}' ...", end=" ", flush=True)

        if search_name.lower() in DIRECT_FDC_MAP:
            fdc_id      = DIRECT_FDC_MAP[search_name.lower()]
            description = f"(direct map) FDC {fdc_id}"
            print(f"→ [{fdc_id}] {description}")
        else:
            fdc_id, description = find_fdc_id(search_name, api_key)

            if fdc_id is None:
                print("NOT FOUND — skipping (add manually if needed)")
                missing.append(name)
                continue

            print(f"→ [{fdc_id}] {description}")

        # ── Step B: fetch real nutrient data (per 100g) ───────────────────────
        per_100g = fetch_nutrients_per_100g(fdc_id, api_key)

        # ── Step B2: sanity check per-100g values before scaling ─────────────
        flagged = []
        if name.lower() not in SANITY_WHITELIST:
            for nutrient_label, threshold in SANITY_THRESHOLDS_PER_100G.items():
                val = per_100g.get(nutrient_label, 0)
                if val > threshold:
                    flagged.append(f"{nutrient_label}: {val:.1f} per 100g (threshold {threshold})")
        if flagged:
            print(f"\n  ⚠️  SANITY FLAG for '{name}' → [{fdc_id}] {description}")
            for f in flagged:
                print(f"      {f}")
            print(f"      → Verify at fdc.nal.usda.gov and add correct ID to DIRECT_FDC_MAP")
            print(f"      → Continuing with this entry but results may be inaccurate\n")

        # ── Step C: scale to actual gram weight (USDA is per 100g) ───────────
        # Example: 60g ground beef → factor = 0.6 → protein = 26g * 0.6 = 15.6g
        factor = grams / 100.0
        scaled = {k: round(v * factor, 3) for k, v in per_100g.items()}

        # ── Step D: add to meal totals ────────────────────────────────────────
        for label, val in scaled.items():
            totals[label] += val

        ingredients.append({
            "name":              name,
            "grams":             grams,
            "fdc_id":            fdc_id,
            "usda_description":  description,
            "nutrients_scaled":  scaled,
        })

        time.sleep(0.15)   # stay polite to the API

    totals = {k: round(v, 2) for k, v in totals.items()}

    if missing:
        print(f"\n  WARNING: {len(missing)} ingredient(s) not found: {missing}")
        print("  → You can manually add FDC IDs to the CSV and re-run if needed.")

    # ── Meal-level sanity checks ──────────────────────────────────────────────

    # Check 1: zero-nutrient ingredients — USDA found something but it had no data
    zero_nutrient = [
        i["name"] for i in ingredients
        if sum(i["nutrients_scaled"].values()) == 0
    ]
    if zero_nutrient:
        print(f"\n  ⚠️  ZERO-NUTRIENT FLAG: These ingredients returned no data from USDA:")
        for z in zero_nutrient:
            print(f"      '{z}' — verify the FDC entry has nutrient data")

    # Check 2: total calorie range for a school lunch
    total_kcal = totals.get("Calories (kcal)", 0)
    if total_kcal < 200:
        print(f"\n  ⚠️  CALORIE FLAG: Total meal = {total_kcal:.0f} kcal — seems too low for a school lunch")
        print(f"      → Recipe may be missing ingredients or gram weights are too small")
    elif total_kcal > 1200:
        print(f"\n  ⚠️  CALORIE FLAG: Total meal = {total_kcal:.0f} kcal — seems too high for a school lunch")
        print(f"      → Check for inflated gram weights or bad USDA matches")

    # Check 3: macro fat ratio — flag if fat > 55% of calories
    protein_kcal = totals.get("Protein (g)", 0) * 4
    fat_kcal     = totals.get("Total Fat (g)", 0) * 9
    carb_kcal    = totals.get("Carbohydrates (g)", 0) * 4
    macro_total  = protein_kcal + fat_kcal + carb_kcal
    if macro_total > 0:
        fat_pct = fat_kcal / macro_total * 100
        if fat_pct > 55:
            print(f"\n  ⚠️  MACRO FLAG: Fat = {fat_pct:.0f}% of calories — unusually high")
            print(f"      → Check oil/cheese/meat weights; or note as a finding in your report")

    # Check 4: calorie consistency with district-provided info
    # (only runs if expected_kcal was passed in)
    print(f"\n[step3] Done. {len(ingredients)}/{len(rows)} ingredients found in USDA.")
    print(f"[step3] Meal totals: {total_kcal:.0f} kcal  |  "
          f"Protein {totals.get('Protein (g)',0):.1f}g  |  "
          f"Fat {totals.get('Total Fat (g)',0):.1f}g  |  "
          f"Carbs {totals.get('Carbohydrates (g)',0):.1f}g")

    return {"totals": totals, "ingredients": ingredients}


def check_calorie_consistency(total_kcal: float, expected_kcal: float,
                               tolerance: float = 0.30) -> None:
    """
    Compare pipeline calorie total against district-provided calories.
    Prints a warning if they differ by more than tolerance (default 30%).
    Call this from main.py after step3 if the user provided district calories.
    """
    diff_pct = abs(total_kcal - expected_kcal) / expected_kcal
    if diff_pct > tolerance:
        print(f"\n  ⚠️  CALORIE CONSISTENCY FLAG:")
        print(f"      District reported: {expected_kcal:.0f} kcal")
        print(f"      Pipeline total:    {total_kcal:.0f} kcal")
        print(f"      Difference:        {diff_pct*100:.0f}% — exceeds {tolerance*100:.0f}% tolerance")
        print(f"      → Recipe estimate may be off; consider adjusting gram weights")
    else:
        print(f"\n  ✅  Calorie check: {total_kcal:.0f} kcal vs district {expected_kcal:.0f} kcal "
              f"({diff_pct*100:.0f}% difference — within tolerance)")


if __name__ == "__main__":
    import sys, json
    config.load_keys()
    csv_file = sys.argv[1] if len(sys.argv) > 1 else input("CSV path: ").strip()
    result   = nutrients_from_csv(csv_file)

    print(f"\n{'Nutrient':<30} {'Amount':>10}")
    print("-" * 42)
    for k, v in result["totals"].items():
        print(f"  {k:<28} {v:>10}")

    out = csv_file.replace("_ingredients.csv", "_nutrients.json")
    with open(out, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\nFull results saved → {out}")
