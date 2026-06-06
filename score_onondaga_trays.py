# score_onondaga_trays.py
# Score Onondaga's fixed daily tray via the SAME engine as San Ramon's trays.
# Onondaga's feed isn't role-tagged, so roles are hand-mapped below (one entree/day).
import requests, statistics
from datetime import date
import tray_score, step3b_fped
from nutrislice_fped_bridge import load_decomp_cache

SLUG, SCHOOL, MENU = "onondaga", 34151, 7687
GRADE = "9-12"          # HS band; affects only the calorie flag, not the HEI score

# Hand-mapped tray per day: (entree, vegetable, fruit). One entree/day = no choice.
# Excluded by hand: extra starch (fries, knots, wedges, rice), desserts (churro),
# condiments (ranch, bbq, cheese sauce, dips), and 2nd veg/protein extras.
ONONDAGA_TRAYS = {
    "2026-05-04": ("Crispy Chicken Smackers",    "Crunchy Baby Carrots",      "Chilled Diced Peaches"),
    "2026-05-05": ("Walking Taco",               "Steamed Sweet Corn",        "Strawberry Slices"),
    "2026-05-06": ("Hamburger on a Bun",         "Steamed Green Beans",       "Chilled Diced Pears"),
    "2026-05-07": ("Toasted Cheese Pretzelwich", "Crunchy Baby Carrots",      "Chilled Diced Pears"),
    "2026-05-08": ("Homemade Pizza",             "Steamed Green Beans",       "Fresh Fruit"),
    "2026-05-11": ("Chicken Patty",              "Crunchy Raw Veggie Cup",    "Chilled Diced Peaches"),
    "2026-05-12": ("General Tso Chicken",        "Vegetables Blend Stir Fry", "Chilled Applesauce Cup"),
    "2026-05-13": ("Meatball Sub",               "Crunchy Raw Veggie Cup",    "Chilled Applesauce Cup"),
    "2026-05-14": ("Nacho Taco",                 "Crunchy Raw Veggie Cup",    "Chilled Diced Pears"),
    "2026-05-15": ("Homemade Pizza",             "Steamed Green Beans",       "Fresh Fruit"),
}

# Onondaga lists "Milk Choice" with no nutrition; substitute a standard nonfat
# milk so the beverage contributes the same way San Ramon's "Nonfat White Milk" did.
MILK = {"name": "Nonfat White Milk", "food_category": "beverage",
        "rounded_nutrition_info": {"calories": 90, "mg_sodium": 125, "g_saturated_fat": 0}}


def get_week(d):
    url = (f"https://{SLUG}.api.nutrislice.com/menu/api/weeks/school/{SCHOOL}"
           f"/menu-type/{MENU}/{d.strftime('%Y/%m/%d')}")
    r = requests.get(url, timeout=15, headers={"Accept": "application/json"})
    return r.json() if r.status_code == 200 else {}


# index every food by name across the two weeks
foods_by_name = {}
for wk in [date(2026, 5, 4), date(2026, 5, 11)]:
    for day in get_week(wk).get("days", []):
        for it in day.get("menu_items", []):
            f = it.get("food")
            if f:
                foods_by_name[f["name"]] = f


def find(name):
    if name in foods_by_name:
        return foods_by_name[name]
    for k, v in foods_by_name.items():        # loose fallback
        if name.lower() in k.lower():
            return v
    raise KeyError(f"not found in feed: {name!r}")


# build trays in role order: entree, fruit, vegetable, beverage
trays = {}
for d, (entree, veg, fruit) in ONONDAGA_TRAYS.items():
    trays[d] = [find(entree), find(fruit), find(veg), MILK]

# nutrient sanity: confirm the feed carries sodium / sat fat (not just calories)
print("nutrient sanity (5/04 tray):", tray_score.tray_nutrients(trays["2026-05-04"]), "\n")

# classify each unique item once (reuses San Ramon machinery + caches)
fped = step3b_fped.FPEDLookup(tray_score.FPED_PATH)
key, cache = tray_score._load_key(), load_decomp_cache()
all_foods = [f for t in trays.values() for f in t]
groups = tray_score.classify_items(all_foods, fped, key, cache)
fped.save()

# score each day's single tray
scores = []
for d, tray in trays.items():
    res = tray_score.score_tray(tray, groups, grade_band=GRADE)
    scores.append(res["total_score"])
    names = " + ".join(f["name"] for f in tray)
    print(f"{d}: {res['total_score']:5.1f} ({res['letter_grade']})  "
          f"{res['calories']:4.0f} kcal | {names}")

print(f"\nOnondaga fixed tray: mean {statistics.mean(scores):.1f}  "
      f"SD {statistics.pstdev(scores):.1f}  "
      f"floor {min(scores):.1f}  ceiling {max(scores):.1f}  ({len(scores)} days)")
