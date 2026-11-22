# whale_btc_report.py
# Laporan posisi whale BTC di Hyperliquid (perpetual) ke Telegram.
# Dijalankan manual oleh .github/workflows/whale-btc-report.yml
# (GitHub Actions, workflow_dispatch + tes). Tidak ada jadwal otomatis —
# jalankan kapan saja mau laporan BTC terbaru di Telegram.
# Isi: harga BTC kini, total LONG/SHORT whale, 10 posisi BTC terbesar
# (entry, mark, PnL, liq, TP/SL), plus posisi BTC baru sejak laporan
# terakhir (dibandingkan dengan snapshot di state).
# "Baru" = perbandingan dengan snapshot laporan sebelumnya yang disimpan
# di state (actions/cache) — Hyperliquid tidak memberi jam buka posisi.
# Rahasia TIDAK ditulis di file ini. Dari GitHub Secrets:
#   TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
# State di .whale-btc-state (actions/cache). Hanya library standar Python.

import html
import json
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone

HL_INFO = "https://api.hyperliquid.xyz/info"
HL_LEADERBOARD = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
STATE_DIR = ".whale-btc-state"
STATE_FILE = os.path.join(STATE_DIR, "btc-report-state.json")
NL = chr(10)
SEP = "━━━━━━━━━━━━━━"
WIB = timezone(timedelta(hours=7))

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TEST_MODE = os.environ.get("TEST_MODE", "") == "true"
MAX_POSITIONS = 10
MAX_NEW = 5

HARI = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
BULAN = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli",
         "Agustus", "September", "Oktober", "November", "Desember"]


def esc(v):
    return html.escape(str(v), quote=False)


def post_json(url, payload):
    last_err = None
    for _ in range(3):
        try:
            req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=25) as res:
                return json.loads(res.read().decode())
        except Exception as e:
            last_err = e
            time.sleep(2)
    raise last_err


def get_json(url):
    # Leaderboard ~38MB dan koneksi kadang terputus di tengah jalan
    # (IncompleteRead). Lanjutkan unduhan dengan HTTP Range sampai lengkap.
    import http.client
    data = b""
    total = None
    for _ in range(20):
        headers = {}
        if data:
            headers["Range"] = "bytes=%d-" % len(data)
            time.sleep(1)
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                if res.status == 200 and data:
                    # Server mengabaikan Range: unduh ulang dari awal.
                    data = b""
                    total = None
                if total is None:
                    cr = res.headers.get("Content-Range")
                    if cr and "/" in cr:
                        total = int(cr.rsplit("/", 1)[1])
                    elif res.headers.get("Content-Length"):
                        total = int(res.headers["Content-Length"])
                while True:
                    try:
                        chunk = res.read(1024 * 1024)
                    except http.client.IncompleteRead as e:
                        data += e.partial
                        break
                    if not chunk:
                        break
                    data += chunk
        except Exception:
            # Gagal di tengah jalan: coba lagi dengan Range (loop berikutnya).
            pass
        if total is not None and len(data) >= total:
            break
        if total is None and data:
            break
    if total is not None and len(data) < total:
        raise IOError("Unduhan tidak lengkap: %d dari %d byte" % (len(data), total))
    return json.loads(data.decode())


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


