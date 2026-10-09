"""班級拍賣點數小幫手：網站主程式。

本機執行：python app.py
正式環境（Zeabur）：gunicorn "app:create_app()"
"""
import hmac
import io
import os
import secrets
from datetime import datetime, timedelta
from functools import wraps

from dotenv import load_dotenv
from flask import (
    Flask,
    Response,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from PIL import Image as PILImage
from PIL import ImageOps, UnidentifiedImageError
from werkzeug.security import check_password_hash, generate_password_hash

import sheet
from auction import BidError, available_points, min_next_bid, place_bid
from models import TAIWAN, Item, Student, db, now

MAX_LOGIN_FAILS = 5
LOCK_MINUTES = 5
DEFAULT_SEATS = range(1, 31)

STATUS_TEXT = {"upcoming": "尚未開始", "active": "拍賣中", "ended": "已結標"}


# ---------- 設定 ----------

def database_url():
    """讀取 DATABASE_URL；沒設定時用本機的 SQLite 檔案。"""
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return "sqlite:///" + os.path.join(os.path.dirname(os.path.abspath(__file__)), "auction.db")
    # Zeabur 給的是 postgres:// 或 postgresql://，要告訴 SQLAlchemy 用 psycopg 連線
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def create_app(test_config=None):
    load_dotenv()
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY") or "dev-only-change-me",
        SQLALCHEMY_DATABASE_URI=database_url(),
        SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
        TEACHER_PASSWORD=os.environ.get("TEACHER_PASSWORD", ""),
        SHEET_ID=os.environ.get("SHEET_ID", "13fp6-NU20QT75KteyX7KI2STr9z_o34ew9JnwevOaWE"),
        SHEET_GID=os.environ.get("SHEET_GID", ""),
        MAX_CONTENT_LENGTH=20 * 1024 * 1024,  # 上傳照片最大 20MB
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_HTTPONLY=True,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        TEACHER_FAILS=0,
        TEACHER_LOCKED_UNTIL=None,
    )
    if test_config:
        app.config.update(test_config)

    db.init_app(app)
    with app.app_context():
        db.create_all()

    register_helpers(app)
    register_routes(app)
    return app


def get_sheet_points():
    """讀取試算表點數。測試時可以用 app.config['POINTS_OVERRIDE'] 指定假資料。"""
    from flask import current_app

    override = current_app.config.get("POINTS_OVERRIDE")
    if override is not None:
        return override
    return sheet.get_points(current_app.config["SHEET_ID"], current_app.config["SHEET_GID"])


# ---------- 小工具 ----------

def iso(dt):
    """把台灣時間轉成網頁 JavaScript 看得懂的格式，例如 2026-10-09T13:00:00+08:00。"""
    return dt.replace(tzinfo=TAIWAN).isoformat()


def compress_image(file_storage):
    """把上傳的照片轉正、縮小到最長邊 1200 像素，存成 JPEG。"""
    try:
        img = PILImage.open(file_storage.stream)
        img = ImageOps.exif_transpose(img)  # 手機直拍的照片轉正
        img = img.convert("RGB")
    except (UnidentifiedImageError, OSError):
        raise ValueError("無法讀取這張照片，請換一張 JPG 或 PNG 圖片")
    img.thumbnail((1200, 1200))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=80, optimize=True)
    return out.getvalue()


def parse_datetime(text):
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M")
    except (TypeError, ValueError):
        return None


def login_required(role):
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if session.get("role") != role:
                if request.path.startswith("/api/"):
                    return jsonify(error="請重新登入"), 401
                return redirect(url_for("login"))
            if role == "student" and db.session.get(Student, session.get("seat")) is None:
                session.clear()  # 老師刪除或重設了這個座號
                return redirect(url_for("login"))
            return view(*args, **kwargs)

        return wrapper

    return decorator


