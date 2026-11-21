# whale_futures_notifier.py
# Notifier Telegram untuk posisi FUTURES whale di Hyperliquid (perpetual).
# Dijalankan oleh .github/workflows/whale-futures-telegram.yml (GitHub Actions).
# Format pesan: MIRIP KARTU TRACKER (teks HTML Telegram) — header emoji,
# garis pemisah, bar harga Liq-SL-Entry-Mark-TP, TP/SL/Liq + jarak %.
# Keterangan bar ditampilkan di bawahnya (Mark = harga kini, Entry = harga
# buka, Liq = harga likuidasi) supaya tidak disangka harga kini semua.
# Jarak >1000% ditulis "sangat jauh", bukan angka persen raksasa.
# ATURAN NOTIF BARU: alert "posisi baru" HANYA untuk posisi yang benar-benar
# baru dibuka — dicek lewat fills Hyperliquid (userFillsByTime): harus ada
# fill pembuka (Open Long / Open Short) untuk koin itu dalam
# RECENT_OPEN_HOURS jam terakhir. Posisi lama yang baru terdeteksi (misal
# sempat hilang dari pantauan lalu muncul lagi) TIDAK diberi notif.
# Registry "seen" di state mengingat semua posisi yang pernah terlihat,
# supaya posisi lama tidak pernah di-alert sebagai baru lagi.
# JAM BUKA: notif posisi baru menampilkan jam whale membuka posisi itu
# (dari waktu fill pembuka, WIB) — "Dibuka: HH:MM WIB, tanggal".
# Crypto = SEMUA koin Hyperliquid. Gold = PAXG, label GOLD · PAXG.
# Fokus khusus: PUMP/USDT — ambang lebih rendah, label PRIORITAS, dikirim dulu.
# Rahasia TIDAK ditulis di file ini. Dari GitHub Secrets:
#   TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
# State di .whale-state (actions/cache). Hanya library standar Python.

import html
import json
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone

WIB = timezone(timedelta(hours=7))
BULAN_SINGKAT = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]


def fmt_wib(ms):
    """Ubah epoch ms menjadi 'HH:MM WIB, DD Mon YYYY'."""
    dt = datetime.fromtimestamp(ms / 1000, tz=WIB)
    return dt.strftime("%H:%M") + " WIB, " + str(dt.day) + " " + BULAN_SINGKAT[dt.month - 1] + " " + str(dt.year)

HL_INFO = "https://api.hyperliquid.xyz/info"
HL_LEADERBOARD = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
STATE_DIR = ".whale-state"
STATE_FILE = os.path.join(STATE_DIR, "state.json")
NL = chr(10)
SEP = "━━━━━━━━━━━━━━"

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
MIN_SIZE_USD = float(os.environ.get("MIN_SIZE_USD", "500000"))
LIQ_DANGER_PCT = float(os.environ.get("LIQ_DANGER_PCT", "12"))
ALERT_NEW = os.environ.get("ALERT_NEW_POSITION", "1") == "1"
ALERT_TPSL = os.environ.get("ALERT_TP_SL_HIT", "1") == "1"
ALERT_LIQ = os.environ.get("ALERT_LIQ_DANGER", "1") == "1"
PUMP_PRIORITY = os.environ.get("PUMP_PRIORITY", "1") == "1"
PUMP_MIN_SIZE_USD = float(os.environ.get("PUMP_MIN_SIZE_USD", "100000"))
RECENT_OPEN_HOURS = float(os.environ.get("RECENT_OPEN_HOURS", "24"))
TEST_MODE = os.environ.get("TEST_MODE", "") == "true"
MAX_MESSAGES = 8


def min_for(coin):
    return PUMP_MIN_SIZE_USD if (PUMP_PRIORITY and coin == "PUMP") else MIN_SIZE_USD


def esc(v):
    return html.escape(str(v), quote=False)


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
    sign = "-" if v < 0 else ""
    if a >= 1e9:
        return sign + "$" + format(a / 1e9, ".2f") + "B"
    if a >= 1e6:
        return sign + "$" + format(a / 1e6, ".2f") + "M"
    if a >= 1e3:
        return sign + "$" + format(a / 1e3, ".1f") + "K"
    return sign + "$" + format(a, ".0f")


