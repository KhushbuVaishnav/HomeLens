"""
scripts/generate_listings.py

Generates a large dataset of Redwood City listings into app/data/generated_listings.json
by combining real neighborhoods with randomized (but meaningful) description
templates — avoids both SimplyRETS' identical boilerplate remarks and the
impossibility of hand-writing thousands of unique paragraphs.

Run from the project root:
    python scripts/generate_listings.py             # 500 listings (default)
    python scripts/generate_listings.py 2000         # custom count
"""

import json
import random
import sys
from pathlib import Path

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "app" / "data" / "generated_listings.json"

SF_NEIGHBORHOODS = [
    ("Mount Carmel", "94062", 1.00),
    ("Emerald Hills", "94062", 1.30),
    ("Central Emerald Hills", "94062", 1.40),
    ("Woodside Plaza", "94061", 0.95),
    ("Farm Hill", "94061", 1.15),
    ("Redwood Oaks", "94063", 1.00),
    ("Redwood Village", "94063", 0.75),
    ("North Fair Oaks", "94063", 0.68),
    ("Redwood Estates", "94062", 1.50),
    ("Redwood Shores Adjacent", "94063", 1.10),
]

CONDO_NEIGHBORHOODS = [
    ("Redwood Shores", "94065", 1.05),
    ("Downtown Redwood City", "94061", 0.85),
]

STREET_NAMES = [
    "Hopkins", "Hillcrest", "Woodside", "Clinton", "Fair Oaks", "Edgewood",
    "Brewster", "Cordilleras", "Middlefield", "Maple", "Alameda", "Stambaugh",
    "Marine", "Hudson", "Jefferson", "Elm", "Vera", "Cedar", "Cypress",
    "Douglas", "Grand", "Poplar", "Spring", "Buckingham", "Roosevelt",
    "Alma", "Hamilton", "Warren", "Charter", "Redwood", "Selby", "Vine",
]
STREET_TYPES = ["Ave", "St", "Rd", "Dr", "Ln", "Ct", "Way", "Pl"]

SINGLE_STORY_STYLES = ["Ranch", "Cottage", "Bungalow", "Craftsman"]
TWO_STORY_STYLES = ["Traditional", "Contemporary", "Colonial", "Mid-Century", "Modern Farmhouse"]

# Fictional schools, deliberately built from names in the same synthetic
# street-name pool used for addresses — sounds locally plausible without
# reusing any real school's actual name. Each neighborhood is mapped to one
# school cluster so a listing's assigned schools are geographically
# consistent with its (also fictional) neighborhood, the way a real MLS
# feed's school assignments would be.
NEIGHBORHOOD_SCHOOLS = {
    "Mount Carmel":             {"elementary": "Poplar Elementary",     "middle": "Grand Middle School",    "high": "Vine Valley High School"},
    "Emerald Hills":            {"elementary": "Cedar Ridge Elementary", "middle": "Warren Middle School",   "high": "Charter High School"},
    "Central Emerald Hills":    {"elementary": "Cedar Ridge Elementary", "middle": "Warren Middle School",   "high": "Charter High School"},
    "Woodside Plaza":           {"elementary": "Cypress Elementary",    "middle": "Selby Middle School",     "high": "Charter High School"},
    "Farm Hill":                {"elementary": "Cypress Elementary",    "middle": "Selby Middle School",     "high": "Charter High School"},
    "Redwood Oaks":             {"elementary": "Douglas Elementary",    "middle": "Grand Middle School",     "high": "Vine Valley High School"},
    "Redwood Village":          {"elementary": "Buckingham Elementary", "middle": "Grand Middle School",     "high": "Vine Valley High School"},
    "North Fair Oaks":          {"elementary": "Buckingham Elementary", "middle": "Grand Middle School",     "high": "Vine Valley High School"},
    "Redwood Estates":          {"elementary": "Cedar Ridge Elementary", "middle": "Warren Middle School",   "high": "Charter High School"},
    "Redwood Shores Adjacent":  {"elementary": "Douglas Elementary",    "middle": "Selby Middle School",     "high": "Charter High School"},
    "Redwood Shores":           {"elementary": "Vera Elementary",       "middle": "Warren Middle School",    "high": "Charter High School"},
    "Downtown Redwood City":    {"elementary": "Alma Elementary",       "middle": "Selby Middle School",     "high": "Vine Valley High School"},
}

QUIET_SENTENCES = [
    "Located on a quiet, tree-lined street with minimal through traffic.",
    "Sits on a peaceful cul-de-sac away from busy roads.",
    "Quiet residential street popular with longtime families.",
    "No through traffic on this dead-end street.",
]
BUSY_SENTENCES = [
    "Located directly on a busy arterial road with steady traffic noise.",
    "Sits near a commercial corridor with vehicle and bus traffic at most hours.",
    "Some road noise during commute hours from the nearby main street.",
]

KITCHEN_UPDATED = [
    "Updated kitchen with quartz counters and stainless appliances.",
    "Remodeled kitchen featuring a large island, ideal for entertaining.",
    "Recently renovated eat-in kitchen with new cabinetry.",
    "Chef's kitchen with custom cabinetry and a farmhouse sink.",
]
KITCHEN_DATED = [
    "Original kitchen and bath present an opportunity to renovate and build equity.",
    "Dated interior throughout, needs a full kitchen remodel.",
    "Kitchen has not been updated in some time but is fully functional.",
]

