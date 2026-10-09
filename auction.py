"""拍賣規則：可用點數計算、出價檢查。"""
from models import Bid, Item, Student, db, now


class BidError(Exception):
    """出價不符合規則，訊息會直接顯示給學生看。"""


def committed_points(seat, exclude_item_id=None):
    """這位同學「已經用掉、但試算表還沒扣」的點數。

    = 還沒按「已扣點」的物品中，這位同學目前領先（或已得標）的出價總和。
    exclude_item_id：計算時略過這件物品（加價自己領先的物品時使用）。
    """
    total = 0
    items = Item.query.filter_by(deducted=False).all()
    for item in items:
        if item.id == exclude_item_id:
            continue
        top = item.top_bid()
        if top is not None and top.seat == seat:
            total += top.amount
    return total


def available_points(seat, sheet_points):
    """可用點數 = 試算表點數 − 已用掉的點數。"""
    return sheet_points.get(seat, 0) - committed_points(seat)


def min_next_bid(item):
    """這件物品下一次出價最少要多少點。"""
    top = item.top_bid()
    return item.min_price if top is None else top.amount + 1


def place_bid(item_id, seat, amount, sheet_points):
    """檢查規則並記錄出價。成功回傳 Bid，失敗丟出 BidError。"""
    # 先鎖住這位同學和這件物品（PostgreSQL 有效），
    # 避免兩個出價同時進來時算錯點數或最高價。
    student = db.session.get(Student, seat, with_for_update=True)
    item = db.session.get(Item, item_id, with_for_update=True)
    if student is None:
        raise BidError("找不到你的座號，請重新登入")
    if item is None:
        raise BidError("找不到這件物品")

    status = item.status()
    if status == "upcoming":
        raise BidError("這件物品還沒開始拍賣")
    if status == "ended":
        raise BidError("這件物品已經結標了")

    lowest = min_next_bid(item)
    if amount < lowest:
        if item.top_bid() is None:
            raise BidError(f"出價不能低於底價 {lowest} 點")
        raise BidError(f"出價至少要 {lowest} 點（比目前最高價多 1 點）")

    # 如果是加價自己正在領先的物品，原本的出價不重複計算
    budget = sheet_points.get(seat, 0) - committed_points(seat, exclude_item_id=item.id)
    if amount > budget:
        raise BidError(f"點數不足：這件物品你最多可以出 {max(budget, 0)} 點")

    bid = Bid(item_id=item.id, seat=seat, amount=amount, created_at=now())
    db.session.add(bid)
    db.session.commit()
    return bid
