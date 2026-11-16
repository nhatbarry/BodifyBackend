"""What every account's record adds up to, for the admin dashboard.

Everything here is computed from source tables at request time; nothing is a
stored counter. That is the same rule Phase 4 follows ("7 workouts" is a
COUNT, never `count += 1`), and it means a report can never drift from the
rows it describes.

Three conventions hold for every query:

* **Days are Vietnamese days.** Timestamps are bucketed with
  `AT TIME ZONE 'Asia/Ho_Chi_Minh'`. `date(completed_at)` in UTC puts a
  workout finished before 07:00 on the previous day (CLAUDE.md, seeding
  notes), which would skew every daily chart.
* **"Active" means the account wrote something that day**: started a workout,
  logged food, weighed in, asked the coach, synced Health Connect or had a
  meal photo analysed. Sign-ins cannot be used: GoTrue's audit log is empty
  and `last_sign_in_at` keeps only the latest one.
* **Every report takes a scope.** 30,000 seeded accounts share the tables
  with the real ones; they are recognised by their reserved address
  (`@seed.bodify.example`), the same marker `seed_fake_users.py --undo` uses.
"""

from __future__ import annotations

import functools
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from time import monotonic
from typing import Any, Literal, TypeVar

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings

# --- Cache --------------------------------------------------------------------
#
# Each report costs about a second alone, but the Analytics page asks for six
# at once, and on the hosted Free-plan database they queue: measured 15 s
# before the last chart drew. These figures move slowly and are read by a
# handful of admins, so a result is reused for five minutes, per process,
# keyed by report and arguments. Nothing user-facing reads this cache.

CACHE_SECONDS = 300
_CACHE_MAX_ENTRIES = 512
_cache: dict[tuple[Any, ...], tuple[float, Any]] = {}
_cache_lock = threading.Lock()

F = TypeVar("F", bound=Callable[..., Any])


def cached(fn: F) -> F:
    @functools.wraps(fn)
    def wrapper(db: Session, *args: Any) -> Any:
        key = (fn.__name__, *args)
        now = monotonic()
        with _cache_lock:
            hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]
        value = fn(db, *args)
        with _cache_lock:
            if len(_cache) >= _CACHE_MAX_ENTRIES:
                _cache.clear()
            _cache[key] = (now, value)
        return value

    return wrapper  # type: ignore[return-value]

# Vietnam has no daylight saving, so a fixed offset is exact and needs no
# tz database (Windows Python ships none, nor does every slim image).
VN = timezone(timedelta(hours=7))
TZ_NAME = "Asia/Ho_Chi_Minh"
SEED_PATTERN = "%@seed.bodify.example"
MAX_DAYS = 366

Scope = Literal["all", "real", "seeded"]


@dataclass(frozen=True)
class Window:
    """An inclusive range of Vietnamese days plus the account scope."""

    d_from: date
    d_to: date
    scope: Scope = "all"

    @property
    def days(self) -> int:
        return (self.d_to - self.d_from).days + 1

    @property
    def lo(self) -> datetime:
        return start_of(self.d_from)

    @property
    def hi(self) -> datetime:
        return start_of(self.d_to + timedelta(days=1))

    def previous(self) -> Window:
        """The equally long range just before this one, for comparisons."""
        shift = timedelta(days=self.days)
        return Window(self.d_from - shift, self.d_to - shift, self.scope)


def start_of(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=VN)


def today_vn() -> date:
    return datetime.now(VN).date()


# Accounts in scope. auth.users is GoTrue's table and is only read here.
SCOPED = """
scoped as (
    select u.id as user_id, u.created_at
      from auth.users u
     where u.deleted_at is null
       and (:scope = 'all'
            or (coalesce(u.email, '') like :seed_pattern) = (:scope = 'seeded'))
)"""

