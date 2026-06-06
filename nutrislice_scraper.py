# -*- coding: utf-8 -*-
"""
nutrislice_scraper.py
Pulls real school lunch nutrition data directly from the Nutrislice public API.
No Claude recipe estimation. No USDA lookup. Real numbers from the district.

The Nutrislice API returns per-item nutrition labels (calories, fat, sodium,
protein, etc.) exactly as shown on their public menu website. We sum those
across an entire lunch tray to get meal-level nutrition, then score with HEI-2020.

PIPELINE
--------
Nutrislice API
  -> per-item nutrition (real labels, as served)
  -> sum across tray items for each day
  -> average across the school year
  -> HEI-2020 score
  -> district_scores.json -> map

NO ESTIMATION NEEDED for Nutrislice districts. Numbers are real.

ONONDAGA CENTRAL IDs (already discovered)
-----------------------------------------
district slug : onondaga
Junior/Senior HS  -> school_id=34151, lunch_menu_id=7687  (grade 6-12)
Rockwell Elem     -> school_id=34149, lunch_menu_id=7686  (K-5)
Wheeler Elem      -> school_id=34150, lunch_menu_id=7686  (K-5)

USAGE
-----
    # Full school year, HS lunch (recommended first run)
    python nutrislice_scraper.py --district onondaga --school-id 34151 --menu-id 7687 --full-year --school-name "Junior/Senior HS"

    # Just this week (fast test, ~30 seconds)
    python nutrislice_scraper.py --district onondaga --school-id 34151 --menu-id 7687 --school-name "Junior/Senior HS"

    # List all schools and menu IDs for any district
    python nutrislice_scraper.py --district onondaga --list-schools

    # Interactive mode
    python nutrislice_scraper.py
"""

import os
import sys
import json
import csv
import time
import requests
from datetime import date, timedelta, datetime
from collections import defaultdict

SCHOOL_YEAR_START = date(2025, 9, 2)
SCHOOL_YEAR_END   = date(2026, 6, 19)

# Nutrislice field -> our label (matches config.py DAILY_VALUES keys)
# Field names confirmed from live Onondaga API response 2026-05-18
NUTRIENT_MAP = {
    "calories":           "Calories (kcal)",
    "g_fat":              "Total Fat (g)",
    "g_saturated_fat":    "Saturated Fat (g)",
    "mg_cholesterol":     "Cholesterol (mg)",
    "mg_sodium":          "Sodium (mg)",
    "g_carbs":            "Carbohydrates (g)",    # actual field name in API
    "g_total_carbs":      "Carbohydrates (g)",    # fallback alias
    "g_fiber":            "Dietary Fiber (g)",    # actual field name in API
    "g_dietary_fiber":    "Dietary Fiber (g)",    # fallback alias
    "g_sugar":            "Total Sugars (g)",
    "g_added_sugar":      "Total Sugars (g)",     # some items use this
    "g_protein":          "Protein (g)",
    "mcg_vitamin_d":      "Vitamin D (mcg)",
    "mg_vitamin_d":       "Vitamin D (mcg)",      # fallback
    "mg_calcium":         "Calcium (mg)",
    "mg_iron":            "Iron (mg)",
    "mg_potassium":       "Potassium (mg)",
    "mg_vitamin_c":       "Vitamin C (mg)",
}

EXCLUDE_CATEGORIES = {
    "condiment", "condiments", "beverage", "beverages",
    "drink", "drinks", "sauce", "dressing",
}

KNOWN_DISTRICTS = {
    "onondaga": {
        "name":  "Onondaga Central School District",
        "state": "NY",
        "lat":   42.976,
        "lng":   -76.139,
    },
}


# -----------------------------------------------------------------------------
# API
# -----------------------------------------------------------------------------

def get_schools(district_slug: str) -> list[dict]:
    url = f"https://{district_slug}.api.nutrislice.com/menu/api/schools/?format=json"
    try:
        resp = requests.get(url, timeout=15, headers={"Accept": "application/json"})
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else data.get("schools", [])
    except Exception as e:
        print(f"  [scraper] Could not fetch schools: {e}")
        return []


def get_week_data(
    district_slug: str,
    school_id:     int,
    menu_type_id:  int,
    week_date:     date,
    timeout:       int = 30,
) -> dict:
    url = (
        f"https://{district_slug}.api.nutrislice.com"
        f"/menu/api/weeks/school/{school_id}"
        f"/menu-type/{menu_type_id}"
        f"/{week_date.strftime('%Y/%m/%d')}"
    )
    try:
        resp = requests.get(url, timeout=timeout,
                            headers={"Accept": "application/json"})
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.Timeout:
        print(f"    Timeout week of {week_date} -- skipping")
        return {}
    except Exception as e:
        print(f"    Error week of {week_date}: {e}")
        return {}


