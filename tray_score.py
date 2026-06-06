# tray_score.py — score every valid tray on a menu, return the HEI distribution
import os, time, statistics, requests
import tray_model
import step3b_fped
import score_district as scorer
from nutrislice_fped_bridge import (decompose_item, get_fped_for_item,
                                    load_decomp_cache, save_decomp_cache)

FPED_PATH = "FPED_1718.xlsx"


def _load_key():
    if os.path.exists("anthropicapikey.txt"):
        return open("anthropicapikey.txt").readline().strip()
    return os.environ.get("ANTHROPIC_API_KEY", "")


def _num(v):                      # labels sometimes carry None
    return v or 0


# ---- per-item food groups (decompose + FPED), each unique name done once ------
def classify_items(foods, fped, key, decomp_cache):
    groups, dirty, seen = {}, False, set()
    for f in foods:
        name = f["name"]
        if name in seen:
            continue
        seen.add(name)
        ckey = name.strip().lower()
        if ckey in decomp_cache:
            ingredients = decomp_cache[ckey]
        else:
            ingredients = decompose_item(name, key)
            if ingredients:
                decomp_cache[ckey] = ingredients; dirty = True
            time.sleep(0.4)
        if not ingredients:
            continue
        hei = get_fped_for_item(name, ingredients, fped)
        if hei:
            groups[name] = hei
    if dirty:
        save_decomp_cache(decomp_cache)
    return groups


# ---- tray-level aggregation (sum one tray, not average the basket) ------------
def tray_nutrients(tray):
    n = {"Calories (kcal)": 0.0, "Sodium (mg)": 0.0, "Saturated Fat (g)": 0.0}
    for f in tray:
        ri = f.get("rounded_nutrition_info", {}) or {}
        n["Calories (kcal)"]   += _num(ri.get("calories"))
        n["Sodium (mg)"]       += _num(ri.get("mg_sodium"))
        n["Saturated Fat (g)"] += _num(ri.get("g_saturated_fat"))
    return n


def tray_fped(tray, groups):
    summed, n_found = {}, 0
    for f in tray:
        hei = groups.get(f["name"])
        if not hei:
            continue
        n_found += 1
        for k, v in hei.items():
            summed[k] = summed.get(k, 0.0) + (v or 0)
    return summed, n_found


def score_tray(tray, groups, grade_band="K-5"):
    fped_groups, n_found = tray_fped(tray, groups)
    fped_result = {"hei_components": fped_groups,
                   "coverage": {"found": n_found, "total": len(tray)}}
    return scorer.score_meal(tray_nutrients(tray), fped_result,
                             meal_name="tray", grade_band=grade_band)


def score_day(day_items, groups, grade_band="K-5"):
    return [score_tray(t, groups, grade_band)["total_score"]
            for t in tray_model.build_trays(day_items)]


# ---- tray description / extremes reporting -----------------------------------
def _tray_record(tray, result, date_str):
    """Compact, presentable description of one tray + its score."""
    roles = tray_model.REQUIRED_ROLES
    items = {roles[i]: tray[i]["name"] for i in range(len(tray))}
    comps = {name: (c["score"], c["max_score"]) for name, c in result["components"].items()}
    return {
        "score":      result["total_score"],
        "grade":      result["letter_grade"],
        "date":       date_str,
        "calories":   result["calories"],
        "items":      items,
        "components": comps,
    }


def show_extremes(dist):
    """Pretty-print the worst and best trays in a distribution."""
    for tag, key in [("FLOOR", "floor_tray"), ("CEILING", "ceiling_tray")]:
        t = dist.get(key)
        if not t:
            continue
        print(f"\n{tag}: {t['score']} ({t['grade']})  {t['calories']:.0f} kcal  [{t['date']}]")
        for role in tray_model.REQUIRED_ROLES:
            print(f"    {role:9} {t['items'].get(role, '-')}")
        # rank by fraction of each component's max, so 'weak' = genuinely underperforming
        ranked = sorted(t["components"].items(),
                        key=lambda kv: (kv[1][0] / kv[1][1]) if kv[1][1] else 0)
        fmt = lambda kv: f"{kv[0]} {kv[1][0]:g}/{kv[1][1]:g}"
        print("    weak:   " + ", ".join(fmt(kv) for kv in ranked[:3]))
        print("    strong: " + ", ".join(fmt(kv) for kv in ranked[-3:]))


def show_summary(dist):
    """Headline read of a distribution: typical-day envelope + variance split."""
    if not dist:
        print("  (no distribution to summarize)")
        return
    print(f"\n{dist['label']}  —  {dist['n_trays']} trays over {dist['n_days']} days")
    print(f"  overall mean {dist['mean']}   (pooled SD {dist['sd']})")
    print(f"  typical day:   floor {dist['avg_day_floor']} (±{dist['avg_day_floor_sd']})  ->  "
          f"mean {dist['avg_day_mean']} (±{dist['between_day_sd']})  ->  "
          f"ceiling {dist['avg_day_ceiling']} (±{dist['avg_day_ceiling_sd']})")
    print(f"  variance:      within-day (choice) SD {dist['within_day_sd']}   |   "
          f"between-day (rotation) SD {dist['between_day_sd']}")
    print(f"  choice gap:    best-minus-worst on a typical day = "
          f"{dist['avg_day_range']} (±{dist['avg_day_range_sd']})")
    print(f"  year extremes: single worst {dist['floor']}   single best {dist['ceiling']}")
    show_extremes(dist)