# One row per (account, Vietnamese day) on which it wrote something.
# UNION, not UNION ALL: the de-duplication is the point.
ACTIVITY = """
    select ws.user_id, (ws.started_at at time zone :tz)::date as d
      from workout_sessions ws
     where ws.started_at >= :alo and ws.started_at < :ahi
    union
    select m.user_id, (mi.created_at at time zone :tz)::date
      from meal_items mi join meals m on m.meal_id = mi.meal_id
     where mi.created_at >= :alo and mi.created_at < :ahi
    union
    select bm.user_id, (bm.created_at at time zone :tz)::date
      from body_metrics bm
     where bm.created_at >= :alo and bm.created_at < :ahi
    union
    select cs.user_id, (cm.created_at at time zone :tz)::date
      from chat_messages cm join chat_sessions cs on cs.chat_session_id = cm.chat_session_id
     where cm.role = 'user' and cm.created_at >= :alo and cm.created_at < :ahi
    union
    select hd.user_id, (hd.synced_at at time zone :tz)::date
      from health_daily hd
     where hd.synced_at >= :alo and hd.synced_at < :ahi
    union
    select pa.user_id, (pa.created_at at time zone :tz)::date
      from meal_photo_analyses pa
     where pa.created_at >= :alo and pa.created_at < :ahi
"""

# Wall time of a finished session, pauses taken out, as the app computes it.
DURATION = (
    "greatest(extract(epoch from (ws.completed_at - ws.started_at))"
    " - coalesce(ws.paused_seconds, 0), 0)"
)


def _params(window: Window, **extra: Any) -> dict[str, Any]:
    return {"tz": TZ_NAME, "seed_pattern": SEED_PATTERN, "scope": window.scope, **extra}


def _days_sql() -> str:
    return (
        "days as (select generate_series(cast(:d_from as date), cast(:d_to as date),"
        " interval '1 day')::date as day)"
    )


def _rate(part: int | None, whole: int | None) -> float | None:
    if not whole:
        return None
    return round(100.0 * (part or 0) / whole, 1)


# --- Overview -----------------------------------------------------------------


def _retention_d30(db: Session, window: Window) -> tuple[int, int]:
    """Cohort: sign-ups in the 30 days ending 36 days before the range ends.

    Retained: active on any day 30 to 36 after their own sign-up day. The
    36-day gap guarantees every member's window has fully passed.
    """
    cohort_to = window.d_to - timedelta(days=36)
    cohort_from = cohort_to - timedelta(days=29)
    row = db.execute(
        text(
            f"""
            with {SCOPED},
            cohort as (
                select user_id, (created_at at time zone :tz)::date as sd
                  from scoped
                 where created_at >= :clo and created_at < :chi
            ),
            kept as (
                select distinct c.user_id
                  from ({ACTIVITY}) a
                  join cohort c on c.user_id = a.user_id
                 where a.d between c.sd + 30 and c.sd + 36
            )
            select (select count(*) from cohort) as size,
                   (select count(*) from kept) as retained
            """
        ),
        _params(
            window,
            clo=start_of(cohort_from),
            chi=start_of(cohort_to + timedelta(days=1)),
            alo=start_of(cohort_from + timedelta(days=30)),
            ahi=window.hi,
        ),
    ).one()
    return row.size, row.retained


