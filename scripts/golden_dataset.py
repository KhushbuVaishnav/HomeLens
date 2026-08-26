"""
scripts/golden_dataset.py

The single source of hand-verified ground truth for
app/data/realistic_listings.json (14 fixed listings). Both
verify_test_cases.py's Tier 3 and eval_dashboard.py import from here —
nowhere else defines these sets, so the two can never silently drift out
of sync.

Every TRUE/FALSE/AMBIGUOUS assignment below was verified by hand against
each listing's actual `remarks` text and structured fields in
app/data/realistic_listings.json (and, for schools, the real ratings in
app/data/schools.json) — never guessed. Where a listing's data genuinely
doesn't support a confident MET/NOT MET call either way, it's left out of
that dimension's TRUE/FALSE sets and — for the binary dimensions — is
surfaced by the caller as "excluded". Two dimensions below
(GOOD_SCHOOLS, RECENTLY_RENOVATED) are AMBIGUOUS-first by design: more
than a third of the listings are deliberately left unlabeled because a
confident call depends on an interpretation choice a human hasn't fixed
(e.g. "good schools" — does the buyer mean the elementary rating alone,
or all three levels together?), not because nobody looked. Grading a
model's answer against a ground truth that doesn't actually exist would
be testing our own guess, not the model's accuracy — see the identical
reasoning already used for QUIET's excluded listings below, just applied
more heavily.

===============================================================================
SINGLE-REQUIREMENT DIMENSIONS
===============================================================================
"""

# ---------------------------------------------------------------------------
# QUIET — "quiet street"
# Ground truth: does the listing's remarks call the street quiet/peaceful/
# low-traffic, or busy/high-traffic? (Original 3-dimension set.)
# ---------------------------------------------------------------------------
QUIET_TRUE = {2001001, 2001002, 2001004, 2001006, 2001007, 2001008, 2001010, 2001011, 2001012, 2001013}
QUIET_FALSE = {2001003, 2001009}
# excluded (ambiguous): 2001005 (Fair Oaks — "some road noise", softer than "busy"), 2001014 (Hudson — downtown foot-traffic noise, not framed as busy/quiet)

# ---------------------------------------------------------------------------
# OFFICE — "a home office"
# Ground truth: does remarks describe a dedicated office/den/flex space
# usable as a home office?
# ---------------------------------------------------------------------------
OFFICE_TRUE = {2001001, 2001002, 2001004, 2001006, 2001007, 2001008, 2001010, 2001011, 2001013}
OFFICE_FALSE = {2001003, 2001005, 2001009, 2001012, 2001014}

# ---------------------------------------------------------------------------
# CALTRAIN — "walkable to Caltrain"
# Ground truth: only 1500 Hudson St #12 explicitly says "two blocks from
# the Caltrain station" — every other listing either doesn't mention
# Caltrain at all or mentions a different amenity (Bay Trail, a park,
# shops) that is NOT Caltrain walkability.
# ---------------------------------------------------------------------------
CALTRAIN_TRUE = {2001014}
CALTRAIN_FALSE = {2001001, 2001002, 2001003, 2001004, 2001005, 2001006, 2001007, 2001008, 2001009, 2001010, 2001011, 2001012, 2001013}

# ---------------------------------------------------------------------------
# ACCESSIBLE — "a single-story home"
# Ground truth: property.stories in the source data (1 vs 2) — an
# objective structural fact, also passed to the model directly in the
# scoring payload as "stories". Cross-checked against remarks for every
# listing: every stories=1 listing either explicitly says "no stairs" /
# "single-level" / "one level", or is silent on stairs with nothing that
# contradicts it; every stories=2 listing's remarks explicitly mentions
# stairs or upstairs bedrooms. No contradictions found between the
# structured field and the text for any of the 14.
#
# Deliberately phrased as JUST "a single-story home", not "...with no
# stairs" — an earlier version appended that clause and it backfired: the
# model (correctly, per its own SYSTEM_PROMPT instruction to break
# preferences into distinct, un-merged requirements) split it into TWO
# requirements, "single-story" and "no stairs", and only credited "no
# stairs" when remarks said so explicitly — it doesn't infer "no stairs"
# from "single-story" alone even though that's a logical certainty. That
# was a flaw in the query wording, not a model bug (confirmed by
# inspecting the actual per-requirement breakdown it returned). Dropping
# the redundant clause removes the artificial second requirement.
# ---------------------------------------------------------------------------
ACCESSIBLE_TRUE = {2001001, 2001003, 2001004, 2001005, 2001007, 2001009, 2001010, 2001011, 2001012, 2001013, 2001014}
ACCESSIBLE_FALSE = {2001002, 2001006, 2001008}

