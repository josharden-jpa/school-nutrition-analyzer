# -*- coding: utf-8 -*-
"""
nutrislice_fped_bridge.py
Connects real Nutrislice nutrition data with FPED food group classification
to produce a complete HEI-2020 score.

THE PROBLEM IT SOLVES
---------------------
Nutrislice gives us accurate per-item nutrition labels (calories, sodium,
protein, fat, etc.) but not food group classifications (cup eq. of vegetables,
oz eq. of protein foods, etc.). HEI-2020 needs both.

FPED gives us food group classifications but requires ingredient-level data,
not whole meal names.

This module bridges them:
  1. Extract unique meal item names from Nutrislice scrape data
  2. Ask Claude to break each item into its component ingredients
     (e.g. "Crispy Chicken Smackers" -> chicken breast, bread crumbs, oil)
  3. Run those ingredients through step3b FPED lookup to get food group data
  4. Average food group data across the school year
  5. Combine with real Nutrislice nutrition numbers
  6. Score complete HEI-2020

This gives you:
  - Real nutrition numbers (from the district's own labels)
  - Real food group classifications (from USDA FPED)
  - Complete HEI-2020 score (all 13 components)

USAGE
-----
    from nutrislice_fped_bridge import complete_hei_score

    # Pass in days from nutrislice_scraper.scrape_school_year()
    result = complete_hei_score(
        days          = scraped_days,
        avg_nutrition = averaged_nutrition,
        district_name = "Onondaga Central School District",
        grade_band    = "6-8",
        fped_path     = "FPED_1718.xlsx",
    )
"""

from __future__ import annotations
import json
import os
import re
import time
import requests
from collections import defaultdict

import config


# How many of the most common items to classify via FPED
# More = more accurate but slower and more API calls
# 30 covers ~80% of what kids actually eat across a school year
MAX_ITEMS_TO_CLASSIFY = 30


# -----------------------------------------------------------------------------
# Decomposition cache
# -----------------------------------------------------------------------------
# The single biggest source of run-to-run score variance is Claude re-guessing
# the composition of generically-named items each run (e.g. "Fresh Fruit" -> 
# apple+banana one run, +strawberries+watermelon the next). That one item drove
# nearly all of a ~2pt run-to-run SD in testing.
#
# This cache stores item_name -> [ingredient list] so that any item Claude has
# already decomposed is reused verbatim on every later run and every school.
# It does NOT change scores on average; it just freezes them so the same menu
# always produces the same number. Delete the file to force fresh decompositions.

DECOMP_CACHE_FILE = "decomp_learned.json"

# High-frequency generic items, seeded with fixed sensible decompositions so they
# never re-roll. These are the items that appear across many schools and caused
# the variance. Gram weights chosen to reflect a typical school serving.
DECOMP_SEED = {
    "fresh fruit": [
        {"name": "apple, raw",  "grams": 90},
        {"name": "banana, raw", "grams": 90},
        {"name": "orange, raw", "grams": 90},
        {"name": "grapes, raw", "grams": 70},
    ],
    "strawberry slices": [
        {"name": "strawberries, raw", "grams": 85},
    ],
    "fresh strawberries": [
        {"name": "strawberries, raw", "grams": 85},
    ],
}


def load_decomp_cache(path: str = DECOMP_CACHE_FILE) -> dict:
    """Load the item-name -> ingredient-list decomposition cache, seeded."""
    cache = dict(DECOMP_SEED)   # start from the fixed seed
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                saved = json.load(f)
            # saved entries fill in non-seeded items; seed always wins for its keys
            for k, v in saved.items():
                if k not in DECOMP_SEED:
                    cache[k] = v
        except Exception as e:
            print(f"  [bridge] Could not read decomp cache ({e}); using seed only")
    return cache


