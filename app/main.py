"""BikePool API: brand/bike catalog, city-wise group-buying pools, admin deal flow."""

import logging
import secrets
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .db import get_conn, init_db
from .schemas import JoinRequest, PoolUpdate
from .upi import build_upi_uri, qr_data_url

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("bikepool")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    if not get_settings().admin_token:
        log.warning("ADMIN_TOKEN is not set: admin endpoints are disabled")
    yield


app = FastAPI(title="BikePool", version="0.1.0", lifespan=lifespan)


# --------------------------------------------------------------------------- helpers
def db():
    with get_conn() as conn:
        yield conn


def require_admin(x_admin_token: str = Header(default="")) -> None:
    expected = get_settings().admin_token
    if not expected:
        raise HTTPException(503, "Admin access is not configured on this server")
    # compare_digest avoids leaking the token through response-time differences
    if not secrets.compare_digest(x_admin_token.encode(), expected.encode()):
        raise HTTPException(401, "Invalid admin token")


def mask_name(name: str) -> str:
    """'Rahul Kumar' -> 'Rahul K.' so the public list never shows full identities."""
    parts = name.split()
    return parts[0] if len(parts) == 1 else f"{parts[0]} {parts[-1][0]}."


def bike_row(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"],
        "model": r["model"],
        "category": r["category"],
        "engine_cc": r["engine_cc"],
        "fuel": r["fuel"],
        "approx_price_inr": r["approx_price_inr"],
        "brand": {"id": r["brand_id"], "slug": r["brand_slug"], "name": r["brand_name"]},
        "interested": r["interested"],
    }


BIKE_SELECT = """
    SELECT b.*, br.slug AS brand_slug, br.name AS brand_name,
           (SELECT COUNT(*) FROM members m JOIN pools p ON p.id = m.pool_id
             WHERE p.bike_id = b.id AND p.status != 'closed') AS interested
    FROM bikes b JOIN brands br ON br.id = b.brand_id
"""


def pool_row(conn: sqlite3.Connection, r: sqlite3.Row) -> dict:
    count = conn.execute("SELECT COUNT(*) c FROM members WHERE pool_id = ?", (r["id"],)).fetchone()["c"]
    timeline = {
        row["timeline"]: row["c"]
        for row in conn.execute(
            "SELECT timeline, COUNT(*) c FROM members WHERE pool_id = ? GROUP BY timeline", (r["id"],)
        )
    }
    return {
        "id": r["id"],
        "city": r["city"],
        "status": r["status"],
        "target_size": r["target_size"],
        "member_count": count,
        "progress_pct": min(100, round(100 * count / r["target_size"])),
        "dealer_name": r["dealer_name"],
        "discount_inr": r["discount_inr"],
        "deal_valid_until": r["deal_valid_until"],
        "timeline_breakdown": timeline,
        "bike": {
            "id": r["bike_id"],
            "model": r["model"],
            "brand": r["brand_name"],
            "brand_slug": r["brand_slug"],
            "approx_price_inr": r["approx_price_inr"],
            "category": r["category"],
        },
    }


POOL_SELECT = """
    SELECT p.*, b.model, b.approx_price_inr, b.category, br.name AS brand_name, br.slug AS brand_slug
    FROM pools p JOIN bikes b ON b.id = p.bike_id JOIN brands br ON br.id = b.brand_id
"""