# -----------------------------------------------------------------------------
# Parsing
# -----------------------------------------------------------------------------

def extract_item_nutrition(food: dict) -> dict | None:
    """
    Pull nutrition from a Nutrislice food object.
    Nutrislice nests nutrition inside food.rounded_nutrition_info.
    Returns dict of {our_label: value} or None if no data.
    """
    if not food:
        return None

    # Nutrislice puts nutrition in rounded_nutrition_info
    nutrition_src = food.get("rounded_nutrition_info") or food
    if not nutrition_src:
        return None

    nutrients  = {}
    has_data   = False
    seen_labels = set()   # avoid double-counting alias fields

    for ns_field, label in NUTRIENT_MAP.items():
        if label in seen_labels:
            continue
        val = nutrition_src.get(ns_field)
        if val is None:
            val = food.get(ns_field)   # fallback to top-level
        if val is not None:
            try:
                v = float(val)
                nutrients[label] = v
                seen_labels.add(label)
                if v > 0:
                    has_data = True
            except (TypeError, ValueError):
                pass

    return nutrients if has_data else None


def parse_week(week_data: dict) -> list[dict]:
    """
    Parse a full week of Nutrislice data into per-day summaries.
    Returns list of day dicts with totals and item lists.
    """
    days_out = []
    for day in week_data.get("days", []):
        menu_items = day.get("menu_items", [])
        if not menu_items:
            continue

        day_totals = {label: 0.0 for label in NUTRIENT_MAP.values()}
        day_items  = []
        has_any    = False

        for item in menu_items:
            food     = item.get("food") or {}
            name     = food.get("name", "").strip()
            category = (
                item.get("food_category") or
                food.get("food_category") or ""
            ).lower().strip()

            if not name:
                continue
            if category in EXCLUDE_CATEGORIES:
                continue

            nutrition = extract_item_nutrition(food)
            day_items.append({
                "name":      name,
                "category":  category,
                "nutrition": nutrition,
                "has_data":  nutrition is not None,
            })

            if nutrition:
                for label, val in nutrition.items():
                    day_totals[label] = round(day_totals[label] + val, 2)
                has_any = True

        if not has_any:
            continue

        days_out.append({
            "date":       day.get("date", ""),
            "day_name":   day.get("day_of_week", ""),
            "items":      day_items,
            "day_totals": {k: round(v, 2) for k, v in day_totals.items()},
            "n_items":    sum(1 for i in day_items if i["has_data"]),
        })

    return days_out


# -----------------------------------------------------------------------------
# School year scraping
# -----------------------------------------------------------------------------

def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def scrape_school_year(
    district_slug: str,
    school_id:     int,
    menu_type_id:  int,
    school_name:   str   = "",
    start:         date  = SCHOOL_YEAR_START,
    end:           date  = SCHOOL_YEAR_END,
    delay:         float = 1.0,
) -> list[dict]:
    all_days  = []
    week      = monday_of(start)
    n_data    = 0
    n_empty   = 0
    label     = school_name or f"school {school_id}"

    print(f"\n  Scraping {label}: {start} through {end}")
    print(f"  (~{((end - start).days // 7) + 1} weeks, ~{delay}s delay each)\n")

    while week <= end:
        data = get_week_data(district_slug, school_id, menu_type_id, week)
        days = parse_week(data) if data else []

        if days:
            all_days.extend(days)
            items_with_data = sum(
                sum(1 for i in d["items"] if i["has_data"]) for d in days)
            print(f"  {week.strftime('%Y-%m-%d')}  "
                  f"{len(days)} days  "
                  f"{items_with_data} items with nutrition")
            n_data += 1
        else:
            n_empty += 1
            print(f"  {week.strftime('%Y-%m-%d')}  empty (break/holiday/summer)")

        week  += timedelta(weeks=1)
        time.sleep(delay)

    print(f"\n  Done: {n_data} weeks with data, "
          f"{n_empty} empty, {len(all_days)} school days total")
    return all_days


# -----------------------------------------------------------------------------
# Scoring
# -----------------------------------------------------------------------------