def sorted_items():
    """拍賣中的排前面（快結束的優先），再來是還沒開始的，最後是已結標的。"""
    items = Item.query.all()
    at = now()

    def key(item):
        status = item.status(at)
        if status == "active":
            return (0, item.end_at)
        if status == "upcoming":
            return (1, item.start_at)
        return (2, -(item.closed_at or item.end_at).timestamp())

    return sorted(items, key=key)


def student_state(seat):
    """學生出價頁需要的所有資料（畫面和自動更新共用）。"""
    sheet_error = None
    try:
        points = get_sheet_points()
    except sheet.SheetError as error:
        points, sheet_error = None, str(error)

    at = now()
    items = []
    for item in sorted_items():
        status = item.status(at)
        top = item.top_bid()
        my_bids = [b.amount for b in item.bids if b.seat == seat]
        i_lead = top is not None and top.seat == seat
        items.append(
            {
                "id": item.id,
                "name": item.name,
                "description": item.description,
                "has_image": item.image is not None,
                "image_version": item.image_version,
                "min_price": item.min_price,
                "status": status,
                "status_text": STATUS_TEXT[status],
                "top_amount": top.amount if top else None,
                "min_next": min_next_bid(item),
                "my_best": max(my_bids) if my_bids else None,
                "i_lead": i_lead,
                "won": status == "ended" and i_lead,
                "start_at": iso(item.start_at),
                "end_at": iso(item.closed_at or item.end_at),
            }
        )
    return {
        "seat": seat,
        "sheet_points": points.get(seat, 0) if points is not None else None,
        "available": available_points(seat, points) if points is not None else None,
        "sheet_error": sheet_error,
        "items": items,
    }


# ---------- 網頁畫面用的格式 ----------

def register_helpers(app):
    @app.template_filter("tw")
    def format_time(dt):
        if dt is None:
            return ""
        return f"{dt.month}/{dt.day} {dt:%H:%M}"

    @app.context_processor
    def inject():
        return {"STATUS_TEXT": STATUS_TEXT, "now": now}


# ---------- 網址 ----------

