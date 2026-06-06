# -*- coding: utf-8 -*-
"""
Created on Fri Jun  5 23:45:22 2026

@author: josha
"""

# tray_model.py — enumerate valid NSLP trays from a Nutrislice day
import itertools
import requests

# food_category (from Nutrislice) -> tray role
ROLE = {
    "entree":    "entree",
    "fruit":     "fruit",
    "vegetable": "vegetable",
    "beverage":  "beverage",
    # "condiment" intentionally excluded from v1 enumeration (handled later)
}
REQUIRED_ROLES = ["entree", "fruit", "vegetable", "beverage"]

def group_by_role(day_items):
    groups = {r: [] for r in REQUIRED_ROLES}
    for it in day_items:
        f = it.get("food")
        if not f:
            continue
        role = ROLE.get(f.get("food_category"))
        if role:
            groups[role].append(f)        # store the full food dict
    return groups

def build_trays(day_items):
    """A tray = one item from each required role. Returns list of [food,...]."""
    g = group_by_role(day_items)
    if any(not g[r] for r in REQUIRED_ROLES):
        return []                          # incomplete day, can't form a tray
    return [list(combo) for combo in itertools.product(*(g[r] for r in REQUIRED_ROLES))]

def get_day(slug, school, menu, d):
    """Return (date_str, menu_items) for the first day that has food."""
    url = (f"https://{slug}.api.nutrislice.com/menu/api/weeks/school/{school}"
           f"/menu-type/{menu}/{d.strftime('%Y/%m/%d')}")
    r = requests.get(url, timeout=15, headers={"Accept": "application/json"}).json()
    for day in r.get("days", []):
        if any(it.get("food") for it in day.get("menu_items", [])):
            return day.get("date"), day.get("menu_items", [])
    return None, []

def preview_trays(day_items, date_str=""):
    g = group_by_role(day_items)
    print(f"{date_str}  |  " + ", ".join(f"{r}:{len(g[r])}" for r in REQUIRED_ROLES))
    trays = build_trays(day_items)
    print(f"  -> {len(trays)} valid trays")
    for t in trays[:6]:
        print("     " + "  +  ".join(f["name"] for f in t))
    if len(trays) > 6:
        print(f"     ... ({len(trays) - 6} more)")
    return trays