def save_decomp_cache(cache: dict, path: str = DECOMP_CACHE_FILE) -> None:
    """Persist the decomposition cache (seed entries are not re-written)."""
    to_save = {k: v for k, v in cache.items() if k not in DECOMP_SEED}
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(to_save, f, indent=2)
    except Exception as e:
        print(f"  [bridge] Could not save decomp cache: {e}")


# -----------------------------------------------------------------------------
# Step 1: Extract unique items from Nutrislice day data
# -----------------------------------------------------------------------------

def extract_unique_items(days: list[dict], max_items: int = MAX_ITEMS_TO_CLASSIFY) -> list[dict]:
    """
    From a school year of daily menu data, extract the unique items
    sorted by frequency -- most commonly served first.

    Returns list of {"name": str, "frequency": int, "category": str}
    """
    counts    = defaultdict(int)
    categories = {}

    for day in days:
        for item in day.get("items", []):
            if not item.get("has_data"):
                continue
            name = item["name"].strip()
            key  = name.lower()
            counts[key] += 1
            if key not in categories:
                categories[key] = item.get("category", "")

    # Build list sorted by frequency
    seen  = {}
    items = []
    for key, count in sorted(counts.items(), key=lambda x: -x[1]):
        # Find original-case name
        for day in days:
            for item in day.get("items", []):
                if item["name"].strip().lower() == key:
                    seen[key] = item["name"].strip()
                    break
            if key in seen:
                break

        items.append({
            "name":      seen.get(key, key),
            "frequency": count,
            "category":  categories.get(key, ""),
        })

    print(f"  [bridge] {len(items)} unique items found across school year")
    if len(items) > max_items:
        print(f"  [bridge] Classifying top {max_items} most frequent items "
              f"(covers most of what kids actually eat)")
        items = items[:max_items]

    return items


# -----------------------------------------------------------------------------
# Step 2: Ask Claude to decompose each item into FPED-searchable ingredients
# -----------------------------------------------------------------------------

DECOMPOSE_SYSTEM_PROMPT = """You are a school nutrition expert. Given a school lunch item name,
list the main ingredients as they would appear in the USDA FPED food database.

Rules:
- Use simple, generic ingredient names that match USDA food descriptions
- Include cooking state (cooked, raw, canned, baked)
- Include estimated gram weight per typical school serving
- List only nutritionally significant ingredients (skip salt, pepper, spices)
- Maximum 6 ingredients per item
- Do NOT add cooking oil, butter, or other added fat to items that are served
  RAW or COLD (e.g. raw vegetable sticks/coins, fresh fruit, salads, side
  cucumbers/carrots, cold fruit cups). Added fat only belongs on items that are
  actually fried, sauteed, griddled, roasted, or baked in fat. When unsure
  whether an item is cooked in fat, do NOT add oil.
- For a plain raw vegetable or fruit side, list ONLY the vegetable or fruit
  itself (e.g. "carrots, raw" or "cucumber, raw") with no oil and no dressing
  unless the item name explicitly names a dressing or sauce.

Return ONLY a JSON array, no markdown, no explanation:
[
  {"name": "chicken breast, cooked", "grams": 60},
  {"name": "bread crumbs, dry", "grams": 15},
  {"name": "vegetable oil", "grams": 8}
]"""


def decompose_item(item_name: str, anthropic_key: str) -> list[dict]:
    """
    Ask Claude to decompose a Nutrislice item name into FPED-searchable ingredients.
    Returns list of {"name": str, "grams": float}.
    """
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "Content-Type":      "application/json",
                "x-api-key":         anthropic_key,
                "anthropic-version": "2023-06-01",
            },
            json={
                "model":      config.ANTHROPIC_MODEL_FAST,
                "max_tokens": 300,
                "system":     DECOMPOSE_SYSTEM_PROMPT,
                "messages":   [{"role": "user", "content": f"Item: {item_name}"}],
            },
            timeout=15,
        )
        resp.raise_for_status()
        raw = resp.json()["content"][0]["text"].strip()
        clean = re.sub(r"```(?:json)?|```", "", raw).strip()
        ingredients = json.loads(clean)
        return ingredients if isinstance(ingredients, list) else []
    except Exception as e:
        print(f"    [bridge] Decompose failed for '{item_name}': {e}")
        return []


