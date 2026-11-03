from .conftest import ADMIN, join


# ------------------------------------------------------------------ catalog
def test_catalog_has_all_requested_brands(client):
    names = {b["name"] for b in client.get("/api/brands").json()}
    assert {"Honda", "Royal Enfield", "Bajaj", "Suzuki", "BMW Motorrad"} <= names


def test_every_bike_belongs_to_a_brand(client):
    bikes = client.get("/api/bikes").json()
    assert len(bikes) > 50
    assert all(b["brand"]["name"] for b in bikes)


def test_filter_by_brand_and_search(client):
    re_bikes = client.get("/api/bikes", params={"brand": "royal-enfield"}).json()
    assert re_bikes and {b["brand"]["slug"] for b in re_bikes} == {"royal-enfield"}
    assert any("Classic" in b["model"] for b in client.get("/api/bikes", params={"q": "classic"}).json())


def test_electric_filter(client):
    assert all(b["fuel"] == "electric" for b in client.get("/api/bikes", params={"fuel": "electric"}).json())


def test_unknown_bike_404(client):
    assert client.get("/api/bikes/99999").status_code == 404


def test_seed_is_idempotent(client):
    from app.db import init_db

    before = len(client.get("/api/bikes").json())
    init_db()
    assert len(client.get("/api/bikes").json()) == before


# ------------------------------------------------------------------ joining
def test_join_creates_pool_and_counts_interest(client, bike_id):
    r = join(client, bike_id)
    assert r.status_code == 201
    body = r.json()
    assert body["member_secret"] and body["pool"]["city"] == "Hyderabad"  # city normalised
    assert body["pool"]["member_count"] == 1
    assert client.get(f"/api/bikes/{bike_id}").json()["interested"] == 1


def test_second_buyer_joins_same_pool(client, bike_id):
    p1 = join(client, bike_id).json()["pool"]["id"]
    p2 = join(client, bike_id, name="Asha Rao", phone="9123456780", city="  HYDERABAD ").json()["pool"]["id"]
    assert p1 == p2
    assert client.get(f"/api/pools/{p1}").json()["member_count"] == 2


def test_different_city_makes_separate_pool(client, bike_id):
    a = join(client, bike_id).json()["pool"]["id"]
    b = join(client, bike_id, city="Pune").json()["pool"]["id"]
    assert a != b


def test_duplicate_phone_rejected(client, bike_id):
    join(client, bike_id)
    assert join(client, bike_id, phone="+91 98765 43210").status_code == 409


def test_phone_formats_normalised(client, bike_id):
    assert join(client, bike_id, phone="+91-98765-43210").status_code == 201


def test_invalid_inputs_rejected(client, bike_id):
    assert join(client, bike_id, phone="12345").status_code == 422
    assert join(client, bike_id, name="<script>").status_code == 422
    assert join(client, bike_id, city="123").status_code == 422
    assert join(client, bike_id, email="nope").status_code == 422
    assert join(client, bike_id, timeline="whenever").status_code == 422


def test_join_unknown_bike_404(client):
    assert join(client, 99999).status_code == 404


def test_public_pool_view_hides_contact_details(client, bike_id):
    pid = join(client, bike_id, email="rahul@example.com").json()["pool"]["id"]
    text = client.get(f"/api/pools/{pid}").text
    assert "9876543210" not in text and "rahul@example.com" not in text
    assert "Rahul K." in text and "Kumar" not in text


# ------------------------------------------------------------------ admin + fee flow
def test_admin_requires_token(client):
    assert client.get("/api/admin/pools").status_code == 401
    assert client.get("/api/admin/pools", headers={"X-Admin-Token": "wrong"}).status_code == 401
    assert client.get("/api/admin/pools", headers=ADMIN).status_code == 200


def test_admin_disabled_when_token_unset(client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "")
    assert client.get("/api/admin/pools", headers=ADMIN).status_code == 503


def test_secure_deal_requires_dealer_and_discount(client, bike_id):
    pid = join(client, bike_id).json()["pool"]["id"]
    r = client.patch(f"/api/admin/pools/{pid}", json={"status": "deal_secured"}, headers=ADMIN)
    assert r.status_code == 422