def get_pool_or_404(conn: sqlite3.Connection, pool_id: int) -> sqlite3.Row:
    row = conn.execute(POOL_SELECT + " WHERE p.id = ?", (pool_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Pool not found")
    return row


def get_member_or_403(conn: sqlite3.Connection, member_id: int, secret: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()
    # Same error for "missing" and "wrong secret" so ids can't be probed.
    if not row or not secrets.compare_digest(row["secret"].encode(), secret.encode()):
        raise HTTPException(403, "Invalid member id or secret")
    return row


# --------------------------------------------------------------------------- catalog
@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/brands")
def list_brands(conn=Depends(db)):
    rows = conn.execute(
        """SELECT br.*, COUNT(b.id) AS bike_count,
                  (SELECT COUNT(*) FROM members m JOIN pools p ON p.id = m.pool_id
                     JOIN bikes bb ON bb.id = p.bike_id
                    WHERE bb.brand_id = br.id AND p.status != 'closed') AS interested
           FROM brands br LEFT JOIN bikes b ON b.brand_id = br.id
           GROUP BY br.id ORDER BY br.name"""
    ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/bikes")
def list_bikes(
    brand: Optional[str] = None,
    category: Optional[str] = None,
    fuel: Optional[str] = None,
    q: Optional[str] = Query(default=None, max_length=60),
    sort: str = Query(default="brand", pattern="^(brand|price_asc|price_desc|interest)$"),
    conn=Depends(db),
):
    where, params = [], []
    if brand:
        where.append("br.slug = ?")
        params.append(brand)
    if category:
        where.append("b.category = ?")
        params.append(category)
    if fuel:
        where.append("b.fuel = ?")
        params.append(fuel)
    if q:
        where.append("(b.model LIKE ? OR br.name LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    order = {
        "brand": "br.name, b.model",
        "price_asc": "b.approx_price_inr ASC",
        "price_desc": "b.approx_price_inr DESC",
        "interest": "interested DESC, br.name",
    }[sort]
    sql = BIKE_SELECT + (" WHERE " + " AND ".join(where) if where else "") + f" ORDER BY {order}"
    return [bike_row(r) for r in conn.execute(sql, params).fetchall()]


@app.get("/api/bikes/{bike_id}")
def get_bike(bike_id: int, conn=Depends(db)):
    row = conn.execute(BIKE_SELECT + " WHERE b.id = ?", (bike_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Bike not found")
    bike = bike_row(row)
    pools = conn.execute(POOL_SELECT + " WHERE p.bike_id = ? AND p.status != 'closed' ORDER BY p.city", (bike_id,))
    bike["pools"] = [pool_row(conn, p) for p in pools.fetchall()]
    return bike


@app.get("/api/categories")
def list_categories(conn=Depends(db)):
    return [r["category"] for r in conn.execute("SELECT DISTINCT category FROM bikes ORDER BY category")]


# --------------------------------------------------------------------------- pools
@app.get("/api/pools")
def list_pools(
    city: Optional[str] = None,
    status: Optional[str] = Query(default=None, pattern="^(open|negotiating|deal_secured|closed)$"),
    conn=Depends(db),
):
    where, params = ["p.status != 'closed'"] if not status else [], []
    if status:
        where.append("p.status = ?")
        params.append(status)
    if city:
        where.append("p.city = ?")
        params.append(city.strip().title())
    sql = POOL_SELECT + (" WHERE " + " AND ".join(where) if where else "")
    pools = [pool_row(conn, r) for r in conn.execute(sql, params).fetchall()]
    pools.sort(key=lambda p: (-p["member_count"], p["city"]))
    return pools


@app.get("/api/pools/{pool_id}")
def get_pool(pool_id: int, conn=Depends(db)):
    pool = pool_row(conn, get_pool_or_404(conn, pool_id))
    members = conn.execute(
        "SELECT name, timeline, joined_at FROM members WHERE pool_id = ? ORDER BY id", (pool_id,)
    ).fetchall()
    pool["members"] = [
        {"display_name": mask_name(m["name"]), "timeline": m["timeline"], "joined_at": m["joined_at"]} for m in members
    ]
    return pool


@app.post("/api/pools/join", status_code=201)
def join_pool(req: JoinRequest, conn=Depends(db)):
    if not conn.execute("SELECT 1 FROM bikes WHERE id = ?", (req.bike_id,)).fetchone():
        raise HTTPException(404, "Bike not found")
    pool = conn.execute("SELECT * FROM pools WHERE bike_id = ? AND city = ?", (req.bike_id, req.city)).fetchone()
    if pool is None:
        cur = conn.execute("INSERT INTO pools (bike_id, city) VALUES (?, ?)", (req.bike_id, req.city))
        pool = conn.execute("SELECT * FROM pools WHERE id = ?", (cur.lastrowid,)).fetchone()
    if pool["status"] == "closed":
        raise HTTPException(409, "This pool is closed")
    # The fee is a success fee: only due once a discount deal actually exists.
    fee_status = "pending" if pool["status"] == "deal_secured" else "not_due"
    member_secret = secrets.token_urlsafe(16)
    try:
        cur = conn.execute(
            """INSERT INTO members (pool_id, name, phone, email, timeline, secret, fee_status)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (pool["id"], req.name, req.phone, req.email, req.timeline, member_secret, fee_status),
        )
    except sqlite3.IntegrityError:
        raise HTTPException(409, "This phone number has already joined this pool") from None
    log.info("member %s joined pool %s", cur.lastrowid, pool["id"])
    return {
        "member_id": cur.lastrowid,
        "member_secret": member_secret,  # shown once; the UI keeps it in localStorage
        "pool": pool_row(conn, get_pool_or_404(conn, pool["id"])),
    }


# --------------------------------------------------------------------------- member self-service
@app.get("/api/members/{member_id}/fee")
def member_fee(member_id: int, x_member_secret: str = Header(default=""), conn=Depends(db)):
    member = get_member_or_403(conn, member_id, x_member_secret)
    pool = get_pool_or_404(conn, member["pool_id"])
    settings = get_settings()
    result = {
        "fee_status": member["fee_status"],
        "amount_inr": settings.platform_fee_inr,
        "pool_status": pool["status"],
        "discount_inr": pool["discount_inr"],
        "dealer_name": pool["dealer_name"],
    }
    if member["fee_status"] == "pending":
        uri = build_upi_uri(
            settings.upi_id,
            settings.upi_payee_name,
            settings.platform_fee_inr,
            f"BikePool fee #{member['id']}",
        )
        result.update({"upi_uri": uri, "qr_png": qr_data_url(uri)})
    return result


@app.delete("/api/members/{member_id}", status_code=204)
def leave_pool(member_id: int, x_member_secret: str = Header(default=""), conn=Depends(db)):
    member = get_member_or_403(conn, member_id, x_member_secret)
    if member["fee_status"] == "paid":
        raise HTTPException(409, "Fee already paid; contact support to withdraw")
    conn.execute("DELETE FROM members WHERE id = ?", (member_id,))


# --------------------------------------------------------------------------- stats
@app.get("/api/stats")
def stats(conn=Depends(db)):
    one = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "brands": one("SELECT COUNT(*) FROM brands"),
        "bikes": one("SELECT COUNT(*) FROM bikes"),
        "active_pools": one("SELECT COUNT(*) FROM pools WHERE status != 'closed'"),
        "interested_buyers": one(
            "SELECT COUNT(*) FROM members m JOIN pools p ON p.id = m.pool_id WHERE p.status != 'closed'"
        ),
        "deals_secured": one("SELECT COUNT(*) FROM pools WHERE status = 'deal_secured'"),
        "platform_fee_inr": get_settings().platform_fee_inr,
    }


# --------------------------------------------------------------------------- admin
@app.get("/api/admin/pools", dependencies=[Depends(require_admin)])
def admin_pools(conn=Depends(db)):
    pools = [pool_row(conn, r) for r in conn.execute(POOL_SELECT + " ORDER BY p.id DESC").fetchall()]
    for p in pools:
        p["members"] = [
            dict(m)
            for m in conn.execute(
                "SELECT id, name, phone, email, timeline, fee_status, joined_at "
                "FROM members WHERE pool_id = ? ORDER BY id",
                (p["id"],),
            )
        ]
    return pools


@app.patch("/api/admin/pools/{pool_id}", dependencies=[Depends(require_admin)])
def admin_update_pool(pool_id: int, upd: PoolUpdate, conn=Depends(db)):
    pool = get_pool_or_404(conn, pool_id)
    fields = upd.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(422, "No fields to update")
    merged = {**dict(pool), **fields}
    if merged["status"] == "deal_secured" and not (merged["dealer_name"] and merged["discount_inr"]):
        raise HTTPException(422, "dealer_name and discount_inr are required to secure a deal")
    if merged["discount_inr"] and merged["discount_inr"] >= pool["approx_price_inr"]:
        raise HTTPException(422, "Discount cannot exceed the bike's price")
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE pools SET {sets} WHERE id = ?", [*fields.values(), pool_id]
    )  # keys come from the model, not the client
    if fields.get("status") == "deal_secured":
        conn.execute(
            "UPDATE members SET fee_status = 'pending' WHERE pool_id = ? AND fee_status = 'not_due'", (pool_id,)
        )
    log.info("admin updated pool %s: %s", pool_id, list(fields))
    return pool_row(conn, get_pool_or_404(conn, pool_id))


@app.post("/api/admin/members/{member_id}/mark-paid", dependencies=[Depends(require_admin)])
def admin_mark_paid(member_id: int, conn=Depends(db)):
    row = conn.execute("SELECT fee_status FROM members WHERE id = ?", (member_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Member not found")
    if row["fee_status"] != "pending":
        raise HTTPException(409, f"Fee is '{row['fee_status']}', not pending")
    conn.execute("UPDATE members SET fee_status = 'paid' WHERE id = ?", (member_id,))
    return {"member_id": member_id, "fee_status": "paid"}


# --------------------------------------------------------------------------- frontend
@app.get("/admin", include_in_schema=False)
def admin_page():
    return FileResponse(STATIC_DIR / "admin.html")


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
