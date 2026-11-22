# whale_signal_telegram.py
# Sinyal whale ke Telegram — ringkasan sinyal TIAP JAM + alert LANGSUNG
# saat ada sinyal KUAT baru.
# Dijalankan oleh .github/workflows/whale-signal-telegram.yml (GitHub Actions).
# Sinyal dihitung dari posisi FUTURES whale di Hyperliquid (perpetual):
# per koin, total LONG vs SHORT, jumlah whale tiap sisi, dominansi %.
# Kekuatan: KUAT bila dominansi >=60% DAN total >= $1 jt DAN >=2 whale di
# sisi dominan; SEDANG bila dominansi >=55% dan total >= $500 rb; sisanya
# LEMAH. Komponen (dominansi %, jumlah whale, total USD) selalu ditampilkan
# supaya user bisa menilai sendiri — sinyal BUKAN jaminan arah harga.
# TIDAK ADA TP/SL karangan di file ini. Yang ditampilkan hanya data whale
# asli: entry rata-rata whale (tertimbang ukuran) dan harga likuidasi whale
# terdekat di sisi berlawanan — keduanya diberi label yang jelas.
# Rahasia TIDAK ditulis di file ini. Dari GitHub Secrets:
#   TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
# State di .whale-signal-state (actions/cache).
# Hanya library standar Python.

import html
import http.client
import json
import os
import subprocess
import time
import urllib.request
from datetime import datetime, timedelta, timezone

WIB = timezone(timedelta(hours=7))
BULAN_SINGKAT = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]

HL_INFO = "https://api.hyperliquid.xyz/info"
HL_LEADERBOARD = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
STATE_DIR = ".whale-signal-state"
STATE_FILE = os.path.join(STATE_DIR, "state.json")
NL = chr(10)
SEP = "━━━━━━━━━━━━━━"

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
SIGNAL_TOP_N = int(os.environ.get("SIGNAL_TOP_N", "5") or 5)
TEST_MODE = os.environ.get("TEST_MODE", "") == "true"
MAX_STRONG_ALERTS = 5
MIN_SIGNAL_TOTAL_USD = 100000
MIN_POS_SIZE_USD = 25000


def esc(v):
    return html.escape(str(v), quote=False)


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


def dist_str(target, ref):
    if not ref:
        return ""
    d = abs(target - ref) / ref * 100
    if d > 1000:
        return "sangat jauh"
    return format(d, ".1f") + "%"


def post_json(url, payload, timeout=25, retries=3):
    """POST JSON dengan retry: koneksi ke Hyperliquid kadang putus di tengah
    jalan (IncompleteRead / remote closed) atau kena rate limit (429).
    Bila urllib tetap gagal, coba curl (terbukti jauh lebih stabil di
    runtime ini untuk unduhan besar maupun POST kecil)."""
    last_exc = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as res:
                return json.loads(res.read().decode())
        except Exception as e:
            last_exc = e
            code = getattr(e, "code", None)
            if attempt < retries - 1:
                time.sleep(3 if code == 429 else 1)
    try:
        out = subprocess.run(
            ["curl", "-s", "-m", "30", "-X", "POST", "-H", "Content-Type: application/json",
             "-d", json.dumps(payload), url],
            capture_output=True, timeout=40)
        if out.returncode == 0 and out.stdout:
            return json.loads(out.stdout.decode())
    except Exception as e:
        last_exc = e
    raise last_exc


def get_json(url):
    # Leaderboard ~38MB dan koneksi kadang terputus di tengah jalan
    # (IncompleteRead). Lanjutkan unduhan dengan HTTP Range sampai lengkap.
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
        raise ValueError("Unduhan leaderboard tidak lengkap: %d dari %d byte" % (len(data), total))
    if not data:
        raise ValueError("Unduhan leaderboard kosong")
    return json.loads(data.decode())


