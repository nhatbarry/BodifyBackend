"""What Bodify would cost if every service were billed, for the admin console.

Every service Bodify runs on is on a free plan, so the real bill is $0. The
console's cost page shows a **simulation** instead: what the same usage would
cost on paid plans, with every unit billed from the first one. Free
allowances and the usage a plan includes are deliberately ignored (the
user's decision, 2026-11-18), otherwise almost every line would read $0.

Each line says where its quantity comes from:

* ``measured``: counted from Bodify's own rows (active users, Gemini requests,
  photo uploads, database size);
* ``assumed``: a figure set below, because nothing records it (CPU, RAM,
  egress, R2 storage, tokens per request);
* ``plan``: a fixed monthly fee, charged per day as monthly / 30.

Prices are examples, not quotes: they were written down from memory on
2026-11-18 and are meant to be edited here. Costs are computed per day, so
the daily series and the line totals add up to the same number.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.core.config import settings
from app.services import admin_analytics as analytics
from app.services.admin_analytics import Window, cached

PRICES_AS_OF = "2026-11-18"
USD_TO_VND = 25_000
DAYS_PER_MONTH = 30
MB_PER_GB = 1024
GB = 1024**3

# --- Example prices (USD) -------------------------------------------------------

SUPABASE_PRO_MONTHLY = 25.0
SUPABASE_DB_PER_GB_MONTH = 0.125
SUPABASE_PER_MAU = 0.00325
SUPABASE_EGRESS_PER_GB = 0.09

RAILWAY_HOBBY_MONTHLY = 5.0
RAILWAY_PER_VCPU_MONTH = 20.0
RAILWAY_PER_GB_RAM_MONTH = 10.0
RAILWAY_EGRESS_PER_GB = 0.05

R2_PER_GB_MONTH = 0.015
R2_CLASS_A_PER_MILLION = 4.50
R2_CLASS_B_PER_MILLION = 0.36

# USD per 1M tokens: (input, output). A model not listed uses the default.
GEMINI_PRICES: dict[str, tuple[float, float]] = {
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-3-flash": (0.50, 3.00),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.00),
}
GEMINI_DEFAULT_PRICE = (0.30, 2.50)

# --- Assumptions for what nothing records -----------------------------------

RAILWAY_VCPU = 0.25  # average vCPU the API keeps busy, around the clock
RAILWAY_RAM_GB = 0.5
RAILWAY_EGRESS_MB_PER_DAU = 3.0  # JSON the API sends one active user a day
SUPABASE_EGRESS_MB_PER_DAU = 1.0  # rows the database sends Railway for them
R2_STORAGE_GB = 2.6  # catalog media + the old media/ copy + user photos
R2_READS_PER_DAU = 40  # pictures and clips fetched by one active user a day
COACH_REQUESTS_PER_REPLY = 2  # one reply is 1 to 4 Gemini rounds with tools
COACH_TOKENS = (3000, 600)  # (input, output) per request: history + context
PHOTO_TOKENS = (1500, 400)  # image + prompt in, JSON draft out

Basis = Literal["measured", "assumed", "plan"]


@dataclass(frozen=True)
class Line:
    provider: str
    key: str
    item: str
    unit: str
    price_unit: str
    unit_price: float
    basis: Basis
    note: str


def _gemini_price(model: str) -> tuple[float, float]:
    return GEMINI_PRICES.get(model, GEMINI_DEFAULT_PRICE)


PROVIDERS: list[dict[str, str]] = [
    {"key": "supabase", "name": "Supabase", "plan": "Pro", "used_for": "Postgres, đăng nhập (Auth)"},
    {"key": "railway", "name": "Railway", "plan": "Hobby", "used_for": "Máy chủ API (FastAPI)"},
    {"key": "r2", "name": "Cloudflare R2", "plan": "Trả theo dùng", "used_for": "Ảnh, video bài tập; ảnh người dùng"},
    {"key": "gemini", "name": "Google Gemini", "plan": "Trả theo token", "used_for": "AI Coach, phân tích ảnh bữa ăn"},
]


def _lines() -> list[Line]:
    coach_in, coach_out = _gemini_price(settings.gemini_model)
    photo_in, photo_out = _gemini_price(settings.vision_model)
    return [
        Line("supabase", "supabase_plan", "Phí gói Pro", "tháng", "tháng", SUPABASE_PRO_MONTHLY, "plan",
             f"${SUPABASE_PRO_MONTHLY:g}/tháng, tính theo ngày."),
        Line("supabase", "supabase_db", "Dung lượng cơ sở dữ liệu", "GB-tháng", "GB-tháng",
             SUPABASE_DB_PER_GB_MONTH, "measured",
             "Dung lượng hiện tại, áp cho mọi ngày trong khoảng."),
        Line("supabase", "supabase_mau", "Người dùng hoạt động (MAU)", "MAU-tháng", "MAU",
             SUPABASE_PER_MAU, "measured",
             "MAU 30 ngày của từng ngày, cộng lại rồi chia 30."),
        Line("supabase", "supabase_egress", "Băng thông ra (egress)", "GB", "GB", SUPABASE_EGRESS_PER_GB,
             "assumed", f"{SUPABASE_EGRESS_MB_PER_DAU:g} MB cho mỗi người hoạt động mỗi ngày."),
        Line("railway", "railway_plan", "Phí gói Hobby", "tháng", "tháng", RAILWAY_HOBBY_MONTHLY, "plan",
             f"${RAILWAY_HOBBY_MONTHLY:g}/tháng, tính theo ngày."),
        Line("railway", "railway_cpu", "CPU", "vCPU-tháng", "vCPU-tháng", RAILWAY_PER_VCPU_MONTH, "assumed",
             f"Trung bình {RAILWAY_VCPU:g} vCPU chạy liên tục."),
        Line("railway", "railway_ram", "RAM", "GB-tháng", "GB-tháng", RAILWAY_PER_GB_RAM_MONTH, "assumed",
             f"Trung bình {RAILWAY_RAM_GB:g} GB chạy liên tục."),
        Line("railway", "railway_egress", "Băng thông ra (egress)", "GB", "GB", RAILWAY_EGRESS_PER_GB,
             "assumed", f"{RAILWAY_EGRESS_MB_PER_DAU:g} MB cho mỗi người hoạt động mỗi ngày."),
        Line("r2", "r2_storage", "Lưu trữ", "GB-tháng", "GB-tháng", R2_PER_GB_MONTH, "assumed",
             f"Khoảng {R2_STORAGE_GB:g} GB trong bucket."),
        Line("r2", "r2_class_a", "Thao tác ghi (Class A)", "request", "1M request", R2_CLASS_A_PER_MILLION,
             "measured", "Mỗi ảnh bữa ăn tải lên là một lần ghi."),
        Line("r2", "r2_class_b", "Thao tác đọc (Class B)", "request", "1M request", R2_CLASS_B_PER_MILLION,
             "assumed", f"{R2_READS_PER_DAU} lần tải ảnh/video cho mỗi người hoạt động mỗi ngày."),
        Line("gemini", "gemini_coach_in", "AI Coach: token vào", "token", "1M token", coach_in, "assumed",
             f"{settings.gemini_model}. Mỗi câu trả lời {COACH_REQUESTS_PER_REPLY} request,"
             f" {COACH_TOKENS[0]:,} token vào mỗi request."),
        Line("gemini", "gemini_coach_out", "AI Coach: token ra", "token", "1M token", coach_out, "assumed",
             f"{settings.gemini_model}. {COACH_TOKENS[1]:,} token ra mỗi request."),
        Line("gemini", "gemini_photo_in", "Phân tích ảnh: token vào", "token", "1M token", photo_in, "assumed",
             f"{settings.vision_model}. {PHOTO_TOKENS[0]:,} token vào mỗi ảnh, kể cả lần lỗi."),
        Line("gemini", "gemini_photo_out", "Phân tích ảnh: token ra", "token", "1M token", photo_out, "assumed",
             f"{settings.vision_model}. {PHOTO_TOKENS[1]:,} token ra mỗi ảnh phân tích được."),
    ]


def _per_million(price_unit: str) -> bool:
    return price_unit.startswith("1M")


def _quantities(dau: int, mau: int, coach: int, photo_ok: int, photo_failed: int,
                db_gb: float) -> dict[str, float]:
    """One day's billable quantities, in each line's unit."""
    month = 1 / DAYS_PER_MONTH
    coach_requests = coach * COACH_REQUESTS_PER_REPLY
    photos = photo_ok + photo_failed
    return {
        "supabase_plan": month,
        "supabase_db": db_gb * month,
        "supabase_mau": mau * month,
        "supabase_egress": dau * SUPABASE_EGRESS_MB_PER_DAU / MB_PER_GB,
        "railway_plan": month,
        "railway_cpu": RAILWAY_VCPU * month,
        "railway_ram": RAILWAY_RAM_GB * month,
        "railway_egress": dau * RAILWAY_EGRESS_MB_PER_DAU / MB_PER_GB,
        "r2_storage": R2_STORAGE_GB * month,
        "r2_class_a": photos,
        "r2_class_b": dau * R2_READS_PER_DAU,
        "gemini_coach_in": coach_requests * COACH_TOKENS[0],
        "gemini_coach_out": coach_requests * COACH_TOKENS[1],
        "gemini_photo_in": photos * PHOTO_TOKENS[0],
        "gemini_photo_out": photo_ok * PHOTO_TOKENS[1],
    }


def _cost(line: Line, quantity: float) -> float:
    if _per_million(line.price_unit):
        return quantity * line.unit_price / 1_000_000
    return quantity * line.unit_price


def build(
    days: list[dict[str, Any]],
    database_bytes: int,
) -> dict[str, Any]:
    """The simulation from per-day usage. Pure, so tests need no database.

    ``days`` rows carry ``day``, ``dau``, ``mau``, ``coach_replies``,
    ``photo_ok`` and ``photo_failed``.
    """
    lines = _lines()
    db_gb = database_bytes / GB
    qty: dict[str, float] = defaultdict(float)
    cost: dict[str, float] = defaultdict(float)
    daily = []
    for d in days:
        q = _quantities(d["dau"], d["mau"], d["coach_replies"], d["photo_ok"], d["photo_failed"], db_gb)
        per_provider: dict[str, float] = defaultdict(float)
        for line in lines:
            c = _cost(line, q[line.key])
            qty[line.key] += q[line.key]
            cost[line.key] += c
            per_provider[line.provider] += c
        daily.append(
            {
                "day": d["day"],
                **{p["key"]: round(per_provider[p["key"]], 4) for p in PROVIDERS},
                "total": round(sum(per_provider.values()), 4),
            }
        )

    providers = []
    for p in PROVIDERS:
        own = [line for line in lines if line.provider == p["key"]]
        providers.append(
            {
                **p,
                "total_usd": round(sum(cost[line.key] for line in own), 4),
                "lines": [
                    {
                        "key": line.key,
                        "item": line.item,
                        "quantity": round(qty[line.key], 6),
                        "unit": line.unit,
                        "unit_price_usd": line.unit_price,
                        "price_unit": line.price_unit,
                        "cost_usd": round(cost[line.key], 8),
                        "basis": line.basis,
                        "note": line.note,
                    }
                    for line in own
                ],
            }
        )

    return {
        "simulated": True,
        "prices_as_of": PRICES_AS_OF,
        "usd_to_vnd": USD_TO_VND,
        "days": len(days),
        "total_usd": round(sum(cost.values()), 4),
        "providers": providers,
        "daily": daily,
        "ai": {
            "requests": int(sum(d["coach_replies"] * COACH_REQUESTS_PER_REPLY
                                + d["photo_ok"] + d["photo_failed"] for d in days)),
            "input_tokens": int(qty["gemini_coach_in"] + qty["gemini_photo_in"]),
            "output_tokens": int(qty["gemini_coach_out"] + qty["gemini_photo_out"]),
            "cost_usd": round(sum(v for k, v in cost.items() if k.startswith("gemini")), 4),
        },
        "infrastructure": {
            "r2_storage_gb": R2_STORAGE_GB,
            "railway_vcpu": RAILWAY_VCPU,
            "railway_ram_gb": RAILWAY_RAM_GB,
            "server_hours": len(days) * 24,
        },
    }


@cached
def simulate(db: Session, window: Window) -> dict[str, Any]:
    active = {r["day"]: r for r in analytics.active_users(db, window)}
    ai = {r["day"]: r for r in analytics.ai_usage(db, window)["daily"]}
    days = [
        {
            "day": day,
            "dau": active.get(day, {}).get("dau", 0),
            "mau": active.get(day, {}).get("mau", 0),
            "coach_replies": ai[day]["coach_replies"],
            "photo_ok": ai[day]["photo_ok"],
            "photo_failed": ai[day]["photo_failed"],
        }
        for day in sorted(ai)
    ]
    return build(days, analytics.database_size(db))