# ---------------------------------------------------------------------------
# HOA_CONDO — "a condo with HOA-covered maintenance"
# Ground truth: only the two actual condos (2001013, 2001014) have both
# an HOA fee AND remarks explicitly describing the HOA covering
# maintenance/landscaping/exterior upkeep for a low-maintenance
# lifestyle. 2001008 is deliberately excluded: it DOES carry an HOA fee
# ($220) and mentions "HOA covers gate maintenance and common-area
# landscaping" — so it's not simply "no HOA" — but it's a large single-
# family home with its own pool/spa the buyer maintains, not remotely a
# low-maintenance-condo-living situation. A model could reasonably score
# this either way depending on which half of the requirement it weighs
# more, so it's excluded rather than force-labeled.
#
# Deliberately dropped the "low-maintenance" prefix from the query — same
# lesson as ACCESSIBLE/RECENTLY_RENOVATED above, confirmed live via
# langsmith_eval.py's per-example results: "a low-maintenance condo with
# HOA-covered maintenance" got split into 3 requirements ("low-
# maintenance", "condo", "HOA-covered"), and non-condo listings with a
# merely tidy backyard picked up partial credit (33) for "low-
# maintenance" alone, scoring above 0 when they should have failed
# outright. "Low-maintenance" was never meant to be graded on its own —
# the ground truth here has only ever been about condo+HOA status.
# ---------------------------------------------------------------------------
HOA_CONDO_TRUE = {2001013, 2001014}
HOA_CONDO_FALSE = {2001001, 2001002, 2001003, 2001004, 2001005, 2001006, 2001007, 2001009, 2001010, 2001011, 2001012}
# excluded (ambiguous): 2001008 (has an HOA fee, but for gate/common-area only — not low-maintenance condo living)

# ---------------------------------------------------------------------------
# NEWER — "newer construction, built after 2000"
# Ground truth: property.yearBuilt, a plain numeric threshold. Clean gap
# in the actual data (next-oldest is 1995, next-newest right at 2001) —
# no listing sits close enough to the cutoff to be ambiguous.
# ---------------------------------------------------------------------------
NEWER_TRUE = {2001008, 2001013}  # yearBuilt 2008, 2001
NEWER_FALSE = {2001001, 2001002, 2001003, 2001004, 2001005, 2001006, 2001007, 2001009, 2001010, 2001011, 2001012, 2001014}

# ---------------------------------------------------------------------------
# LARGE_LOT — "a lot of at least 8,000 sqft"
# Ground truth: property.lotSize, a plain numeric threshold (also passed
# to the model directly). Clean gap in the actual data (next below is
# 7200, next above is 9100) — no borderline cases. The two condos have no
# lot at all (lotSize: null) and are unambiguously FALSE.
#
# Deliberately dropped the "large lot" phrase and kept only the numeric
# threshold — same lesson as HOA_CONDO above, confirmed live via
# langsmith_eval.py: "a large lot, at least 8,000 sqft" got split into
# "large lot" (vague, credited from remarks language like "large, private
# backyard" regardless of actual size) and "at least 8,000 sqft"
# (correctly checked against the real number) as two separate
# requirements — a 6,500 sqft lot with a nice-sounding backyard scored
# 50 instead of the correct 0. The ground truth was always the numeric
# threshold; the descriptive phrase was redundant and actively harmful.
# ---------------------------------------------------------------------------
LARGE_LOT_TRUE = {2001006, 2001008}  # lotSize 9100, 10200
LARGE_LOT_FALSE = {2001001, 2001002, 2001003, 2001004, 2001005, 2001007, 2001009, 2001010, 2001011, 2001012, 2001013, 2001014}

# ---------------------------------------------------------------------------
# NOT_RANCH — "definitely not a ranch-style home"
# A NEGATION case, not just "requirement not met": the buyer explicitly
# rules OUT a style, so the correct answer is MET (true) for every
# listing that is NOT ranch, and NOT MET (false) for every listing that
# IS ranch. This is the exact example SYSTEM_PROMPT itself uses ("style
# when they name or rule out an architectural style, e.g. ... 'not a
# ranch'"), and it's never been checked against real ground truth before
# now. Ground truth: property.style, exact structured field, no judgment
# call involved.
# ---------------------------------------------------------------------------
NOT_RANCH_TRUE = {2001002, 2001003, 2001004, 2001005, 2001006, 2001008, 2001009, 2001013, 2001014}  # style != Ranch
NOT_RANCH_FALSE = {2001001, 2001007, 2001010, 2001011, 2001012}  # style == Ranch