def usd_signed(v):
    if v > 0:
        return "+" + usd(v)
    return usd(v)


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


def pct_str(target, ref):
    if not ref:
        return ""
    d = (target - ref) / ref * 100
    if abs(d) > 1000:
        return (">1000%" if d >= 0 else "<-1000%")
    return ("+" if d >= 0 else "") + format(d, ".1f") + "%"


def dist_str(target, ref):
    if not ref:
        return ""
    d = abs(target - ref) / ref * 100
    if d > 1000:
        return "sangat jauh"
    return format(d, ".1f") + "%"


def coin_label(coin):
    if coin == "PAXG":
        return "GOLD · PAXG"
    if coin == "PUMP":
        return "PRIORITAS · PUMP/USDT"
    return str(coin)


def coin_short(coin):
    if coin == "PAXG":
        return "PAXG"
    return str(coin)


def price_bar(p):
    pts = []
    if p.get("liq"):
        pts.append(("Liq", p["liq"]))
    if p.get("sl"):
        pts.append(("SL", p["sl"]))
    if p.get("entry"):
        pts.append(("Entry", p["entry"]))
    if p.get("mark"):
        pts.append(("Mark", p["mark"]))
    if p.get("tp"):
        pts.append(("TP", p["tp"]))
    pts.sort(key=lambda x: x[1])
    parts = [name + " $" + px(val) for name, val in pts]
    return " — ".join(parts)


def card_text(kind, p, extra_note=""):
    # kind: new | tp_hit | sl_hit | liq_danger | closed_tp | closed_sl
    headers = {
        "new": "🐳 <b>POSISI FUTURES BARU — WHALE</b>",
        "tp_hit": "🎯 <b>TP KENA — WHALE</b>",
        "sl_hit": "🛑 <b>SL KENA — WHALE</b>",
        "liq_danger": "⚠️ <b>DEKAT LIKUIDASI — WHALE</b>",
        "closed_tp": "🎯 <b>KEMUNGKINAN TP KENA — POSISI TUTUP</b>",
        "closed_sl": "🛑 <b>KEMUNGKINAN SL KENA — POSISI TUTUP</b>",
        "test": "🐳 <b>Whale Futures Tracker — TES</b>",
    }
    header = headers.get(kind, headers["new"])
    label = coin_label(p.get("coin", ""))
    side = p.get("side", "")
    side_emoji = "🟢" if side == "LONG" else "🔴"
    mark = num(p.get("mark"))
    entry = num(p.get("entry"))
    lines = []
    lines.append(header)
    lines.append(SEP)
    title_line = side_emoji + " <b>" + esc(label) + " " + esc(side) + " " + esc(p.get("leverage", "")) + "x</b>"
    if p.get("lev_type"):
        title_line += " (" + esc(p["lev_type"]) + ")"
    lines.append(title_line)
    lines.append(SEP)
    if kind == "new" and p.get("open_ms"):
        lines.append("🕐 Dibuka: <b>" + esc(fmt_wib(num(p["open_ms"]))) + "</b>")
    lines.append("💰 Ukuran: <b>" + esc(usd(num(p.get("size_usd")))) + "</b> — " + esc(format(num(p.get("size_coin")), ".4f")) + " " + esc(coin_short(p.get("coin", ""))))
    lines.append("📍 Entry: <b>$" + esc(px(entry)) + "</b>  •  Mark (harga kini): <b>$" + esc(px(mark)) + "</b>")
    pnl = p.get("pnl")
    if pnl is not None:
        try:
            pnl_f = float(pnl)
            roe_txt = ""
            margin = num(p.get("size_usd")) / max(int(num(p.get("leverage"), 1)), 1) if p.get("size_usd") else 0
            if margin:
                roe = pnl_f / margin * 100
                roe_txt = " (" + ("+" if roe >= 0 else "") + format(roe, ".1f") + "%)"
            pnl_emoji = "📈" if pnl_f >= 0 else "📉"
            lines.append(pnl_emoji + " PnL: <b>" + esc(usd_signed(pnl_f)) + esc(roe_txt) + "</b>")
        except (TypeError, ValueError):
            pass
    bar = price_bar(p)
    if bar:
        lines.append(SEP)
        lines.append("<code>" + esc(bar) + "</code>")
        lines.append("<i>Mark = harga kini · Entry = harga buka · Liq = harga likuidasi</i>")
    lines.append(SEP)
    # TP box
    if p.get("tp"):
        lines.append("🎯 TP: <b>$" + esc(px(num(p["tp"]))) + "</b>  •  " + esc(pct_str(num(p["tp"]), mark)) + " dari Mark  •  " + esc(pct_str(num(p["tp"]), entry)) + " dari Entry")
    else:
        lines.append("🎯 TP: <i>Belum pasang TP</i>")
    if p.get("sl"):
        lines.append("🛑 SL: <b>$" + esc(px(num(p["sl"]))) + "</b>  •  " + esc(pct_str(num(p["sl"]), mark)) + " dari Mark  •  " + esc(pct_str(num(p["sl"]), entry)) + " dari Entry")
    else:
        lines.append("🛑 SL: <i>Belum pasang SL</i>")
    if p.get("liq"):
        lines.append("💧 Liq: <b>$" + esc(px(num(p["liq"]))) + "</b>  •  jarak " + esc(dist_str(num(p["liq"]), mark)) + " dari Mark")
    else:
        lines.append("💧 Liq: <i>sangat jauh / cross</i>")
    if extra_note:
        lines.append(SEP)
        lines.append(esc(extra_note))
    lines.append(SEP)
    if p.get("whale"):
        lines.append("Whale: <code>" + esc(str(p["whale"])[:10]) + "...</code>")
    lines.append("<i>Hyperliquid · Futures Perp</i>")
    text = NL.join(lines)
    return text[:4000]


