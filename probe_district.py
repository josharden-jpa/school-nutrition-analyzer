# -*- coding: utf-8 -*-
"""
probe_district.py
Cafeteria Critic -- fast Nutrislice slug verification.

Given a district slug, hits the public schools endpoint and prints each
school's name, city/state, school_id, and available menu types -- WITHOUT
running a full-year scrape. Use this to vet a candidate district in seconds
before committing to a ~30-minute scoring run.

WHY THIS EXISTS:
  Slug discovery + verification is the real bottleneck for scaling across the
  country, not the scoring. Districts churn on and off Nutrislice constantly
  (migrate to SchoolCafe, LINQ, PDFs, or "client removed"), and same-named
  districts exist in different states (the Marcellus MI / Marcellus NY trap).
  This catches dead slugs and wrong-state matches fast, so a full run is only
  ever spent on a verified-good district.

HOW TO RUN (in Spyder):
  Edit SLUG below, then:
  %runfile C:/Users/josha/OneDrive/Documents/pai789/APAFINAL2/probe_district.py --wdir

  Or call probe("someslug") interactively after importing.

WHAT TO LOOK FOR:
  - Does it return schools at all? (empty / 404 / "client removed" = dead slug)
  - Are the addresses in the STATE you expect? (guards the same-name trap)
  - Which menu types exist per school, and what are their IDs? (you need the
    LUNCH menu_id for the grade band you want -- copy it into run_batch.py)
"""

import sys
import requests

SCHOOLS_URL = "https://{slug}.api.nutrislice.com/menu/api/schools/?format=json"


def probe(slug: str) -> list[dict]:
    """Print a quick report on a Nutrislice district slug. Returns school dicts."""
    url = SCHOOLS_URL.format(slug=slug)
    print(f"\nProbing slug: '{slug}'")
    print(f"  {url}")
    print("=" * 70)

    try:
        resp = requests.get(url, timeout=15)
    except Exception as e:
        print(f"  REQUEST FAILED: {e}")
        print("  -> Likely a dead slug or wrong subdomain.")
        return []

    if resp.status_code != 200:
        print(f"  HTTP {resp.status_code} -- slug likely dead or renamed.")
        return []

    try:
        data = resp.json()
    except Exception:
        print("  Response was not JSON -- slug likely points to a 'client removed'")
        print("  page or a non-API URL. Treat as dead.")
        return []

    # The schools endpoint returns a list of school objects
    schools = data if isinstance(data, list) else data.get("schools", [])
    if not schools:
        print("  No schools returned -- empty or inactive district.")
        return []

    print(f"  {len(schools)} school(s) found:\n")
    states = set()
    for s in schools:
        name = s.get("name", "?")
        sid  = s.get("id", "?")

        # Location is a single 'address' string, e.g.
        # "4479 South Onondaga Road, Nedrow, NY, USA"
        address = s.get("address", "") or ""
        state = _state_from_address(address)
        if state:
            states.add(state)

        # Menu types tell you which menu_id to use (look for 'lunch')
        menu_types = s.get("active_menu_types", []) or s.get("menu_types", [])
        menu_summary = []
        for mt in menu_types:
            mt_name = mt.get("name", "?") if isinstance(mt, dict) else str(mt)
            mt_id   = mt.get("id", "?")   if isinstance(mt, dict) else "?"
            menu_summary.append(f"{mt_name} (id={mt_id})")

        print(f"  [{sid}] {name}")
        if address:
            print(f"        address: {address}")
        if menu_summary:
            print(f"        menus: {'; '.join(menu_summary)}")
        print()

    # State sanity flag -- the guard against the same-name trap
    if states:
        print(f"  STATES PRESENT: {sorted(states)}")
        print("  -> Confirm this matches the district you intended (same-name trap).")
    else:
        print("  (No state could be parsed from addresses -- inspect manually.)")

    return schools


# US state abbreviations, for parsing the trailing ", XX, USA" of an address
_US_STATES = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA",
    "KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ",
    "NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT",
    "VA","WA","WV","WI","WY","DC",
}


def _state_from_address(address: str) -> str:
    """Pull the 2-letter state code out of a 'street, city, ST, USA' string."""
    parts = [p.strip() for p in address.split(",")]
    for p in parts:
        token = p.upper()
        if token in _US_STATES:
            return token
    return ""


if __name__ == "__main__":
    # Edit this, or pass on the command line if your runner supports it
    SLUG = sys.argv[1] if len(sys.argv) > 1 else "minneapolisschools"
    probe(SLUG)