def register_routes(app):
    # ----- 登入 -----

    @app.route("/")
    def index():
        if session.get("role") == "teacher":
            return redirect(url_for("teacher_home"))
        if session.get("role") == "student":
            return redirect(url_for("student_home"))
        # 還沒登入：顯示拍賣中和即將開始的物品（不顯示出價者座號）
        at = now()
        items = [it for it in sorted_items() if it.status(at) != "ended"]
        return render_template("home.html", items=items, iso=iso)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "GET":
            return render_template("login.html")

        if request.form.get("role") == "teacher":
            return teacher_login()

        seat = request.form.get("seat", type=int)
        password = request.form.get("password", "").strip()
        student = db.session.get(Student, seat) if seat else None
        if student is None:
            flash("座號或密碼錯誤", "error")
            return redirect(url_for("login"))
        if student.locked_until and student.locked_until > now():
            flash(f"密碼錯誤太多次，請 {LOCK_MINUTES} 分鐘後再試", "error")
            return redirect(url_for("login"))
        if not check_password_hash(student.password_hash, password):
            student.failed_logins += 1
            if student.failed_logins >= MAX_LOGIN_FAILS:
                student.failed_logins = 0
                student.locked_until = now() + timedelta(minutes=LOCK_MINUTES)
            db.session.commit()
            flash("座號或密碼錯誤", "error")
            return redirect(url_for("login"))

        student.failed_logins = 0
        student.locked_until = None
        db.session.commit()
        session.clear()
        session.permanent = True
        session["role"] = "student"
        session["seat"] = student.seat
        return redirect(url_for("student_home"))

    def teacher_login():
        expected = app.config["TEACHER_PASSWORD"]
        if not expected:
            flash("尚未設定老師密碼（環境變數 TEACHER_PASSWORD）", "error")
            return redirect(url_for("login"))
        locked = app.config["TEACHER_LOCKED_UNTIL"]
        if locked and locked > now():
            flash(f"密碼錯誤太多次，請 {LOCK_MINUTES} 分鐘後再試", "error")
            return redirect(url_for("login"))
        password = request.form.get("password", "")
        if not hmac.compare_digest(password.encode(), expected.encode()):
            app.config["TEACHER_FAILS"] += 1
            if app.config["TEACHER_FAILS"] >= MAX_LOGIN_FAILS:
                app.config["TEACHER_FAILS"] = 0
                app.config["TEACHER_LOCKED_UNTIL"] = now() + timedelta(minutes=LOCK_MINUTES)
            flash("老師密碼錯誤", "error")
            return redirect(url_for("login"))
        app.config["TEACHER_FAILS"] = 0
        session.clear()
        session.permanent = True
        session["role"] = "teacher"
        return redirect(url_for("teacher_home"))

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("index"))

    # ----- 學生 -----

    @app.route("/student")
    @login_required("student")
    def student_home():
        return render_template("student.html", state=student_state(session["seat"]))

    @app.route("/api/student/state")
    @login_required("student")
    def student_state_api():
        return jsonify(student_state(session["seat"]))

    @app.route("/student/bid/<int:item_id>", methods=["POST"])
    @login_required("student")
    def student_bid(item_id):
        amount = request.form.get("amount", type=int)
        if amount is None or amount <= 0:
            flash("請輸入正確的點數（整數）", "error")
            return redirect(url_for("student_home"))
        try:
            points = get_sheet_points()
            place_bid(item_id, session["seat"], amount, points)
        except sheet.SheetError as error:
            db.session.rollback()
            flash(f"暫時讀不到點數，請稍後再試（{error}）", "error")
        except BidError as error:
            db.session.rollback()
            flash(str(error), "error")
        else:
            flash(f"出價成功：{amount} 點", "success")
        return redirect(url_for("student_home") + f"#item-{item_id}")

    @app.route("/items/<int:item_id>/image")
    def item_image(item_id):
        # 首頁不用登入就能看物品，所以照片也公開
        item = db.session.get(Item, item_id)
        if item is None or item.image is None:
            abort(404)
        response = Response(item.image, mimetype="image/jpeg")
        response.headers["Cache-Control"] = "public, max-age=86400"
        return response

    # ----- 老師：物品 -----

    @app.route("/teacher")
    @login_required("teacher")
    def teacher_home():
        return render_template("teacher.html", items=sorted_items())

    @app.route("/teacher/items/new", methods=["GET", "POST"])
    @login_required("teacher")
    def item_new():
        if request.method == "POST":
            item = Item(image_version=0, deducted=False)
            if save_item_form(item, is_new=True):
                db.session.add(item)
                db.session.commit()
                flash(f"已新增「{item.name}」", "success")
                return redirect(url_for("teacher_home"))
        start = now().replace(second=0, microsecond=0)
        return render_template(
            "item_form.html",
            item=None,
            form=request.form,
            default_start=start,
            default_end=start + timedelta(days=1),
            locked=False,
        )

    @app.route("/teacher/items/<int:item_id>/edit", methods=["GET", "POST"])
    @login_required("teacher")
    def item_edit(item_id):
        item = db.get_or_404(Item, item_id)
        locked = bool(item.bids)  # 有人出價後，底價和開始時間不能改
        if request.method == "POST":
            if save_item_form(item, is_new=False):
                db.session.commit()
                flash(f"已儲存「{item.name}」", "success")
                return redirect(url_for("item_detail", item_id=item.id))
        return render_template(
            "item_form.html",
            item=item,
            form=request.form,
            default_start=item.start_at,
            default_end=item.end_at,
            locked=locked,
        )

    def save_item_form(item, is_new):
        """檢查並套用表單內容。有錯誤時用 flash 顯示並回傳 False。"""
        form = request.form
        errors = []
        locked = not is_new and bool(item.bids)

        name = form.get("name", "").strip()
        if not name:
            errors.append("請輸入物品名稱")
        min_price = form.get("min_price", type=int)
        start_at = parse_datetime(form.get("start_at"))
        end_at = parse_datetime(form.get("end_at"))
        if locked:
            min_price, start_at = item.min_price, item.start_at
        if min_price is None or min_price < 1:
            errors.append("底價要是 1 以上的整數")
        if start_at is None or end_at is None:
            errors.append("請設定開始和結束時間")
        elif end_at <= start_at:
            errors.append("結束時間要比開始時間晚")

        image_bytes = None
        upload = request.files.get("image")
        if upload and upload.filename:
            try:
                image_bytes = compress_image(upload)
            except ValueError as error:
                errors.append(str(error))

        if errors:
            for error in errors:
                flash(error, "error")
            return False

        item.name = name[:100]
        item.description = form.get("description", "").strip()
        item.min_price = min_price
        item.start_at = start_at
        item.end_at = end_at
        if image_bytes:
            item.image = image_bytes
            item.image_version = (item.image_version or 0) + 1
        return True

    @app.route("/teacher/items/<int:item_id>")
    @login_required("teacher")
    def item_detail(item_id):
        item = db.get_or_404(Item, item_id)
        bids = sorted(item.bids, key=lambda b: (-b.amount, b.id))
        return render_template("item_detail.html", item=item, bids=bids, top=item.top_bid())

    @app.route("/teacher/items/<int:item_id>/close", methods=["POST"])
    @login_required("teacher")
    def item_close(item_id):
        item = db.get_or_404(Item, item_id)
        if item.status() != "ended":
            item.closed_at = now()
            db.session.commit()
            flash(f"「{item.name}」已提早結標", "success")
        return redirect(url_for("item_detail", item_id=item.id))

    @app.route("/teacher/items/<int:item_id>/delete", methods=["POST"])
    @login_required("teacher")
    def item_delete(item_id):
        item = db.get_or_404(Item, item_id)
        db.session.delete(item)
        db.session.commit()
        flash(f"已刪除「{item.name}」", "success")
        return redirect(url_for("teacher_home"))

    # ----- 老師：得標統計 -----

    @app.route("/teacher/results")
    @login_required("teacher")
    def results():
        rows = []
        per_seat = {}
        for item in sorted_items():
            if item.status() != "ended":
                continue
            top = item.top_bid()
            rows.append({"item": item, "top": top})
            if top and not item.deducted:
                per_seat[top.seat] = per_seat.get(top.seat, 0) + top.amount
        return render_template("results.html", rows=rows, per_seat=sorted(per_seat.items()))

    @app.route("/teacher/items/<int:item_id>/deducted", methods=["POST"])
    @login_required("teacher")
    def item_deducted(item_id):
        item = db.get_or_404(Item, item_id)
        if item.status() == "ended":
            item.deducted = request.form.get("value") == "1"
            db.session.commit()
        return redirect(url_for("results"))

    # ----- 老師：學生密碼 -----

    @app.route("/teacher/passwords", methods=["GET", "POST"])
    @login_required("teacher")
    def passwords():
        try:
            seats = sorted(get_sheet_points().keys())
        except sheet.SheetError:
            seats = list(DEFAULT_SEATS)

        new_passwords = {}
        if request.method == "POST":
            target = request.form.get("seat")
            chosen = seats if target == "all" else [int(target)] if target and target.isdigit() else []
            for seat in chosen:
                password = f"{secrets.randbelow(10000):04d}"
                student = db.session.get(Student, seat) or Student(seat=seat)
                student.password_hash = generate_password_hash(password)
                student.failed_logins = 0
                student.locked_until = None
                db.session.add(student)
                new_passwords[seat] = password
            db.session.commit()

        has_password = {s.seat for s in Student.query.all()}
        return render_template(
            "passwords.html", seats=seats, has_password=has_password, new_passwords=new_passwords
        )


if __name__ == "__main__":
    # host="0.0.0.0" 讓同一個 Wi-Fi 的手機也能連進來測試；
    # 不開 debug 模式，避免區網裡的其他人看到除錯畫面。改程式存檔後會自動重新載入。
    create_app().run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), use_reloader=True)
