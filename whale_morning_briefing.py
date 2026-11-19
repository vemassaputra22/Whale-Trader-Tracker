# whale_morning_briefing.py
# Ringkasan PAGI posisi FUTURES whale Hyperliquid -> Telegram.
# Dijalankan oleh .github/workflows/whale-morning-briefing.yml
# Jadwal workflow: cron "0 0 * * *" = 00:00 UTC = 07:00 WIB tiap hari.
# BEDA dari whale_futures_notifier.py (alert tiap 5 menit):
#   - File ini kirim SATU ringkasan snapshot tiap pagi, bukan alert kejadian.
#   - Isi: total posisi besar, Top 10 terbesar, Gold (PAXG), PUMP/USDT,
#     dan posisi dekat likuidasi (<=12% dari Mark).
# Format: MIRIP KARTU TRACKER (teks HTML Telegram).
# Ambang default sesuai pilihan user 2026-11-18:
#   Semua koin >= $250.000, PUMP >= $100.000.
# Rahasia TIDAK ditulis di file ini. Dari GitHub Secrets:
#   TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
# Variables opsional: MIN_SIZE_USD, PUMP_MIN_SIZE_USD, LIQ_DANGER_PCT
# Hanya library standar Python.

import html
import json
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone

HL_INFO = "https://api.hyperliquid.xyz/info"
HL_LEADERBOARD = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
NL = chr(10)
SEP = "━━━━━━━━━━━━━━"
WIB = timezone(timedelta(hours=7))

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
MIN_SIZE_USD = float(os.environ.get("MIN_SIZE_USD", "250000"))
PUMP_MIN_SIZE_USD = float(os.environ.get("PUMP_MIN_SIZE_USD", "100000"))
LIQ_DANGER_PCT = float(os.environ.get("LIQ_DANGER_PCT", "12"))
TEST_MODE = os.environ.get("TEST_MODE", "") == "true"
MAX_TOP = int(os.environ.get("MAX_TOP", "10"))
MAX_PER_SECTION = int(os.environ.get("MAX_PER_SECTION", "5"))


def min_for(coin):
    return PUMP_MIN_SIZE_USD if coin == "PUMP" else MIN_SIZE_USD


def esc(v):
    return html.escape(str(v), quote=False)


def post_json(url, payload):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
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
    return ("+" + usd(v)) if v > 0 else usd(v)


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
    return ("+" if d >= 0 else "") + format(d, ".1f") + "%"


def dist_str(target, ref):
    if not ref:
        return ""
    return format(abs(target - ref) / ref * 100, ".1f") + "%"


def coin_label(coin):
    if coin == "PAXG":
        return "GOLD · PAXG"
    if coin == "PUMP":
        return "PRIORITAS · PUMP/USDT"
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
    return " — ".join(n + " $" + px(v) for n, v in pts)


def compact_card(p, rank=None):
    """Satu posisi dalam format ringkas mirip kartu tracker."""
    side = p.get("side", "")
    emoji = "🟢" if side == "LONG" else "🔴"
    mark = num(p.get("mark"))
    entry = num(p.get("entry"))
    head = ""
    if rank is not None:
        head = "<b>" + str(rank) + ".</b> "
    lines = []
    lines.append(head + emoji + " <b>" + esc(coin_label(p.get("coin", "")))
                 + " " + esc(side) + " " + esc(p.get("leverage", "")) + "x</b>"
                 + (" (" + esc(p["lev_type"]) + ")" if p.get("lev_type") else ""))
    lines.append("💰 <b>" + esc(usd(num(p.get("size_usd")))) + "</b>"
                 + " • Entry $" + esc(px(entry)) + " • Mark $" + esc(px(mark)))
    if p.get("pnl") is not None:
        pnl_f = num(p.get("pnl"))
        lines.append(("📈" if pnl_f >= 0 else "📉") + " PnL: <b>"
                     + esc(usd_signed(pnl_f)) + "</b>")
    bar = price_bar(p)
    if bar:
        lines.append("<code>" + esc(bar) + "</code>")
    tp_txt = ("$" + px(num(p["tp"])) + " (" + pct_str(num(p["tp"]), mark) + ")"
              if p.get("tp") else "<i>Belum pasang TP</i>")
    sl_txt = ("$" + px(num(p["sl"])) + " (" + pct_str(num(p["sl"]), mark) + ")"
              if p.get("sl") else "<i>Belum pasang SL</i>")
    lines.append("🎯 TP: " + tp_txt + "  •  🛑 SL: " + sl_txt)
    if p.get("liq"):
        lines.append("💧 Liq: $" + esc(px(num(p["liq"]))) + " • jarak "
                     + esc(dist_str(num(p["liq"]), mark)) + " dari Mark")
    else:
        lines.append("💧 Liq: <i>sangat jauh / cross</i>")
    if p.get("whale"):
        lines.append("Whale: <code>" + esc(str(p["whale"])[:10]) + "...</code>")
    return NL.join(lines)


