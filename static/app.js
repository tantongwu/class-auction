// 倒數計時、學生出價頁每 5 秒自動更新、老師頁面定時重新整理。
(function () {
  "use strict";

  // ----- 倒數計時 -----
  function formatLeft(ms) {
    var s = Math.floor(ms / 1000);
    var days = Math.floor(s / 86400);
    var h = Math.floor((s % 86400) / 3600);
    var m = Math.floor((s % 3600) / 60);
    var sec = s % 60;
    var pad = function (n) { return (n < 10 ? "0" : "") + n; };
    return (days > 0 ? days + " 天 " : "") + h + ":" + pad(m) + ":" + pad(sec);
  }

  var reloadScheduled = false;
  function tickCountdowns() {
    var nowMs = Date.now();
    document.querySelectorAll(".countdown[data-until]").forEach(function (el) {
      var left = new Date(el.dataset.until).getTime() - nowMs;
      if (left <= 0) {
        el.textContent = "時間到";
        // 開始或結標的時間到了，稍等一下重新整理，讓畫面換成新的狀態
        if (!reloadScheduled) {
          reloadScheduled = true;
          setTimeout(safeReload, 1500);
        }
      } else {
        el.textContent = formatLeft(left);
      }
    });
  }
  tickCountdowns();
  setInterval(tickCountdowns, 1000);

  // 使用者正在輸入時不要重新整理，免得打到一半的數字不見
  function isTyping() {
    var el = document.activeElement;
    return el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA") && el.value !== "";
  }

  function safeReload() {
    if (isTyping()) {
      setTimeout(safeReload, 3000);
      return;
    }
    location.reload();
  }

  // ----- 老師頁面：定時重新整理 -----
  var auto = document.querySelector("[data-autorefresh]");
  if (auto) {
    setInterval(safeReload, Number(auto.dataset.autorefresh) * 1000);
  }

  // ----- 學生出價頁：每 5 秒向伺服器拿最新資料 -----
  var page = document.getElementById("student-page");
  if (!page) return;

  function setField(root, name, text) {
    var el = root.querySelector('[data-field="' + name + '"]');
    if (el && el.textContent !== String(text)) el.textContent = text;
  }

  function leadMessage(it) {
    if (it.won) return '<span class="good">🎉 恭喜你得標！</span>';
    if (it.status === "ended" && it.my_best !== null) return '<span class="muted">沒有得標</span>';
    if (it.i_lead) return '<span class="good">你目前領先</span>';
    if (it.my_best !== null) return '<span class="bad">你的出價被超過了</span>';
    return "";
  }

  function apply(state) {
    var cards = page.querySelectorAll("[data-item]");
    // 物品數量或狀態改變（例如開始、結標、新增物品）時，整頁重新整理
    var changed = cards.length !== state.items.length;
    state.items.forEach(function (it, i) {
      var card = cards[i];
      if (!card || card.dataset.item !== String(it.id) || card.dataset.status !== it.status) {
        changed = true;
      }
    });
    if (changed) {
      safeReload();
      return;
    }

    if (state.available !== null) {
      setField(page, "available", state.available);
      setField(page, "sheet_points", state.sheet_points);
    }
    state.items.forEach(function (it, i) {
      var card = cards[i];
      setField(card, "top_amount", it.top_amount !== null ? it.top_amount : "尚無人出價");
      setField(card, "my_best", it.my_best !== null ? it.my_best : "—");
      var msg = card.querySelector('[data-field="lead_msg"]');
      var html = leadMessage(it);
      if (msg && msg.innerHTML !== html) msg.innerHTML = html;
      var input = card.querySelector('[data-field="min_next"]');
      if (input) {
        input.min = it.min_next;
        input.placeholder = "至少 " + it.min_next + " 點";
      }
    });
  }

  function poll() {
    fetch(page.dataset.stateUrl, { credentials: "same-origin" })
      .then(function (r) {
        if (r.status === 401) { location.href = "/login"; return null; }
        return r.ok ? r.json() : null;
      })
      .then(function (state) { if (state) apply(state); })
      .catch(function () { /* 網路暫時不穩，下次再試 */ });
  }
  setInterval(poll, 5000);
})();
