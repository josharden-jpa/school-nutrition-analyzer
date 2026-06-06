# -*- coding: utf-8 -*-
"""
map_builder.py
Aggregates district_menu.py output CSVs into a single district_scores.json
file that the map visualization reads.

As you analyze more districts, run this again to refresh the map data.
The map will update automatically next time it's opened.

Usage
-----
    python map_builder.py                    # reads all *_summary.csv in current dir
    python map_builder.py --dir ./results    # reads from a specific directory
    python map_builder.py --out scores.json  # custom output filename

Output format
-------------
{
  "generated": "2026-05-18T...",
  "n_districts": 3,
  "districts": [
    {
      "name":        "Onondaga Central School District",
      "state":       "NY",             # inferred from name if possible
      "hei_score":   41.9,
      "hei_grade":   "F",
      "n_meals":     4,
      "calories":    545.0,
      "components": {
        "Total Vegetables": 3.1,
        "Whole Grains": 0.0,
        ...
      },
      "meals": [...]   # per-meal rows from CSV
    }, ...
  ]
}
"""

import os
import csv
import json
import sys
import glob
from datetime import datetime


# State abbreviation lookup -- tries to infer state from district name
# Most district names contain state-specific terms or can be looked up
STATE_KEYWORDS = {
    "central school district": "NY",   # very common in NY
    "union free school district": "NY",
    "city school district": "NY",
    "common school district": "NY",
    "enlarged city school district": "NY",
    "unified school district": "CA",   # common in CA
    "independent school district": "TX",  # common in TX
    "community school district": "IL",
    "public schools": None,   # ambiguous
}


def infer_state(district_name: str) -> str | None:
    """Try to infer state from district name patterns."""
    name_lower = district_name.lower()
    for keyword, state in STATE_KEYWORDS.items():
        if keyword in name_lower:
            return state
    return None


def load_district_csv(csv_path: str) -> dict | None:
    """
    Load a district_summary.csv produced by district_menu.py.
    Returns a district dict or None if the file is empty/invalid.
    """
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except Exception as e:
        print(f"  [map_builder] Could not read {csv_path}: {e}")
        return None

    if not rows:
        return None

    # Find the DISTRICT AVERAGE row
    avg_row = next((r for r in rows if r.get("meal") == "DISTRICT AVERAGE"), None)
    meal_rows = [r for r in rows if r.get("meal") != "DISTRICT AVERAGE"]

    if not avg_row:
        print(f"  [map_builder] No DISTRICT AVERAGE row in {csv_path} -- skipping")
        return None

    district_name = avg_row.get("district", "Unknown District")
    hei_score = avg_row.get("hei_score", "")
    hei_grade = avg_row.get("hei_grade", "")

    try:
        hei_score = float(hei_score)
    except (ValueError, TypeError):
        hei_score = None

    # Compute average calories across meals
    cals = []
    for r in meal_rows:
        try:
            cals.append(float(r.get("calories", 0) or 0))
        except ValueError:
            pass
    avg_calories = round(sum(cals) / len(cals), 1) if cals else None

    return {
        "name":       district_name,
        "state":      infer_state(district_name),
        "hei_score":  hei_score,
        "hei_grade":  hei_grade,
        "n_meals":    len(meal_rows),
        "calories":   avg_calories,
        "is_partial": avg_row.get("is_partial", "").lower() == "true",
        "meals":      meal_rows,
        "source_csv": os.path.basename(csv_path),
    }


def build_scores_json(
    search_dir: str = ".",
    output_path: str = "district_scores.json",
) -> str:
    """
    Find all *_summary.csv files in search_dir, load them, and write
    a combined district_scores.json.

    Returns the path to the output file.
    """
    pattern = os.path.join(search_dir, "*_summary.csv")
    csv_files = sorted(glob.glob(pattern))

    if not csv_files:
        print(f"[map_builder] No *_summary.csv files found in '{search_dir}'")
        print("  Run district_menu.py first to generate district data.")
        return output_path

    print(f"[map_builder] Found {len(csv_files)} district CSV(s)")

    districts = []
    for csv_path in csv_files:
        print(f"  Loading: {os.path.basename(csv_path)}")
        district = load_district_csv(csv_path)
        if district:
            districts.append(district)
            score_str = f"{district['hei_score']:.1f}" if district['hei_score'] else "N/A"
            print(f"    -> {district['name']} | HEI: {score_str} ({district['hei_grade']})")

    # Sort by score ascending (worst first) for easy journalism
    districts.sort(key=lambda d: d["hei_score"] if d["hei_score"] else 999)

    output = {
        "generated":   datetime.now().isoformat(),
        "n_districts": len(districts),
        "score_range": {
            "min": min(d["hei_score"] for d in districts if d["hei_score"]),
            "max": max(d["hei_score"] for d in districts if d["hei_score"]),
        } if districts else {},
        "districts":   districts,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n[map_builder] Wrote {len(districts)} districts -> {output_path}")
    print(f"[map_builder] Score range: "
          f"{output['score_range'].get('min', 'N/A'):.1f} - "
          f"{output['score_range'].get('max', 'N/A'):.1f}")

    if districts:
        print(f"\n  Worst:  {districts[0]['name']} ({districts[0]['hei_score']:.1f})")
        print(f"  Best:   {districts[-1]['name']} ({districts[-1]['hei_score']:.1f})")

    return output_path


if __name__ == "__main__":
    search_dir  = "."
    output_path = "district_scores.json"

    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--dir" and i + 1 < len(args):
            search_dir = args[i + 1]
        elif arg == "--out" and i + 1 < len(args):
            output_path = args[i + 1]

    build_scores_json(search_dir, output_path)
