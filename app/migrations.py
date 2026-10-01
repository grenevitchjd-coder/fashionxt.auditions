"""
Automatic database migrations — runs on every backend start (see app/main.py).

HOW TO ADD A MIGRATION (every future feature):
  1. Append ONE new ("NNN_short_name", "SQL statement") tuple to the END of MIGRATIONS.
  2. Push to GitHub. Railway redeploys, and the new SQL runs once at startup.

RULES:
  - One SQL statement per entry (the Postgres driver does not allow several at once).
  - Never edit or reorder an entry that has already shipped — only append.
  - Write statements to be re-runnable (IF NOT EXISTS etc.), so a database that
    already has the change (because it was applied by hand earlier) is unaffected.

Applied migrations are recorded in the `schema_migration` table, so each one runs once.
If a migration fails, the app refuses to start and the error shows in the Railway deploy
logs — better than running against a half-updated database.
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

MIGRATIONS: list[tuple[str, str]] = [
    # ---- Catch-up: brings any database in line with the current code. ----
    # Your live database already has all of this from the manual SQL you ran,
    # so on Railway these are harmless no-ops. They matter for a fresh database.
    (
        "001_show_day",
        """
        CREATE TABLE IF NOT EXISTS show_day (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            show_date DATE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
    ),
    (
        "002_designer",
        """
        CREATE TABLE IF NOT EXISTS designer (
            id SERIAL PRIMARY KEY,
            show_day_id INTEGER NOT NULL REFERENCES show_day(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            order_in_day INTEGER NOT NULL,
            notes TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
    ),
    (
        "003_designer_assignment",
        """
        CREATE TABLE IF NOT EXISTS designer_assignment (
            id SERIAL PRIMARY KEY,
            designer_id INTEGER NOT NULL REFERENCES designer(id) ON DELETE CASCADE,
            applicant_id INTEGER NOT NULL REFERENCES applicant(id) ON DELETE CASCADE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (designer_id, applicant_id)
        )
        """,
    ),
    (
        "004_assignment_order_in_lineup",
        "ALTER TABLE designer_assignment ADD COLUMN IF NOT EXISTS order_in_lineup INTEGER NOT NULL DEFAULT 1",
    ),
    (
        "005_designer_share_token",
        "ALTER TABLE designer ADD COLUMN IF NOT EXISTS share_token TEXT UNIQUE",
    ),
    (
        "006_assignment_preference",
        "ALTER TABLE designer_assignment ADD COLUMN IF NOT EXISTS preference TEXT",
    ),
    (
        "007_designer_roster_only",
        "ALTER TABLE designer ADD COLUMN IF NOT EXISTS roster_only BOOLEAN NOT NULL DEFAULT false",
    ),
    (
        "008_seed_show_days_if_empty",
        """
        INSERT INTO show_day (name, show_date)
        SELECT v.name, v.show_date
        FROM (VALUES
            ('Thursday', DATE '2026-10-08'),
            ('Friday',   DATE '2026-10-09'),
            ('Saturday', DATE '2026-10-10')
        ) AS v(name, show_date)
        WHERE NOT EXISTS (SELECT 1 FROM show_day)
        """,
    ),
    # ---- Day of Show ----
    (
        "009_non_model_attendee",
        """
        CREATE TABLE IF NOT EXISTS non_model_attendee (
            id SERIAL PRIMARY KEY,
            show_day_id INTEGER NOT NULL REFERENCES show_day(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            attendee_type TEXT NOT NULL,
            group_key TEXT,
            checked_in_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
    ),
    (
        "010_non_model_attendee_day_index",
        "CREATE INDEX IF NOT EXISTS ix_non_model_attendee_show_day ON non_model_attendee (show_day_id)",
    ),
        (
        "011_day_model_status",
        """
        CREATE TABLE IF NOT EXISTS day_model_status (
            id SERIAL PRIMARY KEY,
            applicant_id INTEGER NOT NULL REFERENCES applicant(id) ON DELETE CASCADE,
            show_day_id INTEGER NOT NULL REFERENCES show_day(id) ON DELETE CASCADE,
            checked_in_at TIMESTAMPTZ,
            note TEXT,
            hair_done BOOLEAN NOT NULL DEFAULT false,
            makeup_done BOOLEAN NOT NULL DEFAULT false,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (applicant_id, show_day_id)
        )
        """,
    ),
    (
        "012_designer_checkin",
        """
        CREATE TABLE IF NOT EXISTS designer_checkin (
            designer_id INTEGER PRIMARY KEY REFERENCES designer(id) ON DELETE CASCADE,
            checked_in_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
    ),
        (
        "013_look_status",
        """
        CREATE TABLE IF NOT EXISTS look_status (
            id SERIAL PRIMARY KEY,
            applicant_id INTEGER NOT NULL REFERENCES applicant(id) ON DELETE CASCADE,
            designer_id INTEGER NOT NULL REFERENCES designer(id) ON DELETE CASCADE,
            hair_done BOOLEAN NOT NULL DEFAULT false,
            makeup_done BOOLEAN NOT NULL DEFAULT false,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (applicant_id, designer_id)
        )
        """,
    ),
    (
        "014_team_order",
        """
        CREATE TABLE IF NOT EXISTS team_order (
            id SERIAL PRIMARY KEY,
            team TEXT NOT NULL,
            designer_id INTEGER NOT NULL REFERENCES designer(id) ON DELETE CASCADE,
            applicant_id INTEGER NOT NULL REFERENCES applicant(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            UNIQUE (team, designer_id, applicant_id)
        )
        """,
    ),
        (
        "015_look_hair_in_progress",
        "ALTER TABLE look_status ADD COLUMN IF NOT EXISTS hair_in_progress BOOLEAN NOT NULL DEFAULT false",
    ),
    (
        "016_look_makeup_in_progress",
        "ALTER TABLE look_status ADD COLUMN IF NOT EXISTS makeup_in_progress BOOLEAN NOT NULL DEFAULT false",
    ),
    # ---- New migrations go BELOW this line. ----
]

# Arbitrary constant — makes sure two backend instances starting at the same
# moment don't run migrations at the same time.
_LOCK_KEY = 7_426_001


async def run_migrations(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _LOCK_KEY})
        try:
            await conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migration (
                        name TEXT PRIMARY KEY,
                        applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
            )
            await conn.commit()

            done = {row[0] for row in (await conn.execute(text("SELECT name FROM schema_migration"))).all()}

            for name, sql in MIGRATIONS:
                if name in done:
                    continue
                try:
                    await conn.execute(text(sql))
                    await conn.execute(text("INSERT INTO schema_migration (name) VALUES (:n)"), {"n": name})
                    await conn.commit()
                    print(f"[migrations] applied {name}")
                except Exception:
                    await conn.rollback()
                    print(f"[migrations] FAILED {name}")
                    raise
        finally:
            await conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _LOCK_KEY})
            await conn.commit()