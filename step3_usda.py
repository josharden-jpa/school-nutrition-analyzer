# -*- coding: utf-8 -*-
"""
step3_usda.py
Read the ingredients CSV produced by step2, look up each ingredient in the
USDA FoodData Central API, scale nutrients to the ingredient's gram weight,
and return a dict of total nutrient amounts for the full meal serving.

IMPORTANT: DIRECT_FDC_MAP must contain SR Legacy IDs ONLY.
Foundation IDs (identifiable by being in ranges like 7xxxxx, 2xxxxxx, 3xxxxx)
will 404 on the detail endpoint. Always verify via lookup_fdc.py and use the
SR Legacy section result.
"""

import csv
import re
import time
import requests
import config
from config import NUTRIENT_MAP

# ── Endpoints ─────────────────────────────────────────────────────────────────
SEARCH_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"
DETAIL_URL = "https://api.nal.usda.gov/fdc/v1/food/{}"


# ── SKIP_INGREDIENTS ──────────────────────────────────────────────────────────
SKIP_INGREDIENTS = {
    # Spices / seasonings — trace amounts, return wrong matches
    "salt", "pepper", "black pepper", "sea salt", "kosher salt",
    "cumin", "chili powder", "garlic powder", "onion powder", "oregano",
    "paprika", "cayenne", "coriander", "turmeric", "bay leaf",
    # Seasoning blends — no good USDA match, trace amounts
    "tajin", "tajin seasoning", "tajin seasoning blend",
    "chile lime seasoning", "ranch seasoning", "taco seasoning",
    # Additives — return bizarre USDA matches
    "ascorbic acid", "citric acid",
    "natural flavor", "natural flavors", "artificial flavor", "artificial flavors",
    # Processing artifacts
    "corn husks", "corn husk",
    # Neutral liquids / leavening
    "water", "ice", "baking powder", "baking soda",
    "buffalo sauce",          # hot-sauce condiment → was matching water-buffalo milk
}


