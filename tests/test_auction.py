"""自動化測試：執行 `python -m pytest` 就會跑全部的測試。

測試使用暫時的 SQLite 資料庫和假的點數，不會連到 Google 試算表，也不會動到正式資料。
"""
import io
import re
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from PIL import Image as PILImage
from werkzeug.security import generate_password_hash

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sheet  # noqa: E402
from app import create_app  # noqa: E402
from models import Bid, Item, Student, db, now  # noqa: E402

TEACHER_PASSWORD = "teacher-secret"


@pytest.fixture
def app(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
            "TEACHER_PASSWORD": TEACHER_PASSWORD,
            # 假的試算表點數：1 號 10 點、2 號 20 點、3 號 -3 點
            "POINTS_OVERRIDE": {1: 10, 2: 20, 3: -3},
        }
    )
    with app.app_context():
        for seat in (1, 2, 3):
            db.session.add(Student(seat=seat, password_hash=generate_password_hash("1234")))
        db.session.commit()
        yield app
        db.session.remove()


@pytest.fixture
def client(app):
    return app.test_client()


def login_student(client, seat, password="1234"):
    return client.post("/login", data={"role": "student", "seat": seat, "password": password})


def login_teacher(client):
    return client.post("/login", data={"role": "teacher", "password": TEACHER_PASSWORD})


def make_item(name="鉛筆盒", min_price=3, starts_in=-60, ends_in=3600):
    """建立一件物品。starts_in / ends_in 是「從現在算起幾秒後」。"""
    item = Item(
        name=name,
        description="",
        min_price=min_price,
        start_at=now() + timedelta(seconds=starts_in),
        end_at=now() + timedelta(seconds=ends_in),
        deducted=False,
        image_version=0,
    )
    db.session.add(item)
    db.session.commit()
    return item.id


def bid(client, item_id, amount):
    response = client.post(f"/student/bid/{item_id}", data={"amount": amount}, follow_redirects=True)
    return response.get_data(as_text=True)


def state(client):
    return client.get("/api/student/state").get_json()


# ---------- 讀取試算表 ----------

def test_parse_points_reads_current_total_row():
    csv_text = (
        "時間戳記,點數說明,1號,2號,3號\n"
        "2026/8/31,開學獎金,3,3,3\n"
        ",,,,\n"
        ",目前累積點數,9,7,\n"
    )
    assert sheet.parse_points(csv_text) == {1: 9, 2: 7, 3: 0}


def test_parse_points_missing_row_is_error():
    with pytest.raises(sheet.SheetError):
        sheet.parse_points("時間戳記,1號\n2026/8/31,3\n")


# ---------- 登入與權限 ----------

def test_student_login_success_and_wrong_password(client):
    assert "/student" in login_student(client, 1).headers["Location"]
    client.get("/logout")
    response = login_student(client, 1, "0000")
    assert "/login" in response.headers["Location"]


def test_student_locked_after_five_wrong_passwords(client):
    for _ in range(5):
        login_student(client, 1, "0000")
    # 鎖定後，就算密碼正確也進不去
    response = login_student(client, 1, "1234")
    assert "/login" in response.headers["Location"]


def test_student_cannot_open_teacher_pages(client):
    login_student(client, 1)
    assert "/login" in client.get("/teacher").headers["Location"]
    assert "/login" in client.get("/teacher/results").headers["Location"]


def test_not_logged_in_cannot_bid_or_see_student_data(client, app):
    item_id = make_item()
    assert "/login" in client.get("/student").headers["Location"]
    assert client.get("/api/student/state").status_code == 401
    response = client.post(f"/student/bid/{item_id}", data={"amount": 5})
    assert "/login" in response.headers["Location"]
    assert Bid.query.count() == 0


