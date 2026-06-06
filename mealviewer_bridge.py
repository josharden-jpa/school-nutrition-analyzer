# mealviewer_bridge.py
# Score a MealViewer school's lunch through the same tray engine as Nutrislice.
# MealViewer nests by menu line, not date -- but each item carries its own
# menu_Block_Date and block_Name, so we group by the item's own date and filter
# to lunch, then hand Nutrislice-shaped day dicts to tray_score.score_from_days.
import requests
from datetime import timedelta
import tray_score

API = "https://api.mealviewer.com/api/v4/school/{slug}/{d1}/{d2}/"

# MealViewer item_Type (case varies) -> the tray role tray_model expects.
ROLE_MAP = {
    "ENTREES": "entree", "ENTREE": "entree", "MAIN ENTREE": "entree",
    "FRUIT": "fruit", "FRUITS": "fruit",
    "VEGETABLES": "vegetable", "VEGETABLE": "vegetable",
    "MILK": "beverage",
}
# USDA nutrient codes in nutritionals[] -> the keys tray_score.tray_nutrients reads.
NUT = {208: "calories", 307: "mg_sodium", 606: "g_saturated_fat"}


def _nutrients(item):
    out = {}
    for n in item.get("nutritionals") or []:
        code = n.get("nutrientCode")
        if code in NUT and n.get("isValid", True):
            out[NUT[code]] = n.get("value") or 0
    return out


def _all_food_items(data):
    """Recursively collect every foodItem dict anywhere in the payload."""
    found, seen = [], set()

    def walk(o):
        if isinstance(o, dict):
            if o.get("object") == "foodItem" or "item_Name" in o:
                k = (o.get("item_Id"), o.get("menu_Block_Date"), o.get("block_Name"))
                if k not in seen:
                    seen.add(k)
                    found.append(o)
                return                      # don't descend into a foodItem's own fields
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    return found


def fetch_days(slug, weeks, meal="lunch"):
    """Reshape into Nutrislice-style day dicts.
    The meal's entrees/sides are dated under the meal block; milk and much of the
    fruit/veg are 'available daily' (block None, sentinel date 0001-01-01). We pair
    each dated lunch day with that daily pool so every day has a complete tray set."""
    dated = {}        # date -> {(name, role): {"food": ...}}
    daily = {}        # (name, role): {"food": ...}  -- available every lunch day
    for w in weeks:
        d1 = w.strftime("%m-%d-%Y")
        d2 = (w + timedelta(days=6)).strftime("%m-%d-%Y")
        try:
            data = requests.get(API.format(slug=slug, d1=d1, d2=d2),
                                timeout=20, headers={"Accept": "application/json"}).json()
        except Exception:
            continue
        for it in _all_food_items(data):
            role = ROLE_MAP.get((it.get("item_Type") or "").strip().upper())
            if not role:
                continue
            name = (it.get("item_Name") or "").strip().title()
            food = {"food": {"name": name, "food_category": role,
                             "rounded_nutrition_info": _nutrients(it)}}
            date  = (it.get("menu_Block_Date") or "")[:10]
            block = it.get("block_Name") or ""
            if block == "" or date in ("", "0001-01-01"):        # daily-available
                daily[(name, role)] = food
            elif meal.lower() in block.lower():                  # dated meal item
                dated.setdefault(date, {})[(name, role)] = food

    days = []
    for date in sorted(dated):
        pool = dict(daily)            # daily milk/fruit/veg available to every day
        pool.update(dated[date])      # plus that day's rotating lunch entrees/sides
        days.append({"date": date, "menu_items": list(pool.values())})
    return days


def score(slug, weeks, grade_band="9-12", label=None, meal="lunch"):
    days = fetch_days(slug, weeks, meal)
    role_days = sum(1 for d in days
                    if {f["food"]["food_category"] for f in d["menu_items"]}
                    >= {"entree", "fruit", "vegetable", "beverage"})
    print(f"  {slug}: {len(days)} day(s) pulled, {role_days} with all four roles")
    return tray_score.score_from_days(days, grade_band=grade_band, label=label or slug)


if __name__ == "__main__":
    from datetime import date
    weeks = [date(2026, 4, 6) + timedelta(weeks=i) for i in range(8)]
    dist = score("JAMESMADISONHIGH", weeks, grade_band="9-12",
                 label="Dallas Madison HS (spring)")
    if dist:
        tray_score.show_summary(dist)