@cached
def overview(db: Session, window: Window) -> dict[str, Any]:
    prev = window.previous()

    users = db.execute(
        text(
            f"""
            with {SCOPED}
            select count(*) filter (where created_at < :cur_hi) as total_cur,
                   count(*) filter (where created_at < :prev_hi) as total_prev,
                   count(*) filter (where created_at >= :cur_lo and created_at < :cur_hi) as new_cur,
                   count(*) filter (where created_at >= :prev_lo and created_at < :prev_hi) as new_prev
              from scoped
            """
        ),
        _params(window, cur_lo=window.lo, cur_hi=window.hi, prev_lo=prev.lo, prev_hi=prev.hi),
    ).one()

    seeded_accounts = db.execute(
        text(
            "select count(*) from auth.users"
            " where deleted_at is null and coalesce(email, '') like :seed_pattern"
        ),
        {"seed_pattern": SEED_PATTERN},
    ).scalar_one()

    active = db.execute(
        text(
            f"""
            with {SCOPED},
            act as (
                select a.user_id, a.d
                  from ({ACTIVITY}) a join scoped s on s.user_id = a.user_id
            )
            select
                (select count(*) from act where d between :cf and :ct) as day_rows_cur,
                (select count(*) from act where d between :pf and :pt) as day_rows_prev,
                (select count(distinct user_id) from act where d between :cw and :ct) as wau_cur,
                (select count(distinct user_id) from act where d between :pw and :pt) as wau_prev,
                (select count(distinct user_id) from act where d between :cm and :ct) as mau_cur,
                (select count(distinct user_id) from act where d between :pm and :pt) as mau_prev
            """
        ),
        _params(
            window,
            alo=start_of(min(prev.d_from, prev.d_to - timedelta(days=29))),
            ahi=window.hi,
            cf=window.d_from,
            ct=window.d_to,
            pf=prev.d_from,
            pt=prev.d_to,
            cw=window.d_to - timedelta(days=6),
            pw=prev.d_to - timedelta(days=6),
            cm=window.d_to - timedelta(days=29),
            pm=prev.d_to - timedelta(days=29),
        ),
    ).one()

    workouts = db.execute(
        text(
            f"""
            with {SCOPED}
            select
                count(*) filter (where ws.status = 'completed'
                                   and ws.completed_at >= :cur_lo and ws.completed_at < :cur_hi) as done_cur,
                count(*) filter (where ws.status = 'completed'
                                   and ws.completed_at >= :prev_lo and ws.completed_at < :prev_hi) as done_prev,
                coalesce(sum({DURATION}) filter (where ws.status = 'completed'
                                   and ws.completed_at >= :cur_lo and ws.completed_at < :cur_hi), 0) as secs_cur,
                coalesce(sum({DURATION}) filter (where ws.status = 'completed'
                                   and ws.completed_at >= :prev_lo and ws.completed_at < :prev_hi), 0) as secs_prev,
                count(*) filter (where ws.status = 'completed'
                                   and ws.started_at >= :cur_lo and ws.started_at < :cur_hi) as started_done_cur,
                count(*) filter (where ws.status = 'abandoned'
                                   and ws.started_at >= :cur_lo and ws.started_at < :cur_hi) as abandoned_cur,
                count(*) filter (where ws.status = 'completed'
                                   and ws.started_at >= :prev_lo and ws.started_at < :prev_hi) as started_done_prev,
                count(*) filter (where ws.status = 'abandoned'
                                   and ws.started_at >= :prev_lo and ws.started_at < :prev_hi) as abandoned_prev
              from workout_sessions ws
              join scoped s on s.user_id = ws.user_id
             where ws.started_at >= :prev_lo or ws.completed_at >= :prev_lo
            """
        ),
        _params(window, cur_lo=window.lo, cur_hi=window.hi, prev_lo=prev.lo, prev_hi=prev.hi),
    ).one()

    ret_size, ret_kept = _retention_d30(db, window)
    prev_size, prev_kept = _retention_d30(db, prev)

    def kpi(value: float | None, previous: float | None) -> dict[str, float | None]:
        return {"value": value, "previous": previous}

    return {
        "range": {"from": window.d_from, "to": window.d_to, "days": window.days},
        "previous_range": {"from": prev.d_from, "to": prev.d_to, "days": prev.days},
        "scope": window.scope,
        "seeded_accounts": seeded_accounts,
        "kpis": {
            "total_users": kpi(users.total_cur, users.total_prev),
            "new_users": kpi(users.new_cur, users.new_prev),
            "dau": kpi(
                round(active.day_rows_cur / window.days, 1),
                round(active.day_rows_prev / prev.days, 1),
            ),
            "wau": kpi(active.wau_cur, active.wau_prev),
            "mau": kpi(active.mau_cur, active.mau_prev),
            "workouts_completed": kpi(workouts.done_cur, workouts.done_prev),
            "workout_hours": kpi(
                round(float(workouts.secs_cur) / 3600, 1),
                round(float(workouts.secs_prev) / 3600, 1),
            ),
            "completion_rate": kpi(
                _rate(workouts.started_done_cur, workouts.started_done_cur + workouts.abandoned_cur),
                _rate(workouts.started_done_prev, workouts.started_done_prev + workouts.abandoned_prev),
            ),
            "avg_daily_workouts": kpi(
                round(workouts.done_cur / window.days, 1),
                round(workouts.done_prev / prev.days, 1),
            ),
            "retention_d30": kpi(_rate(ret_kept, ret_size), _rate(prev_kept, prev_size)),
        },
    }


# --- Daily series -------------------------------------------------------------


