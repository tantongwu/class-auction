"""資料表定義：學生、拍賣物品、出價紀錄。"""
from datetime import datetime, timedelta, timezone

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

# 台灣時間（UTC+8，沒有日光節約時間）。資料庫裡的時間一律存「台灣時間」。
TAIWAN = timezone(timedelta(hours=8))


def now():
    """目前的台灣時間（不帶時區資訊，方便和資料庫裡的時間比較）。"""
    return datetime.now(TAIWAN).replace(tzinfo=None)


class Student(db.Model):
    seat = db.Column(db.Integer, primary_key=True)  # 座號
    password_hash = db.Column(db.String(255), nullable=False)
    failed_logins = db.Column(db.Integer, default=0, nullable=False)
    locked_until = db.Column(db.DateTime)  # 密碼打錯太多次，暫時鎖定到這個時間


class Item(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, default="", nullable=False)
    min_price = db.Column(db.Integer, nullable=False)  # 底價
    start_at = db.Column(db.DateTime, nullable=False)
    end_at = db.Column(db.DateTime, nullable=False)
    closed_at = db.Column(db.DateTime)  # 老師提早結標的時間
    deducted = db.Column(db.Boolean, default=False, nullable=False)  # 老師已在試算表扣點
    image = db.Column(db.LargeBinary)  # 壓縮後的 JPEG 照片
    image_version = db.Column(db.Integer, default=0, nullable=False)

    bids = db.relationship(
        "Bid", backref="item", cascade="all, delete-orphan", order_by="Bid.id"
    )

    def status(self, at=None):
        """回傳 'upcoming'（還沒開始）、'active'（拍賣中）或 'ended'（已結標）。"""
        at = at or now()
        if self.closed_at or at >= self.end_at:
            return "ended"
        if at < self.start_at:
            return "upcoming"
        return "active"

    def top_bid(self):
        """目前最高的出價；同分時先出價的人優先。沒有人出價時回傳 None。"""
        best = None
        for bid in self.bids:
            if best is None or bid.amount > best.amount:
                best = bid
        return best


class Bid(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False, index=True)
    seat = db.Column(db.Integer, nullable=False, index=True)
    amount = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=now, nullable=False)