def send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID belum di-set.")
        return False
    url = "https://api.telegram.org/bot" + BOT_TOKEN + "/sendMessage"
    try:
        data = post_json(url, {"chat_id": CHAT_ID, "text": text[:4096],
                                "parse_mode": "HTML",
                                "disable_web_page_preview": True})
        return bool(data.get("ok"))
    except Exception as e:
        print("Gagal kirim Telegram:", e)
        return False


def send_long(text):
    """Kirim teks panjang dengan memecah per blok (maks 4000 char/pesan)."""
    if len(text) <= 4000:
        return send_telegram(text)
    blocks = text.split(NL + SEP + NL)
    buf = ""
    ok_all = True
    for i, b in enumerate(blocks):
        piece = b if i == 0 else SEP + NL + b
        if len(buf) + len(piece) + 1 > 4000 and buf:
            ok_all = send_telegram(buf) and ok_all
            time.sleep(0.6)
            buf = piece
        else:
            buf = (buf + NL + piece) if buf else piece
    if buf:
        ok_all = send_telegram(buf) and ok_all
    return ok_all



def fetch_marks():
    data = post_json(HL_INFO, {"type": "metaAndAssetCtxs"})
    marks = {}
    for i, u in enumerate(data[0]["universe"]):
        if i < len(data[1]):
            marks[u["name"]] = num(data[1][i].get("markPx"))
    return marks


def fetch_positions():
    lb = get_json(HL_LEADERBOARD)
    rows = sorted(lb.get("leaderboardRows", []),
                  key=lambda r: num(r.get("accountValue")), reverse=True)
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
                "whale": addr, "coin": p.get("coin"), "side": side,
                "entry": entry, "size_usd": size_usd,
                "size_coin": abs(szi),
                "leverage": int(num(lev.get("value"), 1)),
                "lev_type": lev.get("type", "cross"),
                "mark": marks.get(p.get("coin"), entry),
                "liq": num(liq_raw) if liq_raw else None,
                "tp": tp, "sl": sl,
                "pnl": num(pnl_raw) if pnl_raw is not None else None,
            })
    return positions, marks


HARI_ID = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
BULAN_ID = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun",
            "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]