OFFICE_SENTENCES = [
    "A spare bedroom currently works well as a home office.",
    "Dedicated home office with built-in shelving off the main living area.",
    "Flexible bonus room, ideal as a home office or den.",
    "Converted garage space functions as a quiet home office.",
]
NO_OFFICE_SENTENCES = [
    "All bedrooms are used as bedrooms, no dedicated office space.",
    "Compact floor plan with no separate room suited to a home office.",
]

ACCESSIBLE_SENTENCES = [
    "Single-level layout with every bedroom on one floor, no interior stairs.",
    "Zero-step entry from the driveway to the front door, with wide hallways throughout.",
    "Primary bedroom and bath are positioned on the main level for easy access.",
    "Walk-in shower with a built-in bench in the primary bathroom.",
]
MULTI_STORY_SENTENCES = [
    "Bedrooms are split across two levels, with stairs from the entry to the main living floor.",
    "Primary suite and secondary bedrooms are all located upstairs.",
]

YARD_SENTENCES = [
    "Low-maintenance backyard with mature landscaping.",
    "Fully fenced yard with a covered patio, great for outdoor dining.",
    "Flat, private backyard suited to gardening or a play structure.",
    "Small, easy-care yard requiring minimal upkeep.",
]

EXTRA_SENTENCES = [
    "Backyard includes a pool and spa, ideal for entertaining.",
    "Close walking distance to a neighborhood park.",
    "Near local shops, cafes, and the Redwood City Caltrain station.",
    "HOA covers landscaping and building maintenance, a low-maintenance option.",
    None, None, None,
]


def make_address(used_addresses: set) -> tuple[str, str]:
    while True:
        number = random.randint(10, 99999)
        street = f"{random.choice(STREET_NAMES)} {random.choice(STREET_TYPES)}"
        full = f"{number} {street}"
        if full not in used_addresses:
            used_addresses.add(full)
            return full, street


def build_description(is_quiet, is_updated, has_office, is_single_story) -> str:
    parts = [
        random.choice(QUIET_SENTENCES if is_quiet else BUSY_SENTENCES),
        random.choice(KITCHEN_UPDATED if is_updated else KITCHEN_DATED),
        random.choice(OFFICE_SENTENCES if has_office else NO_OFFICE_SENTENCES),
        random.choice(ACCESSIBLE_SENTENCES if is_single_story else MULTI_STORY_SENTENCES),
        random.choice(YARD_SENTENCES),
    ]
    extra = random.choice(EXTRA_SENTENCES)
    if extra:
        parts.append(extra)
    return " ".join(parts)


def generate(count: int) -> list[dict]:
    listings = []
    used_addresses = set()

    for i in range(count):
        is_condo = random.random() < 0.20
        neighborhood, postal, price_tier = random.choice(CONDO_NEIGHBORHOODS if is_condo else SF_NEIGHBORHOODS)
        subtype = "Condominium" if is_condo else "SingleFamilyResidence"

        full_address, _ = make_address(used_addresses)
        if is_condo:
            full_address += f" #{random.randint(1, 400)}"

        sqft = random.randint(700, 1400) if is_condo else random.randint(1000, 3800)
        beds = random.choice([1, 2, 2, 3, 3, 3, 4]) if is_condo else random.choice([2, 3, 3, 4, 4, 5])
        baths = max(1, beds - random.choice([0, 0, 1]))

        base_price_per_sqft = random.uniform(780, 900) if is_condo else random.uniform(950, 1150)
        price = int(sqft * base_price_per_sqft * price_tier * random.uniform(0.9, 1.1) / 5000) * 5000

        is_single_story = True if is_condo else (random.random() < 0.6)
        stories = 1 if is_single_story else 2
        is_quiet = random.random() < 0.65
        is_updated = random.random() < 0.6
        has_office = random.random() < 0.5

        hoa_fee = None
        if is_condo:
            hoa_fee = random.choice([350, 420, 480, 550, 650, 720])
        elif random.random() < 0.08:
            hoa_fee = random.choice([150, 200, 250])

        listing = {
            "mlsId": 3000000 + i,
            "listPrice": price,
            "address": {"full": full_address, "city": "Redwood City", "state": "CA", "postalCode": postal},
            "property": {
                "area": sqft,
                "bathsFull": baths,
                "bathsHalf": 0,
                "bedrooms": beds,
                "yearBuilt": random.randint(1930, 2022),
                "lotSize": None if is_condo else str(random.randint(3500, 11000)),
                "subdivision": neighborhood,
                "style": "Condo" if is_condo else random.choice(SINGLE_STORY_STYLES if is_single_story else TWO_STORY_STYLES),
                "subType": subtype,
                "stories": stories,
            },
            "association": {"fee": hoa_fee},
            "schools": NEIGHBORHOOD_SCHOOLS[neighborhood],
            "remarks": build_description(is_quiet, is_updated, has_office, is_single_story),
            "photos": [],
        }
        listings.append(listing)

    return listings


if __name__ == "__main__":
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 500
    data = generate(count)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Generated {len(data)} listings -> {OUTPUT_PATH}")