# ── DIRECT_FDC_MAP ────────────────────────────────────────────────────────────
# SR Legacy IDs ONLY. Foundation IDs 404 on the detail endpoint.
# SR Legacy IDs are in the 160000-174999 range roughly.
# When in doubt, use lookup_fdc.py and take the SR Legacy section result.
DIRECT_FDC_MAP = {

    # ── PROTEINS ──────────────────────────────────────────────────────────────
    # Chicken
    "chicken, cooked":                      171477,   # Chicken, broiler, breast, meat only, cooked, roasted ✅
    "chicken, cooked, diced":               171477,
    "chicken breast, cooked":               171477,
    "chicken breast, diced, cooked":        171477,
    "chicken breast, diced":                171477,
    "chicken breast, roasted":              171477,
    "chicken, roasted":                     171477,
    "chicken breast, breaded and fried":    171477,

    # Turkey
    "turkey, sliced, cooked":               172941,   # Turkey breast, sliced, prepackaged ✅
    "turkey breast, sliced":                172941,
    "turkey breast, sliced, cooked":        172941,
    "turkey, deli sliced":                  172941,
    "turkey breast, cooked":                174516,   # Turkey, retail parts, breast, meat only, cooked, roasted ✅
    "turkey, cooked":                       174516,
    "turkey, roasted":                      174516,

    # Beef
    "ground beef, cooked":                  174032,   # Beef, ground, 80% lean, cooked ✅
    "ground beef":                          174032,

    # Plant proteins
    "tofu, extra firm":                     174290,   # Tofu, extra firm, nigari ✅
    "tofu, extra firm, cooked":             174290,
    "tempeh, cooked":                       174272,   # Tempeh ✅
    "tempeh":                               174272,
    "nutritional yeast":                    168875,   # Nutritional yeast ✅

    # Edamame — search returns bulgur first without direct map
    "edamame, cooked":                      168411,   # Edamame, frozen, prepared ✅
    "edamame, frozen, cooked":              168411,
    "edamame":                              168411,

    # ── DAIRY & EGGS ──────────────────────────────────────────────────────────
    # Cheese
    "cheddar cheese":                       170899,   # Cheese, cheddar, sharp, sliced ✅
    "cheddar cheese, shredded":             170899,
    "cheese, cheddar":                      170899,
    "cheese, cheddar, shredded":            170899,

    # Milk — SR Legacy only; bare "milk" was returning crackers
    "milk":                                 171265,   # Milk, whole, 3.25% milkfat, with added vitamin D ✅
    "milk, whole":                          171265,
    "whole milk":                           171265,
    "milk, whole, fluid":                   171265,

# Eggs — SR Legacy; bare "eggs" was returning bagels.
    # NOTE: "egg" (singular) is a SEPARATE dict key from "eggs" — Claude writes
    # the singular in baked-goods decompositions, so it needs its own entry.
    "egg":                                  171287,   # ← ADDED: singular was hitting Bagels, egg
    "eggs":                                 171287,   # Egg, whole, raw, fresh ✅
    "egg, whole":                           171287,
    "egg, whole, raw":                      171287,
    "eggs, whole":                          171287,
    
    # ── GRAINS & BREAD ────────────────────────────────────────────────────────
    # White bread — SR Legacy; Foundation 2758993 404s
    "bread, white":                         167532,   # Bread, white wheat ✅
    "bread, white, sliced":                 167532,
    "bread, white sandwich":                167532,
    "bread, white, sandwich":               167532,
    "bread, white, enriched":               167532,

    # Whole wheat bread — SR Legacy; was returning pita
    "bread, whole wheat":                   172688,   # Bread, whole-wheat, commercially prepared ✅
    "bread, whole wheat, sliced":           172688,
    "bread, whole grain":                   172688,
    "bread, whole wheat sandwich":          172688,

    # Rice
    "rice, cooked":                         168880,   # Rice, white, medium-grain, enriched, cooked ✅
    "rice, white, cooked":                  168880,
    "white rice, cooked":                   168880,

# Cornmeal — no cooked entry in USDA; dry used, calorie-anchor corrects total
    # ⚠️ dry form (~360 kcal/100g vs ~70 cooked) — food-group grams will be inflated
    "cornmeal, cooked":                     168867,   # Cornmeal, degermed, enriched, yellow (dry) ⚠️
    "cornmeal, yellow, enriched":           168867,
    "cornmeal, yellow":                     168867,
    "corn meal, yellow":                    168867,
    "corn, ground":                         168867,   # ← ADDED: was matching Chicken, ground, raw
    
    # ── VEGETABLES ────────────────────────────────────────────────────────────
    "tomatoes, fresh, diced":               170457,   # Tomatoes, red, ripe, raw ✅
    "tomato, fresh, diced":                 170457,
    "tomatoes, raw":                        170457,
    "tomato, raw":                          170457,
    "tomatoes, diced":                      170457,
    "lettuce, raw":                         169248,   # Lettuce, iceberg ✅
    "lettuce, iceberg, raw":                169248,
    "lettuce, iceberg, shredded":           169248,

    # Corn — search returns pasta/noodles without direct map
    "corn, cooked":                         168401,   # Corn, sweet, yellow, frozen, kernels, cooked, without salt ✅
    "corn, steamed":                        168401,
    "corn kernels, cooked":                 168401,
    "corn, whole kernel":                   169214,   # Corn, sweet, yellow, canned, whole kernel, drained ✅
    "corn, whole kernel, canned":           169214,
    "corn kernels, canned":                 169214,

    # ── FRUIT ─────────────────────────────────────────────────────────────────
    # Apples — search returns rose-apples without direct map
    "apples, raw":                          171689,   # Apples, raw, without skin ✅
    "apple, raw":                           171689,
    "apples, raw, with skin":               171689,
    "apple wedges":                         171689,
    "apple slices":                         171689,

    # Oranges — search returns orange peel without direct map
    "oranges, raw":                         169097,   # Oranges, raw, all commercial varieties ✅
    "orange, raw":                          169097,
    "orange segments":                      169097,

    # Grapes — search returns grape leaves without direct map
    "grapes, raw":                          174683,   # Grapes, red or green, European type, raw ✅
    "grapes, seedless":                     174683,
    "grapes, red, seedless":                174683,
    "grapes, green, seedless":              174683,

    # ── FATS & OILS ───────────────────────────────────────────────────────────
    "vegetable oil":                        172370,   # Oil, vegetable, soybean ✅
    "butter":                               173410,   # Butter, salted ✅
    "butter, salted":                       173410,

    # ── JUICES — SR Legacy only ───────────────────────────────────────────────
    "apple juice":                          167771,   # Apple juice, canned/bottled, unsweetened, with ascorbic acid ✅
    "apple juice, from concentrate":        167771,
    "apple juice, unsweetened":             167771,
    "white grape juice":                    173041,   # Grape juice, canned/bottled, unsweetened, with ascorbic acid ✅
    "grape juice, white":                   173041,
    "grape juice, unsweetened":             173041,

    # ── ADD VERIFIED SR LEGACY IDs HERE ───────────────────────────────────────
    # Run lookup_fdc.py → take the SR Legacy section result → paste here.
    # DO NOT use Foundation IDs — they 404 on the detail endpoint.
}


# Ingredients whose high nutrient values are correct — pure fats legitimately
# have very high calorie/fat density and should not trigger sanity flags.
SANITY_WHITELIST = {
    "vegetable oil", "oil, vegetable",
    "olive oil", "oil, olive",
    "canola oil", "oil, canola",
    "coconut oil", "oil, coconut",
    "butter", "butter, unsalted", "butter, salted",
    "margarine", "shortening",
}

SANITY_THRESHOLDS_PER_100G = {
    "Protein (g)":       45,
    "Calories (kcal)":  700,
    "Sodium (mg)":     2000,
    "Cholesterol (mg)": 400,
}


