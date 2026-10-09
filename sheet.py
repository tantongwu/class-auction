"""從 Google 試算表讀取每個座號的「目前累積點數」。

試算表要設定成「知道連結的任何人都能檢視」，網站用「下載成 CSV」的網址讀取，
不需要任何 Google 金鑰，也不會修改試算表。
"""
import csv
import io
import re
import threading
import time

import requests

CACHE_SECONDS = 60
POINTS_ROW_LABEL = "目前累積點數"

_cache = {"points": None, "time": 0.0}
_lock = threading.Lock()


class SheetError(Exception):
    """讀不到試算表，或找不到點數那一列。"""


def parse_points(csv_text):
    """把 CSV 文字轉成 {座號: 點數}。

    找第一列裡「1號、2號……」這些欄位標題，再找寫著「目前累積點數」的那一列。
    """
    rows = list(csv.reader(io.StringIO(csv_text)))
    if not rows:
        raise SheetError("試算表是空的")

    seat_columns = {}
    for col, title in enumerate(rows[0]):
        match = re.fullmatch(r"\s*(\d+)\s*號\s*", title)
        if match:
            seat_columns[int(match.group(1))] = col
    if not seat_columns:
        raise SheetError("試算表第一列找不到「1號、2號……」欄位")

    points_row = next(
        (row for row in rows if any(cell.strip() == POINTS_ROW_LABEL for cell in row)),
        None,
    )
    if points_row is None:
        raise SheetError(f"試算表裡找不到「{POINTS_ROW_LABEL}」這一列")

    points = {}
    for seat, col in seat_columns.items():
        raw = points_row[col].strip().replace(",", "") if col < len(points_row) else ""
        try:
            points[seat] = int(float(raw)) if raw else 0
        except ValueError:
            points[seat] = 0
    return points


def _download(sheet_id, gid):
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
    if gid:
        url += f"&gid={gid}"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
    except requests.RequestException as error:
        raise SheetError(f"無法連線到 Google 試算表：{error}") from error
    response.encoding = "utf-8"
    return response.text


def get_points(sheet_id, gid=""):
    """回傳 {座號: 點數}，結果會暫存 60 秒。

    讀取失敗時，如果之前讀過，就先用上一次的結果；完全沒讀過才會出錯。
    """
    with _lock:
        if _cache["points"] is not None and time.time() - _cache["time"] < CACHE_SECONDS:
            return _cache["points"]
        try:
            points = parse_points(_download(sheet_id, gid))
        except SheetError:
            if _cache["points"] is not None:
                return _cache["points"]
            raise
        _cache["points"] = points
        _cache["time"] = time.time()
        return points


def clear_cache():
    with _lock:
        _cache["points"] = None
        _cache["time"] = 0.0