@cached
def growth(db: Session, window: Window) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            f"""
            with {SCOPED}, {_days_sql()},
            signups as (
                select (created_at at time zone :tz)::date as d, count(*) as n
                  from scoped
                 where created_at >= :lo and created_at < :hi
                 group by 1
            ),
            base as (select count(*) as n from scoped where created_at < :lo)
            select days.day,
                   coalesce(s.n, 0) as signups,
                   (select n from base) + sum(coalesce(s.n, 0)) over (order by days.day) as total
              from days left join signups s on s.d = days.day
             order by days.day
            """
        ),
        _params(window, d_from=window.d_from, d_to=window.d_to, lo=window.lo, hi=window.hi),
    ).all()
    return [{"day": r.day, "signups": r.signups, "total": int(r.total)} for r in rows]


@cached
def active_users(db: Session, window: Window) -> list[dict[str, Any]]:
    """DAU, and the trailing 7- and 30-day distinct counts ending on each day.

    Counting distinct users over a sliding window for every day was measured
    at 10.8 s for ten months. Instead, each active day covers the next 7 (or
    30) days; a user's overlapping coverage is merged into islands, and
    since one user's islands never overlap, the number of islands covering a
    day is the number of distinct users. That is a running sum of +1 where
    an island starts and -1 where it ends.
    """
    rows = db.execute(
        text(
            f"""
            with {SCOPED},
            act as (
                select a.user_id, a.d
                  from ({ACTIVITY}) a join scoped s on s.user_id = a.user_id
            ),
            gaps as (
                select user_id, d, d - lag(d) over (partition by user_id order by d) as gap
                  from act
            ),
            w7 as (
                select user_id, d,
                       sum(case when gap is null or gap > 7 then 1 else 0 end)
                           over (partition by user_id order by d) as island
                  from gaps
            ),
            w30 as (
                select user_id, d,
                       sum(case when gap is null or gap > 30 then 1 else 0 end)
                           over (partition by user_id order by d) as island
                  from gaps
            ),
            edges as (
                select min(d) as day, 1 as dw, 0 as dm from w7 group by user_id, island
                union all
                select max(d) + 7, -1, 0 from w7 group by user_id, island
                union all
                select min(d), 0, 1 from w30 group by user_id, island
                union all
                select max(d) + 30, 0, -1 from w30 group by user_id, island
            ),
            delta as (select day, sum(dw) as dw, sum(dm) as dm from edges group by day),
            daily as (select d as day, count(*) as n from act group by d),
            span as (
                select generate_series(cast(:a_from as date), cast(:d_to as date),
                                       interval '1 day')::date as day
            ),
            running as (
                select span.day,
                       coalesce(daily.n, 0) as dau,
                       sum(coalesce(delta.dw, 0)) over (order by span.day) as wau,
                       sum(coalesce(delta.dm, 0)) over (order by span.day) as mau
                  from span
                  left join delta on delta.day = span.day
                  left join daily on daily.day = span.day
            )
            select day, dau, wau, mau from running where day >= :d_from order by day
            """
        ),
        _params(
            window,
            a_from=window.d_from - timedelta(days=29),
            d_from=window.d_from,
            d_to=window.d_to,
            alo=start_of(window.d_from - timedelta(days=29)),
            ahi=window.hi,
        ),
    ).all()
    return [{"day": r.day, "dau": r.dau, "wau": int(r.wau), "mau": int(r.mau)} for r in rows]


@cached
def workouts(db: Session, window: Window) -> list[dict[str, Any]]:
    """Finished sessions by the day they finished; completion by the day started."""
    rows = db.execute(
        text(
            f"""
            with {SCOPED}, {_days_sql()},
            finished as (
                select (ws.completed_at at time zone :tz)::date as d,
                       count(*) as completed,
                       sum({DURATION}) as secs
                  from workout_sessions ws join scoped s on s.user_id = ws.user_id
                 where ws.status = 'completed' and ws.completed_at >= :lo and ws.completed_at < :hi
                 group by 1
            ),
            started as (
                select (ws.started_at at time zone :tz)::date as d,
                       count(*) filter (where ws.status = 'completed') as done,
                       count(*) filter (where ws.status = 'abandoned') as abandoned
                  from workout_sessions ws join scoped s on s.user_id = ws.user_id
                 where ws.started_at >= :lo and ws.started_at < :hi
                 group by 1
            )
            select days.day,
                   coalesce(f.completed, 0) as completed,
                   coalesce(f.secs, 0) as secs,
                   coalesce(st.abandoned, 0) as abandoned,
                   coalesce(st.done, 0) as started_done
              from days
              left join finished f on f.d = days.day
              left join started st on st.d = days.day
             order by days.day
            """
        ),
        _params(window, d_from=window.d_from, d_to=window.d_to, lo=window.lo, hi=window.hi),
    ).all()
    return [
        {
            "day": r.day,
            "completed": r.completed,
            "abandoned": r.abandoned,
            "minutes": round(float(r.secs) / 60),
            "completion_rate": _rate(r.started_done, r.started_done + r.abandoned),
        }
        for r in rows
    ]