def fetch_marks():
    data = post_json(HL_INFO, {"type": "metaAndAssetCtxs"})
    if not isinstance(data, list) or len(data) < 2:
        raise ValueError("metaAndAssetCtxs mengembalikan data tak terduga")
    universe = data[0]["universe"]
    ctxs = data[1]
    marks = {}
    for i, u in enumerate(universe):
        if i < len(ctxs):
            marks[u["name"]] = num(ctxs[i].get("markPx"))
    return marks


def fetch_positions():
    """Posisi futures whale: top-100 leaderboard by accountValue, lalu
    clearinghouseState per whale. Berhenti setelah 20 whale berposisi
    (cukup untuk sinyal) supaya run tiap 15 menit tetap ringan."""
    lb = get_json(HL_LEADERBOARD)
    rows = sorted(lb.get("leaderboardRows", []), key=lambda r: num(r.get("accountValue")), reverse=True)
    addresses = [r["ethAddress"].lower() for r in rows[:100]]
    marks = fetch_marks()
    positions = []
    whales_with_pos = 0
    failed = 0
    for addr in addresses:
        if whales_with_pos >= 20:
            break
        time.sleep(0.3)  # jeda kecil antar whale supaya tidak kena rate limit
        try:
            state = post_json(HL_INFO, {"type": "clearinghouseState", "user": addr})
        except Exception as e:
            failed += 1
            print("Gagal fetch whale", addr[:10], ":", e)
            continue
        if not isinstance(state, dict):
            continue  # API mengembalikan null/kosong: whale tanpa posisi
        aps = state.get("assetPositions", [])
        if aps:
            whales_with_pos += 1
        for ap in aps:
            p = ap.get("position", {})
            size_usd = num(p.get("positionValue"))
            szi = num(p.get("szi"))
            if size_usd < MIN_POS_SIZE_USD or szi == 0:
                continue
            side = "SHORT" if szi < 0 else "LONG"
            lev = p.get("leverage") or {}
            liq_raw = p.get("liquidationPx")
            positions.append({
                "whale": addr,
                "coin": p.get("coin"),
                "side": side,
                "entry": num(p.get("entryPx")),
                "size_usd": size_usd,
                "leverage": int(num(lev.get("value"), 1)),
                "mark": marks.get(p.get("coin"), num(p.get("entryPx"))),
                "liq": num(liq_raw) if liq_raw else None,
            })
    print("Whale berposisi:", whales_with_pos, "- gagal fetch:", failed, "- posisi:", len(positions))
    return positions


def build_signals(positions):
    """Agregasi per koin -> daftar sinyal, urut total USD terbesar."""
    agg = {}
    for p in positions:
        coin = p["coin"]
        a = agg.setdefault(coin, {
            "coin": coin, "mark": p["mark"],
            "long_usd": 0.0, "short_usd": 0.0,
            "long_whales": set(), "short_whales": set(),
            "long_w": 0.0, "short_w": 0.0,  # sum(entry*size) per sisi
            "biggest": None, "liqs": {"LONG": [], "SHORT": []},
        })
        if p["side"] == "LONG":
            a["long_usd"] += p["size_usd"]
            a["long_whales"].add(p["whale"])
            a["long_w"] += p["entry"] * p["size_usd"]
        else:
            a["short_usd"] += p["size_usd"]
            a["short_whales"].add(p["whale"])
            a["short_w"] += p["entry"] * p["size_usd"]
        if a["biggest"] is None or p["size_usd"] > a["biggest"]["size_usd"]:
            a["biggest"] = p
        if p["liq"]:
            a["liqs"][p["side"]].append(p["liq"])
    signals = []
    for coin, a in agg.items():
        total = a["long_usd"] + a["short_usd"]
        if total < MIN_SIGNAL_TOTAL_USD:
            continue
        if a["long_usd"] >= a["short_usd"]:
            side, dom_usd, dom_whales, opp_whales = "LONG", a["long_usd"], a["long_whales"], a["short_whales"]
            avg_entry = a["long_w"] / a["long_usd"] if a["long_usd"] else 0
        else:
            side, dom_usd, dom_whales, opp_whales = "SHORT", a["short_usd"], a["short_whales"], a["long_whales"]
            avg_entry = a["short_w"] / a["short_usd"] if a["short_usd"] else 0
        dom_pct = dom_usd / total * 100 if total else 0
        if dom_pct >= 60 and total >= 1000000 and len(dom_whales) >= 2:
            strength = "KUAT"
        elif dom_pct >= 55 and total >= 500000:
            strength = "SEDANG"
        else:
            strength = "LEMAH"
        # Likuidasi whale terdekat di sisi berlawanan (referensi saja).
        opp_side = "SHORT" if side == "LONG" else "LONG"
        opp_liqs = [x for x in a["liqs"][opp_side] if x and x > 0]
        nearest_liq = None
        if opp_liqs and a["mark"]:
            nearest_liq = min(opp_liqs, key=lambda x: abs(x - a["mark"]) / a["mark"])
        biggest = a["biggest"]
        signals.append({
            "coin": coin, "side": side, "strength": strength,
            "dom_pct": dom_pct, "total_usd": total,
            "dom_usd": dom_usd, "dom_whales": len(dom_whales),
            "opp_whales": len(opp_whales),
            "mark": a["mark"], "avg_entry": avg_entry,
            "biggest": biggest, "nearest_opp_liq": nearest_liq,
        })
    signals.sort(key=lambda s: s["total_usd"], reverse=True)
    return signals


