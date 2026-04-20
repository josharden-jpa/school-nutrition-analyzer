# -*- coding: utf-8 -*-
"""
lookup_fdc.py
Search USDA FoodData Central for an ingredient and get back FDC IDs.
Run this whenever you need to verify or add a new entry to DIRECT_FDC_MAP.

Usage:
    python lookup_fdc.py
    python lookup_fdc.py "cheddar cheese"
"""

import sys
import requests

with open('usdaapikey.txt') as f:
    apikey = f.readline().strip()

SEARCH_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"


def lookup(query: str, n: int = 8) -> None:
    print(f"\nSearching USDA for: '{query}'")
    print("=" * 64)

    for data_type in ["SR Legacy", "Foundation"]:
        params = {
            "query":    query,
            "dataType": data_type,
            "pageSize": n,
            "api_key":  apikey,
        }
        resp = requests.get(SEARCH_URL, params=params, timeout=10)
        resp.raise_for_status()
        foods = resp.json().get("foods", [])

        if not foods:
            continue

        print(f"\n  [{data_type}]")
        for food in foods:
            print(f"  {food['fdcId']}  {food['description']}")

    print()
    print("Copy the FDC ID of the correct entry into DIRECT_FDC_MAP in step3_usda.py")
    print(f'  e.g.  "{query.lower()}": XXXXXX,')


if __name__ == "__main__":
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
    else:
        query = input("Enter ingredient to search: ").strip()

    lookup(query)