@cached
def nutrition(db: Session, window: Window) -> list[dict[str, Any]]:
    """Food logging by the day it was entered (not the diary date it was entered for)."""
    rows = db.execute(
        text(
            f"""
            with {SCOPED}, {_days_sql()},
            logged as (
                select (mi.created_at at time zone :tz)::date as d,
                       count(distinct m.user_id) as loggers,
                       count(*) filter (where mi.status = 'eaten') as eaten,
                       count(*) filter (where mi.status = 'planned') as planned,
                       count(*) filter (where mi.photo_analysis_id is not null) as from_photo
                  from meal_items mi
                  join meals m on m.meal_id = mi.meal_id
                  join scoped s on s.user_id = m.user_id
                 where mi.created_at >= :lo and mi.created_at < :hi
                 group by 1
            )
            select days.day,
                   coalesce(l.loggers, 0) as loggers,
                   coalesce(l.eaten, 0) as eaten,
                   coalesce(l.planned, 0) as planned,
                   coalesce(l.from_photo, 0) as from_photo
              from days left join logged l on l.d = days.day
             order by days.day
            """
        ),
        _params(window, d_from=window.d_from, d_to=window.d_to, lo=window.lo, hi=window.hi),
    ).all()
    return [
        {
            "day": r.day,
            "loggers": r.loggers,
            "items_eaten": r.eaten,
            "items_planned": r.planned,
            "items_from_photo": r.from_photo,
        }
        for r in rows
    ]


# --- Breakdowns ---------------------------------------------------------------


@cached
def muscle_distribution(db: Session, window: Window, language: str) -> list[dict[str, Any]]:
    """Completed sets in finished sessions, by the exercise's primary muscle.

    By muscle rather than `exercise_type`: 611 of 704 exercises are
    "Strength", so a split by type is one slice.
    """
    rows = db.execute(
        text(
            f"""
            with {SCOPED}
            select mg.muscle_id, coalesce(tt.name, mg.name) as name, count(*) as sets
              from session_sets ss
              join session_exercises se on se.session_exercise_id = ss.session_exercise_id
              join workout_sessions ws on ws.session_id = se.session_id
              join scoped s on s.user_id = ws.user_id
              join exercise_muscles em on em.exercise_id = se.exercise_id and em.role = 'primary'
              join muscle_groups mg on mg.muscle_id = em.muscle_id
              left join term_translations tt on tt.muscle_id = mg.muscle_id and tt.language = :lang
             where ws.status = 'completed' and ws.completed_at >= :lo and ws.completed_at < :hi
               and ss.completed
             group by mg.muscle_id, coalesce(tt.name, mg.name)
             order by sets desc
            """
        ),
        _params(window, lo=window.lo, hi=window.hi, lang=language),
    ).all()
    return [{"muscle_id": r.muscle_id, "name": r.name, "sets": r.sets} for r in rows]


@cached
def top_exercises(db: Session, window: Window, language: str, limit: int) -> list[dict[str, Any]]:
    """Ranked by how many people trained it, then by how often."""
    rows = db.execute(
        text(
            f"""
            with {SCOPED},
            ranked as (
                select se.exercise_id,
                       count(distinct ws.user_id) as users,
                       count(distinct ws.session_id) as sessions,
                       count(ss.session_set_id) filter (where ss.completed) as sets
                  from session_exercises se
                  join workout_sessions ws on ws.session_id = se.session_id
                  join scoped s on s.user_id = ws.user_id
                  left join session_sets ss on ss.session_exercise_id = se.session_exercise_id
                 where ws.status = 'completed' and ws.completed_at >= :lo and ws.completed_at < :hi
                   and se.exercise_id is not null
                 group by se.exercise_id
                 order by users desc, sessions desc
                 limit :limit
            )
            select r.exercise_id, coalesce(tl.name, te.name) as name, r.users, r.sessions, r.sets
              from ranked r
              left join exercise_translations tl on tl.exercise_id = r.exercise_id and tl.language = :lang
              left join exercise_translations te on te.exercise_id = r.exercise_id and te.language = 'en'
             order by r.users desc, r.sessions desc
            """
        ),
        _params(window, lo=window.lo, hi=window.hi, lang=language, limit=limit),
    ).all()
    return [
        {
            "exercise_id": r.exercise_id,
            "name": r.name,
            "users": r.users,
            "sessions": r.sessions,
            "sets": r.sets,
        }
        for r in rows
    ]