def side_emoji(side):
    return "🟢" if side == "LONG" else "🔴"


def coin_label(coin):
    if coin == "PAXG":
        return "GOLD · PAXG"
    if coin == "PUMP":
        return "PUMP/USDT"
    return str(coin)


def signal_block(idx, s):
    """Satu blok sinyal (dipakai ringkasan maupun alert KUAT)."""
    lines = []
    head = side_emoji(s["side"]) + " <b>" + esc(coin_label(s["coin"])) + " " + esc(s["side"]) + " — " + esc(s["strength"]) + "</b>"
    if idx is not None:
        head = str(idx) + ". " + head
    lines.append(head)
    lines.append("Dominansi " + format(s["dom_pct"], ".0f") + "% · " + str(s["dom_whales"]) + " whale " + esc(s["side"]) + " vs " + str(s["opp_whales"]) + " lawan · Total " + esc(usd(s["total_usd"])))
    lines.append("Mark (harga kini): <b>$" + esc(px(s["mark"])) + "</b> · Entry rata-rata whale: $" + esc(px(s["avg_entry"])))
    b = s["biggest"]
    if b:
        lines.append("Posisi whale terbesar: " + esc(b["side"]) + " " + str(b["leverage"]) + "x " + esc(usd(b["size_usd"])) + " (<code>" + esc(b["whale"][:10]) + "...</code>)")
    liq = s["nearest_opp_liq"]
    if liq:
        opp = "SHORT" if s["side"] == "LONG" else "LONG"
        lines.append("Liq whale terdekat (sisi " + esc(opp) + "): $" + esc(px(liq)) + " — jarak " + esc(dist_str(liq, s["mark"])) + " dari mark")
    return NL.join(lines)


def summary_text(signals, top_n, hour_label, test=False):
    lines = []
    lines.append("📡 <b>SINYAL WHALE — ringkasan jam " + esc(hour_label) + " WIB</b>")
    if test:
        lines.append("🧪 <b>TES</b> — pesan contoh dari data live asli")
    lines.append(SEP)
    top = signals[:top_n]
    if not top:
        lines.append("Belum ada sinyal di atas ambang saat ini.")
    for i, s in enumerate(top, 1):
        lines.append(signal_block(i, s))
        lines.append("")
    shown = {s["coin"] for s in top}
    extra = [s for s in signals if s["coin"] in ("PAXG", "PUMP") and s["coin"] not in shown]
    if extra:
        lines.append(SEP)
        for s in extra:
            lines.append("📌 " + signal_block(None, s))
            lines.append("")
    lines.append(SEP)
    lines.append("<i>Sinyal = ringkasan posisi whale, bukan jaminan arah harga. Whale juga bisa salah — atur risiko sebelum masuk.</i>")
    lines.append("<i>Hyperliquid · Sinyal Ikut Whale</i>")
    return NL.join(lines)