def send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID belum di-set di GitHub Secrets.")
        return False
    url = "https://api.telegram.org/bot" + BOT_TOKEN + "/sendMessage"
    try:
        data = post_json(url, {"chat_id": CHAT_ID, "text": text[:4096], "parse_mode": "HTML", "disable_web_page_preview": True})
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
            pnl_raw = p.get("unrealizedPnl")
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
                "pnl": num(pnl_raw) if pnl_raw is not None else None,
            })
    return positions, marks


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return None


def save_state(positions, seen=None):
    os.makedirs(STATE_DIR, exist_ok=True)
    state = {"positions": {p["key"]: p for p in positions}, "saved_at": int(time.time() * 1000), "seen": seen or {}}
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def crossed(prev_mark, now_mark, level):
    return (prev_mark < level <= now_mark) or (prev_mark > level >= now_mark)


def recent_open_time(addr, coin, side, hours):
    """Kembalikan waktu (epoch ms) fill pembuka TERBARU untuk `coin` dalam
    `hours` jam terakhir, atau None bila tidak ada / pengecekan gagal.
    Dipakai untuk membuktikan posisi benar-benar baru dibuka DAN untuk
    menampilkan jam bukanya di notif."""
    try:
        now_ms = int(time.time() * 1000)
        start_ms = now_ms - int(hours * 3600 * 1000)
        fills = post_json(HL_INFO, {"type": "userFillsByTime", "user": addr, "startTime": start_ms, "endTime": now_ms})
    except Exception as e:
        print("Cek fills gagal untuk", addr[:10], coin, ":", e)
        return None
    want = "Open Long" if side == "LONG" else "Open Short"
    best = None
    for f in fills or []:
        if f.get("coin") == coin and f.get("dir") == want:
            t = num(f.get("time"))
            if t and (best is None or t > best):
                best = t
    return best


def should_alert_new(p, prev, seen, hours):
    """Alert 'posisi baru' hanya bila posisi ini belum pernah terlihat di
    run sebelumnya maupun di registry seen, DAN ada fill pembuka dalam
    `hours` jam terakhir. Kembalikan (True/False, jam buka ms / None).
    Posisi lama yang baru terdeteksi -> (False, None)."""
    if p["key"] in prev or p["key"] in seen:
        return False, None
    t = recent_open_time(p["whale"], p["coin"], p["side"], hours)
    return (t is not None), t


