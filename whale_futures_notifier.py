# whale_futures_notifier.py
# Notifier Telegram untuk posisi FUTURES whale di Hyperliquid (perpetual).
# Dijalankan oleh .github/workflows/whale-futures-telegram.yml (GitHub Actions).
# Ini file TERPISAH dari tracker spot Solana lama — yang lama tidak diubah.
# Crypto = SEMUA koin Hyperliquid. Gold = PAXG (emas tokenisasi, perp 10x di Hyperliquid),
# diberi label GOLD · PAXG di setiap pesan. Bukan spot XAU/USD.
# Fokus khusus: PUMP/USDT — kalau PUMP_PRIORITY aktif, alert PUMP memakai ambang
# PUMP_MIN_SIZE_USD (lebih rendah dari ambang global), berlabel PRIORITAS, dan dikirim
# lebih dulu. Alert koin lain & Gold tetap jalan dengan ambang global MIN_SIZE_USD.
#
# Yang dikirim ke Telegram:
#   1. Posisi futures BARU yang besar (koin termasuk GOLD · PAXG, Long/Short, entry, ukuran, leverage, TP, SL, harga likuidasi)
#   2. TP / SL KENA (harga melewati level TP/SL sejak cek sebelumnya, atau posisi tutup saat harga melewati TP/SL)
#   3. Harga MENDEKAT ke likuidasi (jarak mark ke harga likuidasi <= LIQ_DANGER_PCT)
#
# Rahasia TIDAK ditulis di file ini. Workflow menyuntikkannya dari GitHub Secrets:
#   TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
# State posisi terakhir disimpan di folder .whale-state (dipulihkan via actions/cache).
# Hanya memakai library standar Python — tidak perlu pip install apa pun.

import json
import os
import time
import urllib.request

HL_INFO = "https://api.hyperliquid.xyz/info"
HL_LEADERBOARD = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
STATE_DIR = ".whale-state"
STATE_FILE = os.path.join(STATE_DIR, "state.json")
NL = chr(10)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
MIN_SIZE_USD = float(os.environ.get("MIN_SIZE_USD", "500000"))
LIQ_DANGER_PCT = float(os.environ.get("LIQ_DANGER_PCT", "12"))
ALERT_NEW = os.environ.get("ALERT_NEW_POSITION", "1") == "1"
ALERT_TPSL = os.environ.get("ALERT_TP_SL_HIT", "1") == "1"
ALERT_LIQ = os.environ.get("ALERT_LIQ_DANGER", "1") == "1"
PUMP_PRIORITY = os.environ.get("PUMP_PRIORITY", "1") == "1"
PUMP_MIN_SIZE_USD = float(os.environ.get("PUMP_MIN_SIZE_USD", "100000"))
TEST_MODE = os.environ.get("TEST_MODE", "") == "true"
MAX_MESSAGES = 8


def min_for(coin):
    return PUMP_MIN_SIZE_USD if (PUMP_PRIORITY and coin == "PUMP") else MIN_SIZE_USD