# -----------------------------------------------------------------------------
# Step 3: Run ingredients through FPED and get food group totals
# -----------------------------------------------------------------------------

def get_fped_for_item(
    item_name:    str,
    ingredients:  list[dict],
    fped:         object,   # FPEDLookup instance
) -> dict | None:
    """
    Run a decomposed item's ingredients through FPED lookup.
    Returns hei_components dict or None.
    """
    if not ingredients:
        return None
    try:
        result = fped.lookup_meal(ingredients)
        return result.get("hei_components")
    except Exception as e:
        print(f"    [bridge] FPED lookup failed for '{item_name}': {e}")
        return None


# -----------------------------------------------------------------------------
# Step 4: Average food group data weighted by item frequency
# -----------------------------------------------------------------------------

def average_fped_by_frequency(
    item_fped:  dict[str, dict],   # item_name -> hei_components
    item_freq:  dict[str, int],    # item_name -> frequency
) -> dict:
    """
    Compute frequency-weighted average of FPED food group components
    across all classified items.

    This approximates what the average school day looks like in food group terms.
    """
    totals      = defaultdict(float)
    total_freq  = sum(item_freq.values())

    if total_freq == 0:
        return {}

    for item_name, hei_components in item_fped.items():
        if not hei_components:
            continue
        freq   = item_freq.get(item_name, 1)
        weight = freq / total_freq

        for component, value in hei_components.items():
            totals[component] += (value or 0) * weight

    return {k: round(v, 4) for k, v in totals.items()}


# -----------------------------------------------------------------------------
# Main integration function
# -----------------------------------------------------------------------------

