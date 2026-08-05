"""
app/services/schools_service.py

Loads school ratings from a real SQLite database (app/data/schools.db)
instead of parsing a JSON file on every startup. Public interface
(lookup_school, attach_school_ratings) is unchanged from the JSON version —
listings_service.py and everything else calling this module needs zero
changes.

schools.db is read-only reference data, built once by
scripts/seed_schools_db.py and committed alongside the code — it is never
written to at runtime, which is what makes this safe on hosts with an
ephemeral filesystem (e.g. Render's free tier): nothing here depends on
writes surviving a restart, only reads of data that ships with the deploy.

Re-run scripts/seed_schools_db.py and redeploy whenever schools.json changes.
"""

import sqlite3
from app.config import settings


def _get_connection():
    # A fresh connection per call rather than one held open for the process
    # lifetime — SQLite connections aren't guaranteed thread-safe across
    # FastAPI's request-handling threads without extra care, and school
    # lookups are infrequent/cheap enough that this costs nothing
    # meaningful in practice.
    conn = sqlite3.connect(settings.SCHOOLS_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def lookup_school(name: str) -> dict | None:
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT name, level, rating, district, enrollment FROM schools WHERE name = ?",
            (name,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def attach_school_ratings(listing: dict, schools: dict) -> dict:
    ratings = {}
    for level, school_name in (schools or {}).items():
        info = lookup_school(school_name)
        ratings[level] = {"name": school_name, "rating": info["rating"] if info else None}
    return {**listing, "school_ratings": ratings}
