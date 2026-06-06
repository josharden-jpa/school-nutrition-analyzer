# mealviewer_discover.py
# Find & validate MealViewer school lookups without hand-holding.
#   - harvest:  pull every schools.mealviewer.com/school/<slug> link off a district menu page
#   - validate: hit the live API for each slug; confirm it resolves and carries scoreable items
# The menu API is its own validator: it returns the school's name/district/coords even on an
# empty day, and the item_Type roles (ENTREE / FRUIT / VEGETABLES / MILK) when menus exist.
import re, requests
from datetime import date

API     = "https://api.mealviewer.com/api/v4/school/{slug}/{d1}/{d2}/"
SLUG_RE = re.compile(r"schools\.mealviewer\.com/school/([A-Za-z0-9_\-]+)", re.I)
ROLE_TYPES = {"ENTREE", "MAIN ENTREE", "FRUIT", "VEGETABLES", "MILK"}

# Probe a week when school is in session, so empty summer weeks don't read as "dead" slugs.
DEFAULT_PROBE = (date(2026, 5, 4), date(2026, 5, 8))


def harvest_slugs(page_url):
    """Pull every schools.mealviewer.com/school/<slug> link off a district menu page."""
    try:
        html = requests.get(page_url, timeout=20,
                            headers={"User-Agent": "Mozilla/5.0"}).text
    except Exception as e:
        print(f"  couldn't fetch {page_url}: {e}")
        return []
    slugs = sorted(set(SLUG_RE.findall(html)))
    print(f"  {len(slugs)} school slug(s) found on page")
    return slugs


def validate(slug, probe=DEFAULT_PROBE):
    """Hit the API; report whether the slug resolves and what roles its menu carries."""
    d1, d2 = (d.strftime("%m-%d-%Y") for d in probe)
    url = API.format(slug=slug, d1=d1, d2=d2)
    try:
        data = requests.get(url, timeout=20, headers={"Accept": "application/json"}).json()
    except Exception:
        return {"slug": slug, "ok": False}
    loc = data.get("physicalLocation") or {}
    name = loc.get("name")
    types, n_items = set(), 0
    for grp in data.get("dailyMenus", []):
        for it in grp.get("items", []):
            n_items += 1
            t = (it.get("item_Type") or "").upper()
            if t:
                types.add(t)
    return {
        "slug": slug, "ok": bool(name), "name": name,
        "district": loc.get("districtLookup"),
        "lat": loc.get("lat"), "lng": loc.get("long"),
        "n_items": n_items,
        "has_entree": any("ENTREE" in t for t in types),
        "tray_ready": {"FRUIT", "VEGETABLES", "MILK"}.issubset(types) and any("ENTREE" in t for t in types),
        "roles": sorted(types & ROLE_TYPES),
        "all_types": sorted(types),
    }


def discover(page_url=None, slugs=None, probe=DEFAULT_PROBE):
    """Harvest (if given a page) then validate every slug. Returns a list of result dicts."""
    if page_url:
        slugs = harvest_slugs(page_url)
    rows = []
    for s in (slugs or []):
        v = validate(s, probe)
        rows.append(v)
        if not v["ok"]:
            print(f"  [BAD       ] {s:32} (no data)")
            continue
        flag = "TRAY-READY" if v["tray_ready"] else ("entree" if v["has_entree"] else "sides-only")
        print(f"  [{flag:10}] {s:32} {v['name']}  ({v['district']})  "
              f"{v['n_items']} items  roles={v['roles']}")
    ready = [r for r in rows if r.get("tray_ready")]
    print(f"\n  {len(ready)}/{len(rows)} slug(s) tray-ready (entree + fruit + veg + milk tagged)")
    return rows


if __name__ == "__main__":
    # Example: harvest + validate a whole district from its public menu page.
    #   discover(page_url="https://www.whitley.kyschools.us/meal-viewer")
    # Or validate specific slugs directly:
    #   discover(slugs=["WhitleyCountyHigh", "BostonElementary"])
    discover(slugs=["WhitleyCountyHigh", "BostonElementary"])
