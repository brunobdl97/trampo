# Plain sqlite3 and hand-written SQL, no ORM

Storage is a single SQLite file accessed through the standard-library `sqlite3` module with hand-written SQL in one `store` module; the schema lives in `schema.sql` and migrations are versioned with SQLite's `PRAGMA user_version`. We rejected SQLAlchemy/SQLModel + Alembic because a handful of tables owned by a single local process doesn't justify an ORM, a migration tool and their dependencies.