def build_briefing(positions):
    now_wib = datetime.now(WIB)
    date_txt = (HARI_ID[now_wib.weekday()] + ", " + str(now_wib.day) + " "
                + BULAN_ID[now_wib.month - 1] + " " + str(now_wib.year)
                + " • 07:00 WIB")
    big = [p for p in positions if p["size_usd"] >= min_for(p["coin"])]
    big.sort(key=lambda x: x["size_usd"], reverse=True)
    long_v = sum(p["size_usd"] for p in big if p["side"] == "LONG")
    short_v = sum(p["size_usd"] for p in big if p["side"] == "SHORT")
    total_v = long_v + short_v
    parts = []
    head = NL.join([
        "🌅 <b>RINGKASAN PAGI — WHALE FUTURES</b>",
        SEP,
        esc(date_txt),
        "Posisi besar terpantau: <b>" + str(len(big)) + "</b>"
        + " (≥" + usd(MIN_SIZE_USD) + ", PUMP ≥" + usd(PUMP_MIN_SIZE_USD) + ")",
        "Total: <b>" + esc(usd(total_v)) + "</b>"
        + " • 🟢 LONG " + esc(usd(long_v)) + " • 🔴 SHORT " + esc(usd(short_v)),
        "<i>Snapshot posisi terbuka saat ini di Hyperliquid.</i>",
    ])
    parts.append(head)
    if not big:
        parts.append("Tidak ada posisi ≥ ambang saat ini." + NL
                     + "<i>Ini dilaporkan apa adanya — tidak dikarang.</i>")
        return NL.join([parts[0], SEP, parts[1], SEP,
                        "<i>Hyperliquid · Futures Perp · Ringkasan Pagi</i>"])
    # Top terbesar
    top_lines = ["🏆 <b>TOP " + str(min(MAX_TOP, len(big)))
                 + " POSISI TERBESAR</b>", SEP]
    for i, p in enumerate(big[:MAX_TOP], 1):
        top_lines.append(compact_card(p, rank=i))
        top_lines.append("")
    parts.append(NL.join(top_lines).rstrip())
    # Gold
    gold = [p for p in big if p["coin"] == "PAXG"]
    g_lines = ["🥇 <b>GOLD · PAXG</b> — " + str(len(gold)) + " posisi besar", SEP]
    if gold:
        for p in gold[:MAX_PER_SECTION]:
            g_lines.append(compact_card(p))
            g_lines.append("")
    else:
        g_lines.append("Tidak ada posisi PAXG ≥ ambang pagi ini.")
    parts.append(NL.join(g_lines).rstrip())
    # PUMP
    pump = [p for p in big if p["coin"] == "PUMP"]
    p_lines = ["🎯 <b>PRIORITAS · PUMP/USDT</b> — " + str(len(pump))
               + " posisi besar", SEP]
    if pump:
        for p in pump[:MAX_PER_SECTION]:
            p_lines.append(compact_card(p))
            p_lines.append("")
    else:
        p_lines.append("Tidak ada posisi PUMP ≥" + usd(PUMP_MIN_SIZE_USD)
                       + " pagi ini.")
    parts.append(NL.join(p_lines).rstrip())
    # Dekat likuidasi
    danger = []
    for p in big:
        if p.get("liq") and p.get("mark"):
            d = abs(p["mark"] - p["liq"]) / p["mark"] * 100
            if d <= LIQ_DANGER_PCT:
                danger.append((d, p))
    danger.sort(key=lambda x: x[0])
    d_lines = ["⚠️ <b>DEKAT LIKUIDASI (≤" + format(LIQ_DANGER_PCT, ".0f")
               + "%)</b> — " + str(len(danger)) + " posisi", SEP]
    if danger:
        for d, p in danger[:MAX_PER_SECTION]:
            d_lines.append(compact_card(p))
            d_lines.append("Jarak likuidasi: <b>" + format(d, ".1f") + "%</b>")
            d_lines.append("")
    else:
        d_lines.append("Tidak ada posisi besar dalam zona bahaya pagi ini.")
    parts.append(NL.join(d_lines).rstrip())
    parts.append("<i>Hyperliquid · Futures Perp · Ringkasan Pagi 07:00 WIB</i>")
    return (NL + SEP + NL).join(parts)



def main():
    if TEST_MODE:
        demo = {
            "coin": "PUMP", "side": "SHORT", "leverage": 10,
            "lev_type": "cross", "size_usd": 584125,
            "size_coin": 216102650, "entry": 0.004477, "mark": 0.002699,
            "liq": 0.050629, "tp": None, "sl": None, "pnl": 125000,
            "whale": "0x0000000000test",
        }
        test_text = NL.join([
            "🌅 <b>Ringkasan Pagi — TES berhasil</b>",
            SEP,
            "Workflow whale-morning-briefing nyambung ke Telegram.",
            "Ringkasan akan dikirim tiap hari jam 07:00 WIB.",
            SEP, "<i>Contoh format posisi:</i>", "", compact_card(demo),
        ])
        print("Test message:", send_telegram(test_text[:4096]))
        return
    positions, _marks = fetch_positions()
    print("Posisi terpantau (>= $25K):", len(positions))
    briefing = build_briefing(positions)
    print("Panjang ringkasan:", len(briefing), "karakter")
    ok = send_long(briefing)
    print("Ringkasan pagi terkirim:", ok)


if __name__ == "__main__":
    main()
