# Migrating to a hosted Supabase project

The app never talks to Supabase directly, only to FastAPI, so this is a change
to the backend's four environment variables and nothing else. No Flutter code
moves. R2 does not move either - the media has been online the whole time.

## What is in this folder

| File | What it is |
| --- | --- |
| `bodify_catalog.sql` | **The one to restore.** All 27 tables, catalog data only (704 exercises, 1406 translations, 2074 media, 2368 muscle links, 703 equipment links, 23 muscle groups, 25 equipment, 48 term translations, 9 achievements). No user rows. |
| `full_public.sql` | The same schema with *every* row including user data. Only useful if you decide to carry accounts over after all. |
| `01_schema.sql` / `02_data_all.sql` | The `supabase db dump` pair. Kept for reference; **do not restore `02_data_all.sql`** - see "Why one file" below. |

Regenerate any of them with the commands at the bottom.

## Why a dump rather than replaying `sql_script/`

`sql_script/` is thirteen files whose correct order is written down nowhere, and
`ddl_phase1.sql` still contains a `CREATE TABLE users` block that its own
comment marks as *"kept only as historical record and is not applied"*.
Replaying them blind rebuilds a table the whole system decided against.

A dump of the live `public` schema is the state *after* every migration and
every cleanup, which is the state you actually want.

## Why one file and not schema + data

`muscle_groups.parent_id` references `muscle_groups.muscle_id`, so a data-only
restore has to insert a child before its parent and the foreign key rejects it.
`pg_dump` says so:

```
warning: there are circular foreign-key constraints on this table:
detail: muscle_groups
hint: Consider using a full dump instead of a --data-only dump
```

The usual escape - `--disable-triggers` - needs superuser, which the hosted
`postgres` role is not. A full dump sidesteps it entirely by adding every
foreign key *after* the `COPY` blocks.

## Steps

### 1. Enable the extensions first

Dashboard -> Database -> Extensions: **`unaccent`**, `pgcrypto`, `uuid-ossp`.

This has to happen **before** the restore. The dump carries zero
`CREATE EXTENSION` statements (verified: the count is 0) because extensions
live in schema `extensions`, which `--schema=public` excludes. Miss `unaccent`
and exercise search stops working for every Vietnamese query - `app/api/
endpoints/exercises.py` calls `func.unaccent()` unqualified and relies on it
being on the role's `search_path`.

### 2. Take the session pooler connection string, not the direct one

Dashboard -> Connect. Three are offered and the default is the wrong one:

| String | Verdict |
| --- | --- |
| `db.<ref>.supabase.co:5432` | Direct. **IPv6 only** without the paid IPv4 add-on. Most Vietnamese ISPs have no IPv6, and the failure is a bare connect timeout that says nothing about addressing. |
| `...pooler.supabase.com:6543` | Transaction pooler. PgBouncer transaction mode - no session state. |
| `...pooler.supabase.com:5432` | **Session pooler. Use this.** IPv4, behaves like an ordinary Postgres connection. |

User is `postgres.<project-ref>`. Append `?sslmode=require`.

### 3. Restore

`psql` is not on PATH here, but the local Supabase database container has it:

```bash
docker exec -i supabase_db_BodifyBackend psql \
  "postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require" \
  < migrate/bodify_catalog.sql
```

### 4. Point the backend at it

Copy `.env` to `.env.online` and change only these four:

```
DATABASE_URL=postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require
SUPABASE_URL=https://<ref>.supabase.co
SUPABASE_PUBLISHABLE_KEY=<anon key>
SUPABASE_SECRET_KEY=<service_role key>
```

The three `STORAGE_*` groups stay exactly as they are.

Keeping the local `.env` alongside it is deliberate: every query now crosses the
internet, so local Supabase stays the faster place to develop against, and the
hosted project is for demos and for not losing the data with the laptop.

### 5. Create the accounts and refill

Accounts cannot be dumped. `auth.users` is GoTrue's, it holds password hashes,
and three tables have real foreign keys into it (`personal_records`,
`personal_bests`, `user_achievements` - nine references in the dump). Register
fresh through the app or `POST /auth/register`, then:

```bash
venv\Scripts\python.exe scripts\seed_fake_history.py --email <new address>
```

That rebuilds six months of plan, sessions, records and achievements in one
command - faster and cleaner than remapping a UUID across ten tables.

## Verifying it took

```bash
venv\Scripts\python.exe -c "from sqlalchemy import text; from app.db.session import SessionLocal; db=SessionLocal(); print({t: db.execute(text(f'select count(*) from {t}')).scalar() for t in ['exercises','exercise_translations','exercise_media','achievements']})"
```

Expect `704 / 1406 / 2074 / 9`. Then hit `GET /exercises?search=nguc` - if it
returns rows with "Ngực" in them, `unaccent` is in place; if it errors, step 1
was skipped.

## Regenerating these dumps

```bash
# catalog only (what you restore)
USERDATA="user_profiles body_metrics workout_plans workout_days workout_exercises \
workout_exercise_sets schedules workout_sessions session_exercises session_sets \
personal_records personal_bests user_achievements user_goals user_preferences \
fitness_goals ai_recommendations ai_feedback"
ARGS=""; for t in $USERDATA; do ARGS="$ARGS --exclude-table-data=public.$t"; done
docker exec supabase_db_BodifyBackend pg_dump -U postgres -d postgres \
  --schema=public --no-owner --no-privileges \
  --exclude-table=staging_exercises --exclude-table=staging_exercises_vie \
  $ARGS > migrate/bodify_catalog.sql
```

`staging_exercises` and `staging_exercises_vie` are excluded on purpose: they
are the raw CSV the catalog was imported from, 704 rows and 2.9 MB each, and
nothing reads them. They are also the tables CLAUDE.md warns about, the ones
`pg_stat_user_tables` reported as empty when they were not.

## One code change this needed

`app/db/session.py` gained `pool_pre_ping=True` and `pool_recycle=300`. A
pooler closes idle connections without telling SQLAlchemy, so the first request
after a quiet spell checks out a dead socket and fails - intermittently, only
after inactivity, with an error that mentions nothing about pooling.