@cached
def retention(db: Session, window: Window, months: int = 12) -> list[dict[str, Any]]:
    """Monthly sign-up cohorts and the share active in each later month.

    Only the end of the range matters: the last `months` cohorts up to the
    month of `to`. Month 0 is the sign-up month itself. A cohort's last
    observed month may be partial (the month `to` falls in).
    """
    last_month = window.d_to.replace(day=1)
    first_month = last_month
    for _ in range(months - 1):
        first_month = (first_month - timedelta(days=1)).replace(day=1)

    sizes = db.execute(
        text(
            f"""
            with {SCOPED}
            select date_trunc('month', created_at at time zone :tz)::date as cohort, count(*) as size
              from scoped
             where created_at >= :lo and created_at < :hi
             group by 1
            """
        ),
        _params(window, lo=start_of(first_month), hi=window.hi),
    ).all()

    cells = db.execute(
        text(
            f"""
            with {SCOPED},
            cohort as (
                select user_id, date_trunc('month', created_at at time zone :tz)::date as cm
                  from scoped
                 where created_at >= :alo and created_at < :ahi
            ),
            act as (
                select distinct a.user_id, date_trunc('month', a.d)::date as m
                  from ({ACTIVITY}) a join cohort c on c.user_id = a.user_id
            )
            select c.cm as cohort,
                   ((extract(year from act.m) - extract(year from c.cm)) * 12
                     + extract(month from act.m) - extract(month from c.cm))::int as month_offset,
                   count(*) as users
              from cohort c join act on act.user_id = c.user_id and act.m >= c.cm
             group by 1, 2
            """
        ),
        _params(window, alo=start_of(first_month), ahi=window.hi),
    ).all()

    by_cohort: dict[date, dict[int, int]] = {}
    for c in cells:
        by_cohort.setdefault(c.cohort, {})[c.month_offset] = c.users

    result = []
    for s in sorted(sizes, key=lambda r: r.cohort):
        observed = (last_month.year - s.cohort.year) * 12 + last_month.month - s.cohort.month + 1
        counts = by_cohort.get(s.cohort, {})
        result.append(
            {
                "cohort": s.cohort,
                "size": s.size,
                "retained": [_rate(counts.get(i, 0), s.size) for i in range(observed)],
            }
        )
    return result


@cached
def feature_adoption(db: Session, window: Window) -> dict[str, Any]:
    """How many active accounts used each feature in the range, plus weekly users."""
    params = _params(window, lo=window.lo, hi=window.hi, alo=window.lo, ahi=window.hi)
    events = f"""
        with {SCOPED},
        ev as (
            select 'coach' as feature, cs.user_id, cm.created_at as ts
              from chat_messages cm join chat_sessions cs on cs.chat_session_id = cm.chat_session_id
             where cm.role = 'user' and cm.created_at >= :lo and cm.created_at < :hi
            union all
            select 'meal_photo', pa.user_id, pa.created_at
              from meal_photo_analyses pa
             where pa.created_at >= :lo and pa.created_at < :hi
            union all
            select 'health_connect', hd.user_id, hd.synced_at
              from health_daily hd
             where hd.synced_at >= :lo and hd.synced_at < :hi
            union all
            select 'camera', ws.user_id, ws.completed_at
              from session_sets ss
              join session_exercises se on se.session_exercise_id = ss.session_exercise_id
              join workout_sessions ws on ws.session_id = se.session_id
             where ss.ai_algorithm is not null and ws.status = 'completed'
               and ws.completed_at >= :lo and ws.completed_at < :hi
        ),
        scoped_ev as (select ev.* from ev join scoped s on s.user_id = ev.user_id)
    """
    totals = db.execute(
        text(
            events
            + """
            select feature, count(distinct user_id) as users, count(*) as events
              from scoped_ev group by feature
            """
        ),
        params,
    ).all()
    weekly = db.execute(
        text(
            events
            + """
            select date_trunc('week', ts at time zone :tz)::date as week, feature,
                   count(distinct user_id) as users
              from scoped_ev group by 1, 2 order by 1
            """
        ),
        params,
    ).all()
    active = db.execute(
        text(
            f"""
            with {SCOPED}
            select count(distinct a.user_id)
              from ({ACTIVITY}) a join scoped s on s.user_id = a.user_id
            """
        ),
        params,
    ).scalar_one()

    features = ["coach", "meal_photo", "health_connect", "camera"]
    found = {t.feature: t for t in totals}
    weeks: dict[date, dict[str, Any]] = {}
    for w in weekly:
        weeks.setdefault(w.week, {"week": w.week, **{f: 0 for f in features}})[w.feature] = w.users
    return {
        "active_users": active,
        "features": [
            {
                "feature": f,
                "users": found[f].users if f in found else 0,
                "events": found[f].events if f in found else 0,
                "share_of_active": _rate(found[f].users if f in found else 0, active),
            }
            for f in features
        ],
        "weekly": [weeks[k] for k in sorted(weeks)],
    }