def average_nutrition(days: list[dict]) -> dict:
    """Average daily totals across all school days."""
    totals = defaultdict(float)
    counts = defaultdict(int)
    for day in days:
        for label, val in day["day_totals"].items():
            if val and val > 0:
                totals[label] += val
                counts[label] += 1
    return {
        label: round(totals[label] / counts[label], 2)
        for label in totals if counts[label] > 0
    }


def score_nutrition(avg: dict, district_name: str, grade_band: str = "6-8",
                    n_days: int = 0) -> dict:
    """Score averaged daily nutrition using HEI-2020 nutrient components."""
    try:
        import score_district as scorer
        result = scorer.score_meal(
            nutrient_totals = avg,
            fped_result     = None,
            meal_name       = district_name,
            grade_band      = grade_band,
        )
        result["n_days_analyzed"] = n_days
        result["data_source"]     = "nutrislice_real"
        return result
    except Exception as e:
        print(f"  Scoring error: {e}")
        return {"total_score": None, "letter_grade": "?",
                "n_days_analyzed": n_days, "data_source": "nutrislice_real"}


# -----------------------------------------------------------------------------
# Output
# -----------------------------------------------------------------------------

def save_outputs(
    district_name: str,
    school_name:   str,
    days:          list[dict],
    avg:           dict,
    score:         dict,
    lat:  float = None,
    lng:  float = None,
    state: str  = None,
) -> tuple[str, str]:

    safe = (district_name + "_" + school_name).lower()
    safe = "".join(c if c.isalnum() else "_" for c in safe).strip("_")[:60]

    # Daily CSV
    daily_csv = f"{safe}_daily.csv"
    with open(daily_csv, "w", newline="", encoding="utf-8") as f:
        fields = ["date", "day_name", "n_items"] + list(NUTRIENT_MAP.values())
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for day in days:
            row = {"date": day["date"], "day_name": day["day_name"],
                   "n_items": day["n_items"]}
            row.update(day["day_totals"])
            w.writerow(row)

    # Summary CSV (feeds map_builder.py)
    summary_csv = f"{safe}_summary.csv"
    hei_score   = score.get("total_score")
    hei_grade   = score.get("letter_grade", "?")
    with open(summary_csv, "w", newline="", encoding="utf-8") as f:
        fields = ["district", "grade_band", "meal", "calories", "protein_g",
                  "fat_g", "sat_fat_g", "sodium_mg", "carbs_g", "fiber_g",
                  "hei_score", "hei_grade", "calorie_flag", "is_partial"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerow({
            "district":    district_name,
            "grade_band":  score.get("grade_band", "6-8"),
            "meal":        "DISTRICT AVERAGE",
            "calories":    round(avg.get("Calories (kcal)", 0), 1),
            "protein_g":   round(avg.get("Protein (g)", 0), 1),
            "fat_g":       round(avg.get("Total Fat (g)", 0), 1),
            "sat_fat_g":   round(avg.get("Saturated Fat (g)", 0), 1),
            "sodium_mg":   round(avg.get("Sodium (mg)", 0), 1),
            "carbs_g":     round(avg.get("Carbohydrates (g)", 0), 1),
            "fiber_g":     round(avg.get("Dietary Fiber (g)", 0), 1),
            "hei_score":   round(hei_score, 1) if hei_score else "",
            "hei_grade":   hei_grade,
            "calorie_flag": score.get("calorie_flag", ""),
            "is_partial":  True,
        })

    # Full JSON
    json_path = f"{safe}_analysis.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "district_name":  district_name,
            "school_name":    school_name,
            "state":          state,
            "lat":            lat,
            "lng":            lng,
            "n_days":         len(days),
            "generated":      datetime.now().isoformat(),
            "data_source":    "nutrislice_real_nutrition_labels",
            "methodology":    (
                "Nutrition data pulled directly from Nutrislice public API. "
                "Values are district-reported per-item nutrition labels, "
                "summed per day across all lunch items and averaged across "
                "the school year. No AI recipe estimation was used. "
                "HEI score is partial -- nutrient-based components only."
            ),
            "avg_daily_nutrition": avg,
            "hei_score":      score,
        }, f, indent=2, ensure_ascii=False)

    print(f"\n  Saved:")
    print(f"    {daily_csv}   ({len(days)} school days)")
    print(f"    {summary_csv}")
    print(f"    {json_path}")
    return json_path, summary_csv


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def analyze_district(
    district_slug: str,
    school_id:     int,
    menu_type_id:  int,
    school_name:   str   = "",
    district_name: str   = "",
    grade_band:    str   = "6-8",
    state:         str   = None,
    lat:           float = None,
    lng:           float = None,
    full_year:     bool  = True,
) -> dict:

    info = KNOWN_DISTRICTS.get(district_slug, {})
    district_name = district_name or info.get("name", district_slug.upper())
    state = state or info.get("state")
    lat   = lat   or info.get("lat")
    lng   = lng   or info.get("lng")

    print(f"\n{'='*64}")
    print(f"  District : {district_name}")
    print(f"  School   : {school_name or school_id}")
    print(f"  Source   : Nutrislice real nutrition labels (no AI estimation)")
    print(f"  Period   : {'Full 2025-2026 school year' if full_year else 'Current week'}")
    print(f"{'='*64}")

    if full_year:
        days = scrape_school_year(
            district_slug, school_id, menu_type_id, school_name)
    else:
        data = get_week_data(district_slug, school_id, menu_type_id, date.today())
        days = parse_week(data) if data else []

    if not days:
        print("  No data found. Check district slug and IDs.")
        return {}

    avg   = average_nutrition(days)
    score = score_nutrition(avg, district_name, grade_band, len(days))

    print(f"\n  Average daily nutrition across {len(days)} school days:")
    for label in ["Calories (kcal)", "Protein (g)", "Total Fat (g)",
                  "Saturated Fat (g)", "Sodium (mg)", "Carbohydrates (g)",
                  "Dietary Fiber (g)", "Total Sugars (g)"]:
        print(f"    {label:<30} {avg.get(label, 0):.1f}")

    hei = score.get("total_score")
    grade = score.get("letter_grade", "?")
    if hei:
        print(f"\n  HEI Score: {hei:.1f}/100  Grade: {grade}  [PARTIAL - nutrient components]")
    print(f"  US child avg benchmark: ~50/100 (USDA 2013)")

    save_outputs(district_name, school_name, days, avg, score, lat, lng, state)

    try:
        import map_builder
        map_builder.build_scores_json()
        print("  Map data updated -> district_scores.json")
    except Exception:
        pass

    return {"district_name": district_name, "school_name": school_name,
            "n_days": len(days), "avg_nutrition": avg, "hei_score": score,
            "lat": lat, "lng": lng, "state": state}


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def list_schools(slug: str):
    schools = get_schools(slug)
    if not schools:
        print(f"  No schools found for '{slug}'")
        return
    print(f"\n  Schools in '{slug}':")
    for s in schools:
        print(f"\n  {s['name']}  (id: {s['id']}, slug: {s['slug']})")
        for mt in s.get("active_menu_types", []):
            print(f"    {mt['name']:<30} id: {mt['id']}  slug: {mt['slug']}")