def find_fdc_id(ingredient_name: str, api_key: str) -> tuple[int | None, str]:
    name_lower = ingredient_name.lower()
    prefer_cooked = any(w in name_lower for w in ["cooked", "canned", "baked", "roasted", "boiled"])
    prefer_raw    = "raw" in name_lower

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

        if prefer_cheese_type:
            for food in foods:
                desc = food.get("description", "").lower()
                if prefer_cheese_type in desc:
                    return food["fdcId"], food.get("description", ingredient_name)

        if prefer_cooked:
            for food in foods:
                desc = food.get("description", "").lower()
                if any(w in desc for w in ["cooked", "canned", "baked", "roasted", "boiled"]):
                    return food["fdcId"], food.get("description", ingredient_name)

        if prefer_raw:
            for food in foods:
                if "raw" in food.get("description", "").lower():
                    return food["fdcId"], food.get("description", ingredient_name)

        return foods[0]["fdcId"], foods[0].get("description", ingredient_name)

    return None, ""


def fetch_nutrients_per_100g(fdc_id: int, api_key: str) -> dict:
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

        if usda_name == "Energy":
            unit = item["nutrient"].get("unitName", "").lower()
            if unit != "kcal":
                continue

        nutrients[NUTRIENT_MAP[usda_name]] = amount

    return nutrients


def nutrients_from_csv(csv_path: str, api_key: str = None) -> dict:
    if api_key is None:
        api_key = config.USDA_API_KEY or config.load_keys()

    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    totals      = {label: 0.0 for label in NUTRIENT_MAP.values()}
    ingredients = []
    missing     = []

    for row in rows:
        name  = row["ingredient_name"].strip()
        grams = float(row["grams"])

        search_name = re.sub(r'\s*\(.*?\)', '', name).strip()

        if search_name.lower() in SKIP_INGREDIENTS:
            print(f"  Skipping '{name}' (in skip list — trace/no good USDA match)")
            continue

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

        per_100g = fetch_nutrients_per_100g(fdc_id, api_key)

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
            print(f"      → Verify at fdc.nal.usda.gov and add correct ID to DIRECT_FDC_MAP\n")

        factor = grams / 100.0
        scaled = {k: round(v * factor, 3) for k, v in per_100g.items()}

        for label, val in scaled.items():
            totals[label] += val

        ingredients.append({
            "name":              name,
            "grams":             grams,
            "fdc_id":            fdc_id,
            "usda_description":  description,
            "nutrients_scaled":  scaled,
        })

        time.sleep(0.15)

    totals = {k: round(v, 2) for k, v in totals.items()}

    if missing:
        print(f"\n  WARNING: {len(missing)} ingredient(s) not found: {missing}")
        print("  → Add SR Legacy FDC IDs to DIRECT_FDC_MAP after running lookup_fdc.py")

    zero_nutrient = [
        i["name"] for i in ingredients
        if sum(i["nutrients_scaled"].values()) == 0
    ]
    if zero_nutrient:
        print(f"\n  ⚠️  ZERO-NUTRIENT FLAG: These ingredients returned no data from USDA:")
        for z in zero_nutrient:
            print(f"      '{z}' — verify the FDC entry has nutrient data")

    total_kcal = totals.get("Calories (kcal)", 0)
    if total_kcal < 200:
        print(f"\n  ⚠️  CALORIE FLAG: Total meal = {total_kcal:.0f} kcal — seems too low for a school lunch")
        print(f"      → Side dish or snack item — expected for non-entree items")
    elif total_kcal > 1200:
        print(f"\n  ⚠️  CALORIE FLAG: Total meal = {total_kcal:.0f} kcal — seems too high")
        print(f"      → Check for inflated gram weights or bad USDA matches")

    protein_kcal = totals.get("Protein (g)", 0) * 4
    fat_kcal     = totals.get("Total Fat (g)", 0) * 9
    carb_kcal    = totals.get("Carbohydrates (g)", 0) * 4
    macro_total  = protein_kcal + fat_kcal + carb_kcal
    if macro_total > 0:
        fat_pct = fat_kcal / macro_total * 100
        if fat_pct > 55:
            print(f"\n  ⚠️  MACRO FLAG: Fat = {fat_pct:.0f}% of calories — unusually high")
            print(f"      → Check oil/cheese/meat weights; or note as a finding in your report")

    print(f"\n[step3] Done. {len(ingredients)}/{len(rows)} ingredients found in USDA.")
    print(f"[step3] Meal totals: {total_kcal:.0f} kcal  |  "
          f"Protein {totals.get('Protein (g)',0):.1f}g  |  "
          f"Fat {totals.get('Total Fat (g)',0):.1f}g  |  "
          f"Carbs {totals.get('Carbohydrates (g)',0):.1f}g")

    return {"totals": totals, "ingredients": ingredients}


def check_calorie_consistency(total_kcal: float, expected_kcal: float,
                               tolerance: float = 0.30) -> None:
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