def test_home_page_shows_items_without_login(client):
    """還沒登入的首頁：看得到拍賣中和即將開始的物品、最高價，看不到出價者座號和已結標物品。"""
    active = make_item("拍賣中的筆記本", min_price=1)
    make_item("即將開始的貼紙", starts_in=600, ends_in=1200)
    make_item("已結束的橡皮擦", starts_in=-1200, ends_in=-600)
    login_student(client, 2)
    bid(client, active, 7)
    client.get("/logout")

    page = client.get("/").get_data(as_text=True)
    assert "拍賣中的筆記本" in page and "即將開始的貼紙" in page
    assert "已結束的橡皮擦" not in page
    assert "<dd>7</dd>" in page  # 看得到目前最高價
    assert "2 號" not in page  # 看不到是誰出價
    assert "登入出價" in page


def test_teacher_wrong_password(client):
    response = client.post("/login", data={"role": "teacher", "password": "wrong"})
    assert "/login" in response.headers["Location"]


# ---------- 出價規則 ----------

def test_bid_must_reach_min_price_then_increase_by_one(client):
    item_id = make_item(min_price=3)
    login_student(client, 1)
    assert "不能低於底價 3 點" in bid(client, item_id, 2)
    assert "出價成功" in bid(client, item_id, 3)
    client.get("/logout")

    login_student(client, 2)
    assert "至少要 4 點" in bid(client, item_id, 3)
    assert "出價成功" in bid(client, item_id, 4)
    items = state(client)["items"]
    assert items[0]["top_amount"] == 4 and items[0]["i_lead"]


def test_cannot_bid_before_start_or_after_end(client):
    upcoming = make_item("還沒開始", starts_in=600, ends_in=1200)
    ended = make_item("已結束", starts_in=-1200, ends_in=-600)
    login_student(client, 1)
    assert "還沒開始" in bid(client, upcoming, 5)
    assert "已經結標" in bid(client, ended, 5)
    assert Bid.query.count() == 0


def test_cannot_bid_more_than_points(client):
    item_id = make_item(min_price=1)
    login_student(client, 1)  # 1 號有 10 點
    assert "點數不足" in bid(client, item_id, 11)
    assert "出價成功" in bid(client, item_id, 10)


def test_negative_points_cannot_bid(client):
    item_id = make_item(min_price=1)
    login_student(client, 3)  # 3 號是 -3 點
    assert "點數不足" in bid(client, item_id, 1)


def test_total_leading_bids_cannot_exceed_points(client):
    """1 號有 10 點：A 物品領先 6 點後，B 物品最多只能出 4 點。"""
    a = make_item("A", min_price=1)
    b = make_item("B", min_price=1)
    login_student(client, 1)
    assert "出價成功" in bid(client, a, 6)
    assert state(client)["available"] == 4
    assert "最多可以出 4 點" in bid(client, b, 5)
    assert "出價成功" in bid(client, b, 4)
    assert state(client)["available"] == 0


def test_raising_own_leading_bid_does_not_double_count(client):
    item_id = make_item(min_price=1)
    login_student(client, 1)
    assert "出價成功" in bid(client, item_id, 6)
    # 自己加價到 10：原本的 6 點不重複計算，所以可以
    assert "出價成功" in bid(client, item_id, 10)


def test_points_come_back_when_outbid(client):
    item_id = make_item(min_price=1)
    login_student(client, 1)
    bid(client, item_id, 8)
    assert state(client)["available"] == 2
    client.get("/logout")

    login_student(client, 2)
    bid(client, item_id, 9)
    client.get("/logout")

    login_student(client, 1)
    s = state(client)
    assert s["available"] == 10  # 被超過，點數還回來
    assert s["items"][0]["i_lead"] is False