def interactive():
    print("\n" + "="*64)
    print("  Cafeteria Critic -- Nutrislice Real Data Scraper")
    print("="*64)
    print()

    slug = input("  District slug (e.g. 'onondaga'): ").strip().lower()
    if not slug:
        return

    show = input("  Fetch school/menu list from API? (y/n): ").strip().lower()
    if show == "y":
        list_schools(slug)

    school_id    = int(input("\n  School ID: ").strip())
    menu_type_id = int(input("  Menu type ID: ").strip())
    school_name  = input("  School display name: ").strip()
    grade_band   = input("  Grade band (K-5/6-8/9-12) [6-8]: ").strip() or "6-8"
    full_year    = input("  Full 2025-2026 school year? (y/n) [y]: ").strip().lower() != "n"

    analyze_district(
        district_slug = slug,
        school_id     = school_id,
        menu_type_id  = menu_type_id,
        school_name   = school_name,
        grade_band    = grade_band,
        full_year     = full_year,
    )


if __name__ == "__main__":
    args = sys.argv[1:]

    if "--list-schools" in args and "--district" in args:
        list_schools(args[args.index("--district") + 1])
        sys.exit(0)

    if "--district" in args and "--school-id" in args and "--menu-id" in args:
        slug      = args[args.index("--district") + 1]
        school_id = int(args[args.index("--school-id") + 1])
        menu_id   = int(args[args.index("--menu-id") + 1])
        full_year = "--full-year" in args
        grade     = args[args.index("--grade") + 1] if "--grade" in args else "6-8"
        sname     = args[args.index("--school-name") + 1] if "--school-name" in args else ""

        analyze_district(
            district_slug = slug,
            school_id     = school_id,
            menu_type_id  = menu_id,
            school_name   = sname,
            grade_band    = grade,
            full_year     = full_year,
        )
        sys.exit(0)

    interactive()
