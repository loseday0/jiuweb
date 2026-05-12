# -*- coding: utf-8 -*-
import datetime
import json
import os
import smtplib
import ssl
import pickle
import time
import hashlib
from urllib.request import Request, urlopen
from email.message import EmailMessage

# ================= 配置区 =================
CONFIG = {
    "thresholds": {
        "subscribe_premium": 0.0,
        "subscribe_premium_2": 0.0,
        "redeem_premium": -0.8,
        "redeem_premium_2": -1.2,
        "rotate_spread": 0.0,
        "rotate_spread_2": 1.0,
    },
    "cooldown_seconds_level1": 3600,
    "cooldown_seconds_level2": 3600,
    "email_enabled": True,   # 请确保开启
    "positions": {
        "160632": 1000000,
        "512690": 1000000,
    },
    "email": {
        "host": "smtp.163.com",
        "port": 465,
        "user": "exist0619@163.com",
        "password": "RAYXzFBWWRYwkfcu",   # ⚠️ 建议移到 GitHub Secrets
        "from": "exist0619@163.com",
        "to": ["6619110@qq.com"],
    },
}

QT_BASE_URL = "http://qt.gtimg.cn/q="
NAV_BASE_URL = "https://fundgz.1234567.com.cn/js/"
COEF_160632 = 0.95
COEF_512690 = 1.0

# 冷却记录文件（通过 GitHub Cache 持久化）
LAST_ALERTS_FILE = "last_alerts.pkl"

# ================= 工具函数（与原代码相同） =================
def _normalize_recipients(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []

def _fetch_url(url, encoding="gbk", timeout=8):
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        if encoding:
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                return raw.decode("utf-8", errors="ignore")
        return raw.decode("utf-8", errors="ignore")
    except Exception:
        return None

def _qt_parts(code):
    text = _fetch_url("{}{}".format(QT_BASE_URL, code), encoding="gbk")
    if not text:
        return []
    line = text.strip().splitlines()[0] if text.strip() else ""
    payload = line.split("=", 1)[-1].strip().rstrip(";")
    return payload.split("~")

def _fetch_change(code):
    parts = _qt_parts(code)
    if len(parts) <= 4:
        return None
    try:
        current_price = float(parts[3])
        prev_close = float(parts[4])
    except Exception:
        return None
    if prev_close == 0:
        return None
    return (current_price / prev_close - 1) * 100

def fetch_nav(code):
    text = _fetch_url("{}{}.js".format(NAV_BASE_URL, code), encoding="utf-8")
    if not text:
        return None
    line = text.strip()
    if not line:
        return None
    if line.startswith("jsonpgz(") and line.endswith(");"):
        line = line[len("jsonpgz(") : -2]
    try:
        payload = json.loads(line)
        return float(payload.get("dwjz"))
    except Exception:
        return None

def fetch_bid_ask(code):
    parts = _qt_parts(code)
    if not parts:
        return None, None, None, None

    def get_val(idx, func):
        if idx < len(parts):
            try:
                return func(parts[idx])
            except Exception:
                return None
        return None

    bid = get_val(9, float)
    ask = get_val(19, float)
    bid_qty = get_val(10, lambda x: int(float(x) * 100))
    ask_qty = get_val(20, lambda x: int(float(x) * 100))
    return bid, ask, bid_qty, ask_qty

def send_email(subject, content):
    if not CONFIG.get("email_enabled", True):
        print("邮件通知未开启，跳过发送")
        return False
    settings = CONFIG.get("email") or {}
    host = settings.get("host")
    port = settings.get("port", 465)
    user = settings.get("user")
    password = settings.get("password")
    sender = settings.get("from") or user
    recipients = _normalize_recipients(settings.get("to"))
    if not (host and user and password and sender and recipients):
        print("邮件配置不完整")
        return False
    
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.set_content(content)
    
    context = ssl.create_default_context()
    try:
        if int(port) == 465:
            with smtplib.SMTP_SSL(host, port, context=context, timeout=10) as smtp:
                smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=10) as smtp:
                smtp.starttls(context=context)
                smtp.login(user, password)
                smtp.send_message(msg)
        print("邮件发送成功")
        return True
    except Exception as e:
        print("邮件发送失败:", e)
        return False

def _limit_qty(qty, limit):
    if qty is None or qty == 0:
        return qty
    if limit is None:
        return qty
    try:
        return min(int(qty), int(limit))
    except Exception:
        return qty

def _level2_prefix(text, is_level2):
    return "★{}".format(text) if is_level2 else text

def _cooldown_key(base_key, level2):
    return "{}:{}".format(base_key, "L2" if level2 else "L1")