def test_discount_cannot_exceed_price(client, bike_id):
    pid = join(client, bike_id).json()["pool"]["id"]
    r = client.patch(f"/api/admin/pools/{pid}", json={"discount_inr": 900_000}, headers=ADMIN)
    assert r.status_code == 422


def test_full_fee_lifecycle(client, bike_id):
    j = join(client, bike_id).json()
    mid, secret, pid = j["member_id"], {"X-Member-Secret": j["member_secret"]}, j["pool"]["id"]

    # No fee before a deal exists (success-fee model)
    fee = client.get(f"/api/members/{mid}/fee", headers=secret).json()
    assert fee["fee_status"] == "not_due" and "qr_png" not in fee

    r = client.patch(
        f"/api/admin/pools/{pid}",
        json={
            "status": "deal_secured",
            "dealer_name": "Sri Motors",
            "discount_inr": 8000,
            "deal_valid_until": "2026-12-31",
        },
        headers=ADMIN,
    )
    assert r.status_code == 200 and r.json()["discount_inr"] == 8000

    fee = client.get(f"/api/members/{mid}/fee", headers=secret).json()
    assert fee["fee_status"] == "pending" and fee["amount_inr"] == 199
    assert fee["upi_uri"].startswith("upi://pay?pa=test@upi") and "am=199.00" in fee["upi_uri"]
    assert fee["qr_png"].startswith("data:image/png;base64,")

    assert client.post(f"/api/admin/members/{mid}/mark-paid", headers=ADMIN).status_code == 200
    assert client.post(f"/api/admin/members/{mid}/mark-paid", headers=ADMIN).status_code == 409  # not pending any more
    assert client.get(f"/api/members/{mid}/fee", headers=secret).json()["fee_status"] == "paid"
    assert client.delete(f"/api/members/{mid}", headers=secret).status_code == 409  # paid members can't self-leave


def test_late_joiner_to_secured_pool_owes_fee_immediately(client, bike_id):
    pid = join(client, bike_id).json()["pool"]["id"]
    client.patch(
        f"/api/admin/pools/{pid}",
        json={"status": "deal_secured", "dealer_name": "Sri Motors", "discount_inr": 5000},
        headers=ADMIN,
    )
    j = join(client, bike_id, name="Late Joiner", phone="9000000001").json()
    fee = client.get(f"/api/members/{j['member_id']}/fee", headers={"X-Member-Secret": j["member_secret"]}).json()
    assert fee["fee_status"] == "pending"


def test_closed_pool_rejects_joiners(client, bike_id):
    pid = join(client, bike_id).json()["pool"]["id"]
    client.patch(f"/api/admin/pools/{pid}", json={"status": "closed"}, headers=ADMIN)
    assert join(client, bike_id, name="Another One", phone="9000000002").status_code == 409


# ------------------------------------------------------------------ member self-service
def test_member_secret_required(client, bike_id):
    j = join(client, bike_id).json()
    assert client.get(f"/api/members/{j['member_id']}/fee").status_code == 403
    assert client.get(f"/api/members/{j['member_id']}/fee", headers={"X-Member-Secret": "bad"}).status_code == 403
    assert client.delete(f"/api/members/{j['member_id']}", headers={"X-Member-Secret": "bad"}).status_code == 403


def test_leave_pool(client, bike_id):
    j = join(client, bike_id).json()
    r = client.delete(f"/api/members/{j['member_id']}", headers={"X-Member-Secret": j["member_secret"]})
    assert r.status_code == 204
    assert client.get(f"/api/pools/{j['pool']['id']}").json()["member_count"] == 0


# ------------------------------------------------------------------ stats / frontend
def test_stats_reflect_activity(client, bike_id):
    join(client, bike_id)
    s = client.get("/api/stats").json()
    assert s["interested_buyers"] == 1 and s["active_pools"] == 1 and s["brands"] >= 15


def test_frontend_served(client):
    assert "BikePool" in client.get("/").text
    assert client.get("/admin").status_code == 200
    assert client.get("/app.js").status_code == 200