def post_json(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as res:
        return json.loads(res.read().decode())


def get_json(url):
    with urllib.request.urlopen(url, timeout=25) as res:
        return json.loads(res.read().decode())


def num(v, fallback=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return fallback


def usd(v):
    a = abs(v)
    if a >= 1e9:
        return "$" + format(v / 1e9, ".2f") + "B"
    if a >= 1e6:
        return "$" + format(v / 1e6, ".2f") + "M"
    if a >= 1e3:
        return "$" + format(v / 1e3, ".1f") + "K"
    return "$" + format(v, ".0f")


def px(v):
    if v >= 1000:
        return format(v, ",.1f")
    if v >= 100:
        return format(v, ".2f")
    if v >= 1:
        return format(v, ".3f")
    if v >= 0.01:
        return format(v, ".4f")
    return format(v, ".6f")


def send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID belum di-set di GitHub Secrets.")
        return False
    url = "https://api.telegram.org/bot" + BOT_TOKEN + "/sendMessage"
    try:
        data = post_json(url, {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True})
        return bool(data.get("ok"))
    except Exception as e:
        print("Gagal kirim Telegram:", e)
        return False


def fetch_marks():
    data = post_json(HL_INFO, {"type": "metaAndAssetCtxs"})
    universe = data[0]["universe"]
    ctxs = data[1]
    marks = {}
    for i, u in enumerate(universe):
        if i < len(ctxs):
            marks[u["name"]] = num(ctxs[i].get("markPx"))
    return marks


def fetch_positions():
    lb = get_json(HL_LEADERBOARD)
    rows = sorted(lb.get("leaderboardRows", []), key=lambda r: num(r.get("accountValue")), reverse=True)
    addresses = [r["ethAddress"].lower() for r in rows[:80]]
    marks = fetch_marks()
    positions = []
    whales_with_pos = 0
    checked = 0
    for addr in addresses:
        if checked >= 24 and whales_with_pos >= 12:
            break
        checked += 1
        try:
            state = post_json(HL_INFO, {"type": "clearinghouseState", "user": addr})
            orders = post_json(HL_INFO, {"type": "frontendOpenOrders", "user": addr})
        except Exception:
            continue
        aps = state.get("assetPositions", [])
        if aps:
            whales_with_pos += 1
        for ap in aps:
            p = ap.get("position", {})
            size_usd = num(p.get("positionValue"))
            szi = num(p.get("szi"))
            if size_usd < 25000 or szi == 0:
                continue
            side = "SHORT" if szi < 0 else "LONG"
            entry = num(p.get("entryPx"))
            tp = None
            sl = None
            for o in orders or []:
                if o.get("coin") != p.get("coin"):
                    continue
                if not o.get("isTrigger") and not o.get("isPositionTpsl"):
                    continue
                trig = num(o.get("triggerPx"))
                if trig <= 0:
                    continue
                profit_side = trig > entry if side == "LONG" else trig < entry
                if profit_side:
                    if tp is None or abs(trig - entry) < abs(tp - entry):
                        tp = trig
                else:
                    if sl is None or abs(trig - entry) < abs(sl - entry):
                        sl = trig
            lev = p.get("leverage") or {}
            liq_raw = p.get("liquidationPx")
            positions.append({
                "key": addr + "|" + str(p.get("coin")),
                "whale": addr,
                "coin": p.get("coin"),
                "side": side,
                "entry": entry,
                "size_usd": size_usd,
                "size_coin": abs(szi),
                "leverage": int(num(lev.get("value"), 1)),
                "lev_type": lev.get("type", "cross"),
                "mark": marks.get(p.get("coin"), entry),
                "liq": num(liq_raw) if liq_raw else None,
                "tp": tp,
                "sl": sl,
            })
    return positions, marks


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return None


def save_state(positions):
    os.makedirs(STATE_DIR, exist_ok=True)
    state = {"positions": {p["key"]: p for p in positions}, "saved_at": int(time.time() * 1000)}
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def pos_text(title, p):
    arrow = "LONG" if p["side"] == "LONG" else "SHORT"
    coin_label = "GOLD \u00b7 PAXG" if p["coin"] == "PAXG" else ("PRIORITAS \u00b7 PUMP/USDT" if p["coin"] == "PUMP" else p["coin"])
    lines = [
        title,
        "<b>" + coin_label + " " + arrow + " " + str(p["leverage"]) + "x</b> (" + p["lev_type"] + ")",
        "Ukuran: <b>" + usd(p["size_usd"]) + "</b> - " + format(p["size_coin"], ".4f") + " " + p["coin"],
        "Entry: $" + px(p["entry"]) + " - Mark: $" + px(p["mark"]),
        ("TP: $" + px(p["tp"])) if p["tp"] else "TP: belum pasang TP",
        ("SL: $" + px(p["sl"])) if p["sl"] else "SL: belum pasang SL",
        ("Liq: $" + px(p["liq"])) if p["liq"] else "Liq: sangat jauh / cross",
        "Whale: <code>" + p["whale"][:10] + "...</code> - Hyperliquid Futures",
    ]
    return NL.join(lines)


def crossed(prev_mark, now_mark, level):
    return (prev_mark < level <= now_mark) or (prev_mark > level >= now_mark)


def main():
    if TEST_MODE:
        ok = send_telegram("Whale Futures Tracker - TES berhasil. Workflow GitHub Actions nyambung ke Telegram. Notif futures (posisi baru, TP/SL kena, dekat likuidasi) akan dikirim dari workflow ini.")
        print("Test message:", ok)
    positions, marks = fetch_positions()
    state = load_state()
    if state is None:
        save_state(positions)
        print("Run pertama: state awal disimpan (" + str(len(positions)) + " posisi). Alert mulai run berikutnya.")
        return
    prev = state.get("positions", {})
    last_liq_alert = state.get("last_liq_alert", {})
    now_ms = int(time.time() * 1000)
    messages = []
    for p in positions:
        old = prev.get(p["key"])
        prio = 0 if (PUMP_PRIORITY and p["coin"] == "PUMP") else 1
        if ALERT_NEW and old is None and p["size_usd"] >= min_for(p["coin"]):
            title = "POSISI FUTURES BARU - WHALE" + (" - GOLD" if p["coin"] == "PAXG" else (" - PRIORITAS PUMP/USDT" if prio == 0 else ""))
            messages.append((prio, pos_text(title, p)))
        if ALERT_TPSL and old is not None and p["size_usd"] >= min_for(p["coin"]):
            tp = p["tp"] if p["tp"] else old.get("tp")
            sl = p["sl"] if p["sl"] else old.get("sl")
            if tp and crossed(old["mark"], p["mark"], tp):
                messages.append((prio, pos_text("TP KENA - " + ("GOLD \u00b7 PAXG" if p["coin"] == "PAXG" else ("PUMP/USDT" if p["coin"] == "PUMP" else p["coin"])) + " (harga melewati TP sejak cek sebelumnya)", p)))
            elif sl and crossed(old["mark"], p["mark"], sl):
                messages.append((prio, pos_text("SL KENA - " + ("GOLD \u00b7 PAXG" if p["coin"] == "PAXG" else ("PUMP/USDT" if p["coin"] == "PUMP" else p["coin"])) + " (harga melewati SL sejak cek sebelumnya)", p)))
        if ALERT_LIQ and p["liq"] and p["mark"] > 0 and p["size_usd"] >= min_for(p["coin"]):
            dist = abs(p["mark"] - p["liq"]) / p["mark"] * 100
            last = last_liq_alert.get(p["key"], 0)
            if dist <= LIQ_DANGER_PCT and now_ms - last > 2 * 3600 * 1000:
                last_liq_alert[p["key"]] = now_ms
                text = NL.join([
                    "DEKAT LIKUIDASI - " + ("GOLD \u00b7 PAXG" if p["coin"] == "PAXG" else ("PRIORITAS \u00b7 PUMP/USDT" if p["coin"] == "PUMP" else p["coin"])),
                    p["side"] + " " + str(p["leverage"]) + "x - Ukuran " + usd(p["size_usd"]),
                    "Mark: $" + px(p["mark"]) + " - Liq: $" + px(p["liq"]),
                    "Jarak ke likuidasi tinggal " + format(dist, ".1f") + "%",
                ])
                messages.append((prio, text))
    if ALERT_TPSL:
        for key, old in prev.items():
            if key in {p["key"] for p in positions}:
                continue
            if old.get("size_usd", 0) < min_for(old["coin"]):
                continue
            mark_now = marks.get(old["coin"])
            if mark_now is None:
                continue
            tp = old.get("tp")
            sl = old.get("sl")
            hit = None
            if tp and ((old["side"] == "LONG" and mark_now >= tp) or (old["side"] == "SHORT" and mark_now <= tp)):
                hit = "TP"
            elif sl and ((old["side"] == "LONG" and mark_now <= sl) or (old["side"] == "SHORT" and mark_now >= sl)):
                hit = "SL"
            if hit:
                level = tp if hit == "TP" else sl
                text = NL.join([
                    "KEMUNGKINAN " + hit + " KENA - " + ("GOLD \u00b7 PAXG" if old["coin"] == "PAXG" else ("PRIORITAS \u00b7 PUMP/USDT" if old["coin"] == "PUMP" else old["coin"])),
                    "Posisi " + old["side"] + " " + str(old["leverage"]) + "x (" + usd(old["size_usd"]) + ") sudah tutup,",
                    "dan harga sekarang $" + px(mark_now) + " sudah melewati " + hit + " posisi itu di $" + px(level) + ".",
                ])
                messages.append((0 if (PUMP_PRIORITY and old["coin"] == "PUMP") else 1, text))
    messages.sort(key=lambda t: t[0])
    sent = 0
    for _prio, m in messages[:MAX_MESSAGES]:
        if send_telegram(m):
            sent += 1
    print("Posisi terpantau:", len(positions), "- notif terkirim:", sent)
    save_state(positions)
    try:
        state2 = load_state() or {}
        state2["last_liq_alert"] = last_liq_alert
        with open(STATE_FILE, "w") as f:
            json.dump(state2, f)
    except Exception:
        pass


if __name__ == "__main__":
    main()