def _get_cooldown_seconds(level2):
    return CONFIG.get("cooldown_seconds_level2" if level2 else "cooldown_seconds_level1", 3600)

# 加载冷却记录
def load_last_alerts():
    if os.path.exists(LAST_ALERTS_FILE):
        try:
            with open(LAST_ALERTS_FILE, "rb") as f:
                return pickle.load(f)
        except Exception:
            return {}
    return {}

# 保存冷却记录
def save_last_alerts(alerts):
    try:
        with open(LAST_ALERTS_FILE, "wb") as f:
            pickle.dump(alerts, f)
    except Exception as e:
        print("保存冷却记录失败:", e)

# ================= 核心逻辑（与原代码相同，但移除了 HTTP 相关） =================
def _build_snapshot():
    now = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    index_change = _fetch_change("sz399987")
    nav1 = fetch_nav("160632")
    nav2 = fetch_nav("512690")
    rate = 0.0 if index_change is None else index_change / 100.0
    est1 = nav1 * (1 + rate * COEF_160632) if nav1 is not None else None
    est2 = nav2 * (1 + rate * COEF_512690) if nav2 is not None else None

    bid1, ask1, bid1_qty, ask1_qty = fetch_bid_ask("sz160632")
    bid2, ask2, bid2_qty, ask2_qty = fetch_bid_ask("sh512690")
    bid1 = None if bid1 in (None, 0) else bid1
    ask1 = None if ask1 in (None, 0) else ask1
    bid2 = None if bid2 in (None, 0) else bid2
    ask2 = None if ask2 in (None, 0) else ask2

    prem_bid1 = None if est1 in (None, 0) or bid1 is None else (bid1 / est1 - 1) * 100
    prem_ask1 = None if est1 in (None, 0) or ask1 is None else (ask1 / est1 - 1) * 100
    prem_bid2 = None if est2 in (None, 0) or bid2 is None else (bid2 / est2 - 1) * 100
    prem_ask2 = None if est2 in (None, 0) or ask2 is None else (ask2 / est2 - 1) * 100

    return {
        "now": now,
        "index_change": index_change,
        "est1": est1,
        "est2": est2,
        "bid1": bid1, "ask1": ask1, "bid1_qty": bid1_qty, "ask1_qty": ask1_qty,
        "bid2": bid2, "ask2": ask2, "bid2_qty": bid2_qty, "ask2_qty": ask2_qty,
        "prem_bid1": prem_bid1, "prem_ask1": prem_ask1,
        "prem_bid2": prem_bid2, "prem_ask2": prem_ask2,
    }

def _maybe_notify(snapshot, est, bid, ask, bid_qty, ask_qty, prem_bid, prem_ask, code, last_alerts):
    now = snapshot["now"]
    subscribe_threshold = CONFIG["thresholds"]["subscribe_premium"]
    subscribe_threshold_2 = CONFIG["thresholds"].get("subscribe_premium_2", subscribe_threshold)
    redeem_threshold = CONFIG["thresholds"]["redeem_premium"]
    redeem_threshold_2 = CONFIG["thresholds"].get("redeem_premium_2", redeem_threshold)

    action = None
    price = None
    qty = CONFIG.get("positions", {}).get(code, 0) or 0
    level2 = False
    if prem_bid is not None and prem_bid > subscribe_threshold:
        action = "申购"
        price = bid
        level2 = prem_bid > subscribe_threshold_2
    elif prem_ask is not None and prem_ask < redeem_threshold:
        action = "赎回"
        price = ask
        level2 = prem_ask < redeem_threshold_2
    if not action or price is None or est is None:
        return

    qty = _limit_qty(qty, bid_qty if action == "申购" else ask_qty)
    key = _cooldown_key("{}:{}".format(code, action), level2)
    last_time = last_alerts.get(key)
    if last_time and (now - last_time).total_seconds() < _get_cooldown_seconds(level2):
        print("冷却中，跳过通知:", key)
        return

    # 记录本次发送时间
    last_alerts[key] = now
    # 同时记录非 level2 的 key，避免 level1 单独再发
    base_key = "{}:{}".format(code, action)
    if level2:
        last_alerts[_cooldown_key(base_key, False)] = now

    title = "{} {}".format(code, action)
    if level2:
        title = "★{}".format(title)
    if action == "申购":
        amt = None if price is None or qty is None else price * qty
        profit = None if price is None or est is None or qty is None else round((price - est) * qty)
        msg = "{}|{}|{}|{}|{}\n>>>|{}|{}|{}".format("申购", code, "卖出", price, qty, "申购", 1.0, amt if amt else "")
    else:
        amt = None if price is None or qty is None else price * qty
        qty2 = None if amt is None or est in (None, 0) else amt / est
        profit = None if price is None or est is None or qty is None else round((est * 0.995 - price) * qty)
        msg = "{}|{}|{}|{}|{}\n>>>|{}|{}|{}".format("赎回", code, "买入", price, qty, "赎回", est, qty2)
    content = "{}\n时间: {}".format(msg, now.strftime("%Y-%m-%d %H:%M:%S"))
    send_email(title, content)

