"""
scripts/seed_schools_db.py

One-time (or re-run-anytime) script that builds app/data/schools.db from
app/data/schools.json. Run this once before starting the app, and again
any time schools.json changes.

Run from the project root:
    python scripts/seed_schools_db.py
"""

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JSON_PATH = ROOT / "app" / "data" / "schools.json"
DB_PATH = ROOT / "app" / "data" / "schools.db"


def seed():
    with open(JSON_PATH) as f:
        data = json.load(f)
    data.pop("_note", None)

    # Fresh build every time this script runs — simpler and safer than
    # trying to diff/update an existing .db file.
    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE schools (
            name       TEXT PRIMARY KEY,
            level      TEXT NOT NULL,
            rating     INTEGER NOT NULL,
            district   TEXT,
            enrollment INTEGER
        )
    """)
    conn.execute("CREATE INDEX idx_schools_level ON schools(level)")

    conn.executemany(
        "INSERT INTO schools (name, level, rating, district, enrollment) VALUES (?, ?, ?, ?, ?)",
        [
            (name, info["level"], info["rating"], info.get("district"), info.get("enrollment"))
            for name, info in data.items()
        ],
    )
    conn.commit()

    count = conn.execute("SELECT COUNT(*) FROM schools").fetchone()[0]
    conn.close()

    print(f"Seeded {count} schools into {DB_PATH}")


if __name__ == "__main__":
    seed()