def now_wib():
    now = datetime.now(WIB)
    return (HARI[now.weekday()] + ", " + str(now.day) + " " + BULAN[now.month - 1]
            + " " + str(now.year) + ", " + now.strftime("%H:%M") + " WIB")


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
    for addr in addresses:
        try:
            state = post_json(HL_INFO, {"type": "clearinghouseState", "user": addr})
        except Exception:
            continue
        for ap in state.get("assetPositions", []):
            p = ap.get("position", {})
            size_usd = num(p.get("positionValue"))
            szi = num(p.get("szi"))
            if size_usd < 25000 or szi == 0:
                continue
            side = "SHORT" if szi < 0 else "LONG"
            lev = p.get("leverage") or {}
            positions.append({
                "key": addr + "|" + str(p.get("coin")),
                "whale": addr,
                "coin": p.get("coin"),
                "side": side,
                "entry": num(p.get("entryPx")),
                "size_usd": size_usd,
                "size_coin": abs(szi),
                "leverage": int(num(lev.get("value"), 1)),
                "lev_type": lev.get("type", "cross"),
                "mark": marks.get(p.get("coin"), num(p.get("entryPx"))),
                "liq": num(p.get("liquidationPx")) if p.get("liquidationPx") else None,
                "pnl": num(p.get("unrealizedPnl")) if p.get("unrealizedPnl") is not None else None,
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


def pos_line(p, prefix=""):
    side_emoji = "🟢" if p["side"] == "LONG" else "🔴"
    pnl_txt = ""
    if p.get("pnl") is not None:
        pnl_txt = " · PnL " + usd_signed(p["pnl"])
    liq_txt = ("liq $" + px(p["liq"])) if p.get("liq") else "liq -"
    lines = []
    lines.append(prefix + side_emoji + " <b>" + esc(p["side"]) + " " + esc(str(p["leverage"])) + "x — " + esc(usd(p["size_usd"])) + "</b>")
    lines.append("entry $" + esc(px(p["entry"])) + " · mark $" + esc(px(p["mark"])) + pnl_txt)
    lines.append(liq_txt + " · whale <code>" + esc(str(p["whale"])[:10]) + "...</code>")
    return NL.join(lines)


def build_message(btc, marks, prev_btc, first_run=False):
    long_total = sum(p["size_usd"] for p in btc if p["side"] == "LONG")
    short_total = sum(p["size_usd"] for p in btc if p["side"] == "SHORT")
    pnl_total = sum(p["pnl"] for p in btc if p.get("pnl") is not None)
    btc_mark = marks.get("BTC")
    top = sorted(btc, key=lambda p: p["size_usd"], reverse=True)

    lines = []
    lines.append("₿ <b>Whale BTC — " + esc(now_wib()) + "</b>")
    lines.append(SEP)
    if btc_mark:
        lines.append("BTC mark: <b>$" + esc(px(btc_mark)) + "</b>")
    lines.append("Posisi terbuka: <b>" + str(len(btc)) + "</b> · LONG <b>" + esc(usd(long_total)) + "</b> (" + str(sum(1 for p in btc if p["side"] == "LONG")) + " posisi) / SHORT <b>" + esc(usd(short_total)) + "</b> (" + str(sum(1 for p in btc if p["side"] == "SHORT")) + " posisi)")
    lines.append("Total PnL BTC: <b>" + esc(usd_signed(pnl_total)) + "</b>")
    lines.append(SEP)
    lines.append("🏆 <b>Posisi BTC terbesar</b>")
    for i, p in enumerate(top[:MAX_POSITIONS], 1):
        lines.append(pos_line(p, str(i) + ". "))
    if not first_run and prev_btc is not None:
        prev_keys = {p["key"] for p in prev_btc}
        new = [p for p in btc if p["key"] not in prev_keys]
        new.sort(key=lambda p: p["size_usd"], reverse=True)
        lines.append(SEP)
        if new:
            lines.append("🆕 <b>Posisi BTC baru sejak laporan terakhir — " + str(len(new)) + " posisi</b>")
            for i, p in enumerate(new[:MAX_NEW], 1):
                lines.append(pos_line(p, str(i) + ". "))
            if len(new) > MAX_NEW:
                lines.append("<i>...dan " + str(len(new) - MAX_NEW) + " posisi baru lainnya.</i>")
        else:
            lines.append("🆕 <b>Posisi BTC baru</b>" + NL + "Tidak ada posisi BTC baru sejak laporan terakhir.")
    if first_run:
        lines.append(SEP)
        lines.append("<i>Laporan pertama — snapshot disimpan sebagai pembanding laporan berikutnya.</i>")
    lines.append(SEP)
    lines.append("<i>Hyperliquid · Futures Perp</i>")
    return NL.join(lines)[:4000]


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


def main():
    if TEST_MODE:
        test_text = NL.join([
            "₿ <b>Whale BTC — TES berhasil</b>",
            SEP,
            "Workflow laporan BTC nyambung ke Telegram.",
            "Jalankan workflow ini kapan saja untuk laporan posisi whale BTC terbaru.",
            SEP,
            "<i>Contoh format laporan di bawah ini:</i>",
            "",
            build_message([
                {"key": "0x000|BTC", "whale": "0x0000000000test", "coin": "BTC", "side": "LONG",
                 "entry": 59405.8, "size_usd": 58956267, "size_coin": 979.1, "leverage": 39,
                 "lev_type": "cross", "mark": 60217, "liq": 31118.6, "pnl": 791269},
                {"key": "0x001|BTC", "whale": "0x0000000001test", "coin": "BTC", "side": "SHORT",
                 "entry": 76773.6, "size_usd": 43529395, "size_coin": 722.9, "leverage": 20,
                 "lev_type": "cross", "mark": 60217, "liq": 81350.2, "pnl": 11971143},
            ], {"BTC": 60217}, [], first_run=True),
        ])
        ok = send_telegram(test_text[:4096])
        print("Test message:", ok)
        return
    positions, marks = fetch_positions()
    btc = [p for p in positions if p["coin"] == "BTC"]
    state = load_state()
    first_run = state is None
    prev_btc = []
    if state is not None:
        prev_btc = [p for p in state.get("positions", {}).values() if p.get("coin") == "BTC"]
    text = build_message(btc, marks, prev_btc, first_run=first_run)
    ok = send_telegram(text)
    print("Posisi BTC:", len(btc), "- pesan terkirim:", ok)
    save_state(positions)


if __name__ == "__main__":
    main()