def _send_rotate_alert(now, sell_code, buy_code, sell_price, buy_price, qty, spread, level2, last_alerts):
    key = _cooldown_key("ROT:{}->{}".format(sell_code, buy_code), level2)
    last_time = last_alerts.get(key)
    if last_time and (now - last_time).total_seconds() < _get_cooldown_seconds(level2):
        return
    last_alerts[key] = now
    # 同时记录 level1 key
    base_key = "ROT:{}->{}".format(sell_code, buy_code)
    if level2:
        last_alerts[_cooldown_key(base_key, False)] = now

    title = "交换 {}->{}".format(sell_code, buy_code)
    if level2:
        title = "★{}".format(title)
    amt = None if sell_price is None or qty is None else sell_price * qty
    qty2 = None if amt is None or buy_price in (None, 0) else amt / buy_price
    msg = "交换|{}|卖出|{}|{}|>>>|{}|买入|{}|{} (价差:{:.2f}%)".format(
        sell_code, sell_price, qty, buy_code, buy_price, qty2, spread
    )
    send_email(title, "{}\n时间: {}".format(msg, now.strftime("%Y-%m-%d %H:%M:%S")))

def run_monitoring():
    print("开始执行监控...")
    last_alerts = load_last_alerts()
    snapshot = _build_snapshot()
    # ===== 调试输出 =====
    print("=== 诊断信息 ===")
    print("溢价率 bid1:", snapshot["prem_bid1"])
    print("溢价率 ask1:", snapshot["prem_ask1"])
    print("溢价率 bid2:", snapshot["prem_bid2"])
    print("溢价率 ask2:", snapshot["prem_ask2"])
    print("EST1:", snapshot["est1"], "EST2:", snapshot["est2"])
    print("阈值 申购:", CONFIG["thresholds"]["subscribe_premium"])
    print("阈值 赎回:", CONFIG["thresholds"]["redeem_premium"])
    print("阈值 交换:", CONFIG["thresholds"]["rotate_spread"])
    print("冷却记录 keys:", list(last_alerts.keys()))
    # =================
    now = snapshot["now"]

    # 处理申购/赎回提醒
    _maybe_notify(snapshot, snapshot["est1"], snapshot["bid1"], snapshot["ask1"],
                  snapshot["bid1_qty"], snapshot["ask1_qty"],
                  snapshot["prem_bid1"], snapshot["prem_ask1"], "160632", last_alerts)
    _maybe_notify(snapshot, snapshot["est2"], snapshot["bid2"], snapshot["ask2"],
                  snapshot["bid2_qty"], snapshot["ask2_qty"],
                  snapshot["prem_bid2"], snapshot["prem_ask2"], "512690", last_alerts)

    # 处理旋转提醒
    rotate_threshold = CONFIG["thresholds"]["rotate_spread"]
    rotate_threshold_2 = CONFIG["thresholds"].get("rotate_spread_2", rotate_threshold)
    pos1 = CONFIG.get("positions", {}).get("160632", 0) or 0
    pos2 = CONFIG.get("positions", {}).get("512690", 0) or 0

    if snapshot["prem_bid2"] is not None and snapshot["prem_ask1"] is not None:
        spread = snapshot["prem_bid2"] - snapshot["prem_ask1"]
        if spread > rotate_threshold:
            qty = _limit_qty(_limit_qty(pos2, snapshot["bid2_qty"]), snapshot["ask1_qty"])
            _send_rotate_alert(now, "512690", "160632", snapshot["bid2"], snapshot["ask1"],
                               qty, spread, spread > rotate_threshold_2, last_alerts)
    if snapshot["prem_bid1"] is not None and snapshot["prem_ask2"] is not None:
        spread = snapshot["prem_bid1"] - snapshot["prem_ask2"]
        if spread > rotate_threshold:
            qty = _limit_qty(_limit_qty(pos1, snapshot["bid1_qty"]), snapshot["ask2_qty"])
            _send_rotate_alert(now, "160632", "512690", snapshot["bid1"], snapshot["ask2"],
                               qty, spread, spread > rotate_threshold_2, last_alerts)

    # 保存冷却记录
    save_last_alerts(last_alerts)
    print("监控完成。")

if __name__ == "__main__":
    run_monitoring()