def test_won_points_stay_used_until_teacher_marks_deducted(client, app):
    item_id = make_item(min_price=1)
    login_student(client, 1)
    bid(client, item_id, 7)
    client.get("/logout")

    login_teacher(client)
    client.post(f"/teacher/items/{item_id}/close")
    page = client.get("/teacher/results").get_data(as_text=True)
    assert "1 號" in page and "標記已扣點" in page
    client.get("/logout")

    login_student(client, 1)
    s = state(client)
    assert s["items"][0]["won"] is True
    assert s["available"] == 3  # 得標但老師還沒扣點，7 點仍算已用掉
    client.get("/logout")

    # 老師在試算表扣點後（點數變成 3），按「已扣點」
    login_teacher(client)
    app.config["POINTS_OVERRIDE"] = {1: 3, 2: 20, 3: -3}
    client.post(f"/teacher/items/{item_id}/deducted", data={"value": "1"})
    client.get("/logout")

    login_student(client, 1)
    assert state(client)["available"] == 3  # 不會被重複扣成 -4


def test_early_close_stops_bidding_and_shows_winner(client):
    item_id = make_item(min_price=1)
    login_student(client, 2)
    bid(client, item_id, 5)
    client.get("/logout")

    login_teacher(client)
    client.post(f"/teacher/items/{item_id}/close")
    page = client.get(f"/teacher/items/{item_id}").get_data(as_text=True)
    assert "得標者" in page and "2 號" in page
    client.get("/logout")

    login_student(client, 1)
    assert "已經結標" in bid(client, item_id, 6)


def test_items_are_independent(client):
    """每件物品的出價分開記錄，不會互相影響。"""
    a = make_item("A", min_price=1)
    b = make_item("B", min_price=2)
    login_student(client, 2)
    bid(client, a, 5)
    items = {it["name"]: it for it in state(client)["items"]}
    assert items["A"]["top_amount"] == 5
    assert items["B"]["top_amount"] is None
    assert items["B"]["min_next"] == 2


# ---------- 老師功能 ----------

def make_photo():
    buffer = io.BytesIO()
    PILImage.new("RGB", (3000, 2000), "orange").save(buffer, "PNG")
    buffer.seek(0)
    return buffer


def test_teacher_creates_item_with_photo(client):
    login_teacher(client)
    response = client.post(
        "/teacher/items/new",
        data={
            "name": "神秘禮物",
            "description": "好東西",
            "min_price": "5",
            "start_at": "2026-01-01T08:00",
            "end_at": "2026-01-02T08:00",
            "image": (make_photo(), "photo.png"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert "已新增「神秘禮物」" in response.get_data(as_text=True)
    item = Item.query.one()
    assert item.min_price == 5
    # 照片被壓縮成 JPEG，最長邊 1200
    photo = PILImage.open(io.BytesIO(item.image))
    assert photo.format == "JPEG" and max(photo.size) == 1200
    assert client.get(f"/items/{item.id}/image").status_code == 200


def test_end_time_must_be_after_start(client):
    login_teacher(client)
    response = client.post(
        "/teacher/items/new",
        data={"name": "X", "min_price": "1", "start_at": "2026-01-02T08:00", "end_at": "2026-01-01T08:00"},
        follow_redirects=True,
    )
    assert "結束時間要比開始時間晚" in response.get_data(as_text=True)
    assert Item.query.count() == 0


def test_min_price_locked_after_first_bid(client):
    item_id = make_item(min_price=3)
    login_student(client, 1)
    bid(client, item_id, 3)
    client.get("/logout")

    login_teacher(client)
    end = (now() + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
    client.post(
        f"/teacher/items/{item_id}/edit",
        data={"name": "新名字", "min_price": "1", "start_at": "2020-01-01T00:00", "end_at": end},
    )
    item = db.session.get(Item, item_id)
    assert item.name == "新名字"
    assert item.min_price == 3  # 底價沒被改


def test_generated_passwords_work(client):
    login_teacher(client)
    page = client.post("/teacher/passwords", data={"seat": "2"}).get_data(as_text=True)
    password = re.search(r'class="mono">(\d{4})<', page).group(1)
    client.get("/logout")

    assert "/student" in login_student(client, 2, password).headers["Location"]
    client.get("/logout")
    assert "/login" in login_student(client, 2, "1234").headers["Location"]  # 舊密碼失效