# --- Operating costs ----------------------------------------------------------


@cached
def ai_usage(db: Session, window: Window) -> dict[str, Any]:
    """Requests that reached Gemini, counted from the rows they left behind.

    Token counts are not recorded anywhere yet: services/gemini.py drops the
    response's usage metadata. Until it is stored, cost can only be estimated
    from request counts, so tokens are reported as untracked rather than 0.

    One coach reply can take up to MAX_TOOL_ROUNDS Gemini requests, so
    `coach_replies` is a lower bound on requests. Seeded accounts never
    called Gemini; their rows are part of the fake history.
    """
    params = _params(window, d_from=window.d_from, d_to=window.d_to, lo=window.lo, hi=window.hi)
    daily = db.execute(
        text(
            f"""
            with {SCOPED}, {_days_sql()},
            coach as (
                select (cm.created_at at time zone :tz)::date as d, count(*) as n
                  from chat_messages cm
                  join chat_sessions cs on cs.chat_session_id = cm.chat_session_id
                  join scoped s on s.user_id = cs.user_id
                 where cm.role = 'assistant' and cm.created_at >= :lo and cm.created_at < :hi
                 group by 1
            ),
            photo as (
                select (pa.created_at at time zone :tz)::date as d,
                       count(*) filter (where pa.status = 'ok') as ok,
                       count(*) filter (where pa.status <> 'ok') as failed
                  from meal_photo_analyses pa join scoped s on s.user_id = pa.user_id
                 where pa.created_at >= :lo and pa.created_at < :hi
                 group by 1
            )
            select days.day,
                   coalesce(c.n, 0) as coach_replies,
                   coalesce(p.ok, 0) as photo_ok,
                   coalesce(p.failed, 0) as photo_failed
              from days
              left join coach c on c.d = days.day
              left join photo p on p.d = days.day
             order by days.day
            """
        ),
        params,
    ).all()
    photo_models = db.execute(
        text(
            f"""
            with {SCOPED}
            select coalesce(pa.model, 'unknown') as model,
                   count(*) filter (where pa.status = 'ok') as ok,
                   count(*) filter (where pa.status <> 'ok') as failed
              from meal_photo_analyses pa join scoped s on s.user_id = pa.user_id
             where pa.created_at >= :lo and pa.created_at < :hi
             group by 1 order by 2 desc
            """
        ),
        params,
    ).all()

    return {
        "tokens_tracked": False,
        "configured": settings.can_use_ai_coach,
        "coach_model": settings.gemini_model,
        "vision_model": settings.vision_model,
        "daily": [
            {
                "day": r.day,
                "coach_replies": r.coach_replies,
                "photo_ok": r.photo_ok,
                "photo_failed": r.photo_failed,
            }
            for r in daily
        ],
        "photo_by_model": [{"model": r.model, "ok": r.ok, "failed": r.failed} for r in photo_models],
    }


def database_size(db: Session) -> int:
    return int(db.execute(text("select pg_database_size(current_database())")).scalar_one())