# ---- vendor-agnostic scoring core --------------------------------------------
def score_from_days(all_days, grade_band="K-5", label="menu"):
    """Score a list of day dicts -> the full distribution.
    Each day: {"date": str, "menu_items": [{"food": {name, food_category,
    rounded_nutrition_info}}]}. Nutrislice and MealViewer both funnel here."""
    fped = step3b_fped.FPEDLookup(FPED_PATH)
    key, decomp_cache = _load_key(), load_decomp_cache()

    foods = [it["food"] for d in all_days
             for it in d.get("menu_items", []) if it.get("food")]
    print(f"  classifying {len(set(f['name'] for f in foods))} unique items ...")
    groups = classify_items(foods, fped, key, decomp_cache)
    fped.save()

    all_scores, per_day = [], []
    floor_rec = ceiling_rec = None
    for d in all_days:
        date_str = d.get("date")
        day_scores = []
        for tray in tray_model.build_trays(d.get("menu_items", [])):
            result = score_tray(tray, groups, grade_band)
            sc = result["total_score"]
            day_scores.append(sc)
            rec = _tray_record(tray, result, date_str)
            if floor_rec   is None or sc < floor_rec["score"]:   floor_rec   = rec
            if ceiling_rec is None or sc > ceiling_rec["score"]: ceiling_rec = rec
        if day_scores:
            all_scores.extend(day_scores)
            per_day.append({
                "date": date_str, "n": len(day_scores),
                "mean":    round(statistics.mean(day_scores), 1),
                "sd":      round(statistics.pstdev(day_scores), 1),
                "floor":   round(min(day_scores), 1),
                "ceiling": round(max(day_scores), 1),
                "range":   round(max(day_scores) - min(day_scores), 1),
            })
    if not all_scores:
        print("  no trays scored"); return None

    day_means    = [p["mean"]    for p in per_day]
    day_floors   = [p["floor"]   for p in per_day]
    day_ceilings = [p["ceiling"] for p in per_day]
    day_ranges   = [p["range"]   for p in per_day]
    # pooled within-day SD: sqrt(mean of per-day variances), each day weighted equally
    within_var = statistics.mean([p["sd"] ** 2 for p in per_day])

    return {
        "label": label, "n_trays": len(all_scores), "n_days": len(per_day),
        # pooled over every tray in the year
        "mean":    round(statistics.mean(all_scores), 1),
        "sd":      round(statistics.pstdev(all_scores), 1),
        # single best / worst tray served all year (a lottery, not a typical day)
        "floor":   round(min(all_scores), 1),
        "ceiling": round(max(all_scores), 1),
        "floor_tray":   floor_rec,
        "ceiling_tray": ceiling_rec,
        # typical-day envelope: each day's own floor/mean/ceiling, averaged over days
        "avg_day_floor":   round(statistics.mean(day_floors), 1),
        "avg_day_mean":    round(statistics.mean(day_means), 1),
        "avg_day_ceiling": round(statistics.mean(day_ceilings), 1),
        # the typical choice envelope: best-minus-worst on a day, and how that gap varies
        "avg_day_range":      round(statistics.mean(day_ranges), 1),
        "avg_day_range_sd":   round(statistics.pstdev(day_ranges), 1),
        # how much the daily floor / ceiling themselves wander day to day
        "avg_day_floor_sd":   round(statistics.pstdev(day_floors), 1),
        "avg_day_ceiling_sd": round(statistics.pstdev(day_ceilings), 1),
        # variance split
        "within_day_sd":  round(within_var ** 0.5, 1),            # the spread choice creates on a given day
        "between_day_sd": round(statistics.pstdev(day_means), 1), # the spread the calendar creates across days
        "per_day": per_day,
    }


# ---- Nutrislice fetch -> distribution ----------------------------------------
def score_distribution(slug, school, menu, weeks, grade_band="K-5", label="menu"):
    all_days = []
    for w in weeks:
        url = (f"https://{slug}.api.nutrislice.com/menu/api/weeks/school/{school}"
               f"/menu-type/{menu}/{w.strftime('%Y/%m/%d')}")
        try:
            r = requests.get(url, timeout=15, headers={"Accept": "application/json"})
            if r.status_code != 200:
                continue
            for d in r.json().get("days", []):
                if any(it.get("food") for it in d.get("menu_items", [])):
                    all_days.append(d)
        except Exception:
            continue
    return score_from_days(all_days, grade_band=grade_band, label=label)