def complete_hei_score(
    days:          list[dict],
    avg_nutrition: dict,
    district_name: str,
    grade_band:    str  = "6-8",
    fped_path:     str  = "FPED_1718.xlsx",
    max_items:     int  = MAX_ITEMS_TO_CLASSIFY,
) -> dict:
    """
    Produce a complete HEI-2020 score combining:
      - Real Nutrislice nutrition data (nutrients)
      - FPED food group classification (food groups)

    Parameters
    ----------
    days          : output of nutrislice_scraper.scrape_school_year()
    avg_nutrition : output of nutrislice_scraper.average_nutrition()
    district_name : display name
    grade_band    : "K-5", "6-8", "9-12"
    fped_path     : path to FPED_1718.xlsx
    max_items     : how many unique items to classify (more = more accurate)

    Returns
    -------
    Complete score dict from score_district.score_meal()
    """
    print(f"\n{'='*60}")
    print(f"  Complete HEI-2020 Scoring")
    print(f"  District: {district_name}")
    print(f"  Combining real Nutrislice nutrition + FPED food groups")
    print(f"{'='*60}\n")

    # Load Anthropic key
    anthropic_key = ""
    if os.path.exists("anthropicapikey.txt"):
        with open("anthropicapikey.txt") as f:
            anthropic_key = f.readline().strip()
    else:
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not anthropic_key:
        print("  [bridge] No Anthropic key -- cannot decompose items")
        print("  [bridge] Falling back to partial score")
        return _partial_fallback(avg_nutrition, district_name, grade_band, len(days))

    # Check FPED available
    if not os.path.exists(fped_path):
        print(f"  [bridge] FPED file not found at '{fped_path}'")
        print("  [bridge] Falling back to partial score")
        return _partial_fallback(avg_nutrition, district_name, grade_band, len(days))

    # Step 1: Extract unique items
    print("  Step 1: Extracting unique menu items...")
    unique_items = extract_unique_items(days, max_items)
    print(f"  Found {len(unique_items)} items to classify\n")

    # Step 2: Load FPED
    print("  Step 2: Loading FPED database...")
    try:
        import step3b_fped as fped_module
        fped = fped_module.FPEDLookup(fped_path)
    except Exception as e:
        print(f"  [bridge] FPED load failed: {e}")
        return _partial_fallback(avg_nutrition, district_name, grade_band, len(days))

    # Step 3: Decompose each item and get FPED food groups
    print("  Step 3: Classifying items via Claude + FPED...")
    item_fped_data = {}
    item_frequencies = {}

    decomp_cache = load_decomp_cache()
    decomp_dirty = False

    for i, item in enumerate(unique_items, 1):
        name = item["name"]
        freq = item["frequency"]
        item_frequencies[name] = freq

        print(f"  [{i:2}/{len(unique_items)}] {name} (served {freq}x)")

        # Decompose -- reuse cached decomposition if we've seen this item name
        key = name.strip().lower()
        if key in decomp_cache:
            ingredients = decomp_cache[key]
            cache_tag = " [decomp cached]"
        else:
            ingredients = decompose_item(name, anthropic_key)
            cache_tag = ""
            if ingredients:
                decomp_cache[key] = ingredients
                decomp_dirty = True

        if not ingredients:
            print(f"    -> No ingredients found, skipping")
            continue

        ing_str = ", ".join(f"{ig['name']} ({ig['grams']}g)"
                            for ig in ingredients[:4])
        print(f"    -> {ing_str}{cache_tag}")

        # Run through FPED
        hei_components = get_fped_for_item(name, ingredients, fped)
        if hei_components:
            item_fped_data[name] = hei_components
            # Show key food groups
            veg = hei_components.get("Total Vegetables", 0)
            prot = hei_components.get("Total Protein Foods", 0)
            grain = hei_components.get("Whole Grains", 0) + \
                    hei_components.get("Refined Grains", 0)
            print(f"    -> veg: {veg:.3f} cup eq | "
                  f"protein: {prot:.3f} oz eq | "
                  f"grains: {grain:.3f} oz eq")

        # Only rate-limit when we actually hit the API this iteration
        if not cache_tag:
            time.sleep(0.5)

    fped.save()
    if decomp_dirty:
        save_decomp_cache(decomp_cache)
        print(f"  [bridge] Decomposition cache saved -> {DECOMP_CACHE_FILE}")

    if not item_fped_data:
        print("\n  [bridge] No items successfully classified -- partial score only")
        return _partial_fallback(avg_nutrition, district_name, grade_band, len(days))

    # Step 4: Average food groups by frequency
    print(f"\n  Step 4: Averaging food groups across {len(item_fped_data)} classified items...")
    avg_food_groups = average_fped_by_frequency(item_fped_data, item_frequencies)

    print("\n  Average daily food groups:")
    for component, val in avg_food_groups.items():
        if val > 0:
            print(f"    {component:<30} {val:.4f}")

    # Step 5: Build fped_result in the format score_meal expects
    fped_result = {
        "hei_components": avg_food_groups,
        "coverage": {
            "found":   len(item_fped_data),
            "total":   len(unique_items),
            "pct":     round(len(item_fped_data) / max(len(unique_items), 1) * 100, 1),
        },
        "source": "nutrislice_fped_bridge",
        "n_items_classified": len(item_fped_data),
        "n_items_total":      len(unique_items),
        "n_days":             len(days),
    }

    # Step 6: Score with complete data
    print(f"\n  Step 5: Scoring with complete HEI-2020 data...")
    print(f"  Coverage: {len(item_fped_data)}/{len(unique_items)} items classified "
          f"({fped_result['coverage']['pct']}%)")

    import score_district as scorer
    result = scorer.score_meal(
        nutrient_totals = avg_nutrition,
        fped_result     = fped_result,
        meal_name       = district_name,
        grade_band      = grade_band,
    )
    result["n_days_analyzed"]      = len(days)
    result["data_source"]          = "nutrislice_real_+_fped"
    result["fped_items_classified"] = len(item_fped_data)
    result["fped_items_total"]      = len(unique_items)

    scorer.explain_score(result)

    return result