# ---------------------------------------------------------------------------
# GOOD_SCHOOLS — "highly rated schools for my kids"  (AMBIGUOUS-heavy)
# Real ratings pulled from app/data/schools.json (not guessed):
#   2001001/2001014: elem 9/6, mid 6/9, high 6/6
#   2001002/2001006/2001008: elem 9, mid 9, high 8   (same 3 schools)
#   2001003/2001004: elem 7, mid 9, high 8            (same 3 schools)
#   2001005/2001009: elem 4, mid 6, high 6            (same 3 schools)
#   2001007: elem 7, mid 6, high 6
#   2001010/2001011/2001012: elem 7, mid 9, high 8    (same 3 schools)
#   2001013: elem 8, mid 9, high 8
# "Good schools" is genuinely interpretation-dependent (does a buyer
# asking about "schools for my kids" mean elementary specifically, or an
# average across all three assigned schools?) — so ground truth here only
# asserts a call where BOTH reasonable interpretations agree:
#   TRUE  = every one of the 3 assigned schools individually rates >= 8
#           (strong under any interpretation)
#   FALSE = every one of the 3 assigned schools individually rates <= 7
#           (not strong under any interpretation)
#   left unlabeled (ambiguous) = a mixed profile where one level is
#           notably stronger/weaker than the others (e.g. elem 7 but mid
#           9) — a real, defensible disagreement point, not laziness.
# ---------------------------------------------------------------------------
GOOD_SCHOOLS_TRUE = {2001002, 2001006, 2001008, 2001013}
GOOD_SCHOOLS_FALSE = {2001005, 2001007, 2001009}
GOOD_SCHOOLS_AMBIGUOUS = {2001001, 2001003, 2001004, 2001010, 2001011, 2001012, 2001014}

# ---------------------------------------------------------------------------
# RECENTLY_RENOVATED — "a home with an updated kitchen"
# (AMBIGUOUS-heavy)
# TRUE only where remarks explicitly describe the KITCHEN as
# updated/remodeled/new. FALSE only where remarks explicitly say the
# interior/kitchen is dated and needs a remodel. Left unlabeled where
# remarks mention an update to something OTHER than the kitchen (a bath,
# for instance) or say nothing about renovation status at all — the
# requirement specifically names the kitchen, so a bath-only update or
# silence isn't a confident MET or NOT MET call.
#
# Deliberately phrased as just "an updated kitchen", not "recently
# renovated, with an updated kitchen" — same lesson as ACCESSIBLE above:
# the "recently renovated" (whole-home) and "updated kitchen" (room-
# specific) clauses got split into two separate requirements by the
# model, and the whole-home claim wasn't always credited even when the
# kitchen clearly was — confirmed by inspecting the actual requirement
# breakdown. The ground truth here was always kitchen-specific, so
# dropping the redundant whole-home clause matches what's actually being
# graded.
# ---------------------------------------------------------------------------
RECENTLY_RENOVATED_TRUE = {2001001, 2001002, 2001004, 2001007, 2001008, 2001010, 2001014}
RECENTLY_RENOVATED_FALSE = {2001005, 2001009}
RECENTLY_RENOVATED_AMBIGUOUS = {2001003, 2001006, 2001011, 2001012, 2001013}


# All binary (TRUE/FALSE, no ambiguous-first framing) single-requirement
# dimensions — the ones combined into the multi-requirement cases below,
# and iterated over in full by eval_dashboard.py.
BINARY_DIMENSIONS = [
    {"key": "quiet", "label": "Quiet street", "query": "quiet street",
     "positive": QUIET_TRUE, "negative": QUIET_FALSE},
    {"key": "office", "label": "Home office", "query": "a home office",
     "positive": OFFICE_TRUE, "negative": OFFICE_FALSE},
    {"key": "caltrain", "label": "Walkable to Caltrain", "query": "walkable to Caltrain",
     "positive": CALTRAIN_TRUE, "negative": CALTRAIN_FALSE},
    {"key": "accessible", "label": "Single-story home", "query": "a single-story home",
     "positive": ACCESSIBLE_TRUE, "negative": ACCESSIBLE_FALSE},
    {"key": "hoa_condo", "label": "Condo with HOA maintenance", "query": "a condo with HOA-covered maintenance",
     "positive": HOA_CONDO_TRUE, "negative": HOA_CONDO_FALSE},
    {"key": "newer", "label": "Newer construction (2000+)", "query": "newer construction, built after 2000",
     "positive": NEWER_TRUE, "negative": NEWER_FALSE},
    {"key": "large_lot", "label": "Large lot (8,000+ sqft)", "query": "a lot of at least 8,000 sqft",
     "positive": LARGE_LOT_TRUE, "negative": LARGE_LOT_FALSE},
    {"key": "not_ranch", "label": "Not a ranch style (negation)", "query": "definitely not a ranch-style home",
     "positive": NOT_RANCH_TRUE, "negative": NOT_RANCH_FALSE},
]