def main():
    if TEST_MODE:
        demo = {
            "coin": "PUMP", "side": "SHORT", "leverage": 10, "lev_type": "cross",
            "size_usd": 584125, "size_coin": 216102650, "entry": 0.004477,
            "mark": 0.002699, "liq": 0.050629, "tp": None, "sl": None,
            "pnl": 125000, "whale": "0x0000000000test",
            "open_ms": int(time.time() * 1000) - 35 * 60 * 1000,
        }
        test_text = NL.join([
            "🐳 <b>Whale Futures Tracker — TES berhasil</b>",
            SEP,
            "Workflow GitHub Actions nyambung ke Telegram.",
            "Notif futures (posisi baru, TP/SL kena, dekat likuidasi) akan dikirim dari workflow ini.",
            SEP,
            "<i>Contoh format kartu di bawah ini:</i>",
            "",
            card_text("new", demo),
        ])
        ok = send_telegram(test_text[:4096])
        print("Test message:", ok)
    positions, marks = fetch_positions()
    state = load_state()
    now_ms = int(time.time() * 1000)
    if state is None:
        save_state(positions, {p["key"]: now_ms for p in positions})
        print("Run pertama: state awal disimpan (" + str(len(positions)) + " posisi). Alert mulai run berikutnya.")
        return
    prev = state.get("positions", {})
    seen = state.get("seen", {}) or {}
    for k in prev:
        seen.setdefault(k, now_ms)
    last_liq_alert = state.get("last_liq_alert", {})
    messages = []
    for p in positions:
        old = prev.get(p["key"])
        prio = 0 if (PUMP_PRIORITY and p["coin"] == "PUMP") else 1
        if ALERT_NEW and old is None and p["size_usd"] >= min_for(p["coin"]):
            ok, open_ms = should_alert_new(p, prev, seen, RECENT_OPEN_HOURS)
            if ok:
                pp = dict(p)
                pp["open_ms"] = open_ms
                messages.append((prio, card_text("new", pp)))
            else:
                print("Lewati alert posisi lama:", p["coin"], p["side"], p["whale"][:10])
            seen.setdefault(p["key"], now_ms)
        if ALERT_TPSL and old is not None and p["size_usd"] >= min_for(p["coin"]):
            tp = p["tp"] if p["tp"] else old.get("tp")
            sl = p["sl"] if p["sl"] else old.get("sl")
            pp = dict(p)
            pp["tp"] = tp
            pp["sl"] = sl
            if tp and crossed(old["mark"], p["mark"], tp):
                messages.append((prio, card_text("tp_hit", pp, "Harga melewati TP sejak cek sebelumnya.")))
            elif sl and crossed(old["mark"], p["mark"], sl):
                messages.append((prio, card_text("sl_hit", pp, "Harga melewati SL sejak cek sebelumnya.")))
        if ALERT_LIQ and p["liq"] and p["mark"] > 0 and p["size_usd"] >= min_for(p["coin"]):
            dist = abs(p["mark"] - p["liq"]) / p["mark"] * 100
            last = last_liq_alert.get(p["key"], 0)
            if dist <= LIQ_DANGER_PCT and now_ms - last > 2 * 3600 * 1000:
                last_liq_alert[p["key"]] = now_ms
                note = "Jarak ke likuidasi tinggal " + format(dist, ".1f") + "% — hati-hati."
                messages.append((prio, card_text("liq_danger", p, note)))
    if ALERT_TPSL:
        current_keys = {p["key"] for p in positions}
        for key, old in prev.items():
            if key in current_keys:
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
                closed = dict(old)
                closed["mark"] = mark_now
                note = "Posisi sudah tutup. Harga sekarang $" + px(mark_now) + " sudah melewati " + hit + " di $" + px(level) + "."
                kind = "closed_tp" if hit == "TP" else "closed_sl"
                messages.append((0 if (PUMP_PRIORITY and old["coin"] == "PUMP") else 1, card_text(kind, closed, note)))
    messages.sort(key=lambda t: t[0])
    sent = 0
    for _prio, m in messages[:MAX_MESSAGES]:
        if send_telegram(m):
            sent += 1
    print("Posisi terpantau:", len(positions), "- notif terkirim:", sent)
    save_state(positions, seen)
    try:
        state2 = load_state() or {}
        state2["last_liq_alert"] = last_liq_alert
        with open(STATE_FILE, "w") as f:
            json.dump(state2, f)
    except Exception:
        pass


if __name__ == "__main__":
    main()