def _partial_fallback(avg_nutrition, district_name, grade_band, n_days):
    """Return a partial score when FPED classification isn't possible."""
    import score_district as scorer
    result = scorer.score_meal(
        nutrient_totals = avg_nutrition,
        fped_result     = None,
        meal_name       = district_name,
        grade_band      = grade_band,
    )
    result["n_days_analyzed"] = n_days
    result["data_source"]     = "nutrislice_real_partial"
    return result


# -----------------------------------------------------------------------------
# Standalone runner -- integrate with existing Nutrislice output
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    """
    Run complete HEI scoring on an already-scraped Nutrislice dataset.

    Reads the daily CSV produced by nutrislice_scraper.py and produces
    a complete HEI score.

    Usage:
        python nutrislice_fped_bridge.py
    """
    import csv
    config.load_keys()

    print("Cafeteria Critic -- Complete HEI-2020 Scoring")
    print("="*60)

    # Load existing Nutrislice daily data
    daily_file = input("  Path to daily CSV (e.g. onondaga_..._daily.csv): ").strip()
    if not os.path.exists(daily_file):
        print(f"File not found: {daily_file}")
        exit(1)

    # Read avg nutrition from the analysis JSON
    json_file = daily_file.replace("_daily.csv", "_analysis.json")
    if not os.path.exists(json_file):
        print(f"Analysis JSON not found: {json_file}")
        exit(1)

    with open(json_file) as f:
        analysis = json.load(f)

    avg_nutrition = analysis["avg_daily_nutrition"]
    district_name = analysis["district_name"]
    school_name   = analysis.get("school_name", "")
    grade_band    = analysis.get("hei_score", {}).get("grade_band", "6-8")
    n_days        = analysis["n_days"]

    print(f"\n  District : {district_name}")
    print(f"  School   : {school_name}")
    print(f"  Days     : {n_days}")
    print(f"  Grade    : {grade_band}")

    # Reconstruct minimal days structure from daily CSV
    # (we just need item names for frequency counting)
    days = []
    with open(daily_file, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            days.append({
                "date":       row.get("date", ""),
                "day_name":   row.get("day_name", ""),
                "items":      [],   # names not in daily CSV -- will scrape fresh
                "day_totals": {},
                "n_items":    int(row.get("n_items", 0)),
            })

    # For item names we need to re-scrape -- ask for the scraper params
    print("\n  To classify food groups, we need the Nutrislice item names.")
    print("  Re-scraping one month of menus to get item list...")

    import nutrislice_scraper as ns
    slug      = input("  District slug (e.g. 'onondaga'): ").strip()
    school_id = int(input("  School ID: ").strip())
    menu_id   = int(input("  Menu type ID: ").strip())

    # Scrape just one semester to get item names (faster)
    from datetime import date
    sample_days = ns.scrape_school_year(
        district_slug = slug,
        school_id     = school_id,
        menu_type_id  = menu_id,
        school_name   = school_name,
        start         = date(2025, 9, 2),
        end           = date(2026, 1, 30),
        delay         = 0.5,
    )

    if not sample_days:
        print("Could not get item names. Exiting.")
        exit(1)

    result = complete_hei_score(
        days          = sample_days,
        avg_nutrition = avg_nutrition,
        district_name = f"{district_name} — {school_name}",
        grade_band    = grade_band,
        fped_path     = "FPED_1718.xlsx",
    )

    print(f"\n  Complete HEI Score: {result.get('total_score', 'N/A')}")
    print(f"  Grade: {result.get('letter_grade', '?')}")
    print(f"  Data source: {result.get('data_source', '?')}")