# Ambiguous-first dimensions — scored and shown, but only the TRUE/FALSE
# subset is graded pass/fail; the AMBIGUOUS subset is displayed with the
# model's real answer and no verdict, since no defensible ground truth
# exists for those listings.
AMBIGUOUS_DIMENSIONS = [
    {"key": "good_schools", "label": "Highly rated schools", "query": "highly rated schools for my kids",
     "positive": GOOD_SCHOOLS_TRUE, "negative": GOOD_SCHOOLS_FALSE, "ambiguous": GOOD_SCHOOLS_AMBIGUOUS},
    {"key": "renovated", "label": "Updated kitchen", "query": "an updated kitchen",
     "positive": RECENTLY_RENOVATED_TRUE, "negative": RECENTLY_RENOVATED_FALSE, "ambiguous": RECENTLY_RENOVATED_AMBIGUOUS},
]


"""
===============================================================================
COMBINED MULTI-REQUIREMENT CASES (2 through 5 requirements)
===============================================================================

Each combo intersects several of the BINARY_DIMENSIONS above (never the
ambiguous-first ones — combining in an already-unlabeled dimension would
make the combined ground truth just as unlabeled, defeating the point).
A listing is only included in a combo if it has a definitive TRUE/FALSE
label in EVERY dimension being combined — a listing excluded from even
one component dimension is excluded from the combo entirely, same
principle as the single-dimension exclusions above.

expected_score = round(100 * met / total) — the same deterministic
scoring formula _compute_deterministic_scores() itself uses, so this
tests the real end-to-end math, not just classification.
"""


def _combo_cases(dims: list[dict], all_ids: set) -> list[dict]:
    """Builds one combo's per-listing expected outcomes from N component
    dimensions. Only listings with a definitive label in every dimension
    are included (see module docstring above)."""
    usable = all_ids.copy()
    for d in dims:
        usable &= (d["positive"] | d["negative"])

    n = len(dims)
    cases = []
    for mls_id in sorted(usable):
        met = sum(1 for d in dims if mls_id in d["positive"])
        cases.append({"mls_id": mls_id, "met": met, "total": n, "expected_score": round(100 * met / n)})
    return cases


_ALL_IDS = {2001001, 2001002, 2001003, 2001004, 2001005, 2001006, 2001007, 2001008,
            2001009, 2001010, 2001011, 2001012, 2001013, 2001014}

_QUIET_DIM = BINARY_DIMENSIONS[0]
_OFFICE_DIM = BINARY_DIMENSIONS[1]
_ACCESSIBLE_DIM = BINARY_DIMENSIONS[3]
_NOT_RANCH_DIM = BINARY_DIMENSIONS[7]
_LARGE_LOT_DIM = BINARY_DIMENSIONS[6]

COMBINED_CASES = [
    {
        "key": "combo_2", "n": 2,
        "label": "Quiet + home office (2 requirements)",
        "query": "quiet street and a home office",
        "cases": _combo_cases([_QUIET_DIM, _OFFICE_DIM], _ALL_IDS),
    },
    {
        "key": "combo_3", "n": 3,
        "label": "Quiet + home office + single-story (3 requirements)",
        # "single-story", not "no stairs" — see ACCESSIBLE's comment above:
        # the model only credits "no stairs" against an explicit textual
        # match, it doesn't infer it from the stories field the way it
        # does for "single-story" phrasing.
        "query": "quiet street, a home office, and a single-story layout",
        "cases": _combo_cases([_QUIET_DIM, _OFFICE_DIM, _ACCESSIBLE_DIM], _ALL_IDS),
    },
    {
        "key": "combo_4", "n": 4,
        "label": "Quiet + home office + single-story + not ranch (4 requirements)",
        "query": "quiet street, a home office, a single-story layout, and definitely not a ranch-style home",
        "cases": _combo_cases([_QUIET_DIM, _OFFICE_DIM, _ACCESSIBLE_DIM, _NOT_RANCH_DIM], _ALL_IDS),
    },
    {
        "key": "combo_5", "n": 5,
        "label": "Quiet + home office + single-story + not ranch + large lot (5 requirements)",
        "query": "quiet street, a home office, a single-story layout, definitely not a ranch-style home, and a lot of at least 8,000 sqft",
        "cases": _combo_cases([_QUIET_DIM, _OFFICE_DIM, _ACCESSIBLE_DIM, _NOT_RANCH_DIM, _LARGE_LOT_DIM], _ALL_IDS),
    },
]