def strong_alert_text(s):
    lines = []
    lines.append("🚨 <b>SINYAL KUAT BARU — " + esc(coin_label(s["coin"])) + " " + side_emoji(s["side"]) + " " + esc(s["side"]) + "</b>")
    lines.append(SEP)
    lines.append(signal_block(None, s))
    lines.append(SEP)
    lines.append("<i>Sinyal = ringkasan posisi whale, bukan jaminan arah harga. Whale juga bisa salah — atur risiko sebelum masuk.</i>")
    lines.append("<i>Hyperliquid · Sinyal Ikut Whale</i>")
    return NL.join(lines)


def split_messages(text, limit=4000):
    """Pecah teks panjang jadi beberapa pesan di batas baris."""
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for line in text.split(NL):
        if cur and len(cur) + len(line) + 1 > limit:
            parts.append(cur)
            cur = line
        else:
            cur = cur + NL + line if cur else line
    if cur:
        parts.append(cur)
    return parts


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


def send_all(text):
    sent = 0
    for part in split_messages(text):
        if send_telegram(part):
            sent += 1
    return sent


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return None


def save_state(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    state["saved_at"] = int(time.time() * 1000)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def hour_now_wib():
    now = datetime.now(WIB)
    return now.strftime("%Y-%m-%d %H"), now.strftime("%H:00")


def main():
    positions = fetch_positions()
    signals = build_signals(positions)
    print("Sinyal dihitung:", len(signals),
          "- KUAT:", sum(1 for s in signals if s["strength"] == "KUAT"),
          "- SEDANG:", sum(1 for s in signals if s["strength"] == "SEDANG"),
          "- LEMAH:", sum(1 for s in signals if s["strength"] == "LEMAH"))
    hour_key, hour_label = hour_now_wib()
    if TEST_MODE:
        text = summary_text(signals, SIGNAL_TOP_N, hour_label, test=True)
        ok = send_all(text)
        print("Pesan TES terkirim:", ok, "bagian dari", len(split_messages(text)))
        return
    state = load_state()
    first_run = state is None
    if first_run:
        state = {"last_summary_hour": None, "seen_strong": {}}
    seen_strong = state.get("seen_strong", {}) or {}
    # 1) Ringkasan tiap jam: sekali per jam WIB.
    if state.get("last_summary_hour") != hour_key:
        text = summary_text(signals, SIGNAL_TOP_N, hour_label)
        sent = send_all(text)
        if sent:
            state["last_summary_hour"] = hour_key
            print("Ringkasan jam", hour_label, "terkirim (" + str(sent) + " pesan).")
        else:
            print("Ringkasan jam", hour_label, "GAGAL terkirim — jam tidak dicatat, dicoba lagi run berikut.")
    else:
        print("Ringkasan jam", hour_label, "sudah terkirim sebelumnya — lewati.")
    # 2) Alert sinyal KUAT baru (bukan di run pertama, supaya tidak banjir).
    if first_run:
        print("Run pertama: simpan state sinyal KUAT, tanpa alert.")
        for s in signals:
            if s["strength"] == "KUAT":
                seen_strong[s["coin"]] = s["side"]
    else:
        alerts = 0
        for s in signals:
            if s["strength"] != "KUAT":
                continue
            if seen_strong.get(s["coin"]) == s["side"]:
                continue
            if alerts >= MAX_STRONG_ALERTS:
                break
            if send_telegram(strong_alert_text(s)):
                alerts += 1
                print("Alert KUAT baru:", s["coin"], s["side"])
            seen_strong[s["coin"]] = s["side"]
        if alerts == 0:
            print("Tidak ada sinyal KUAT baru.")
    state["seen_strong"] = seen_strong
    save_state(state)


if __name__ == "__main__":
    main()
