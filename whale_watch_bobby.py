# whale_watch_bobby.py
# Pantau SATU wallet whale (default: BobbyBigSize) di Hyperliquid futures
# dan kirim notifikasinya ke Telegram. Terpisah dari notifier umum
# (whale_futures_notifier.py) yang memantau semua whale besar.
# Dijalankan oleh .github/workflows/whale-watch-bobby.yml (GitHub Actions).
#
# Alert yang dikirim (maks 5/run, posisi baru diprioritaskan):
#   a) POSISI BARU — koin+sisi yang belum ada di state run sebelumnya.
#      Menampilkan jam buka asli dari fills (userFillsByTime) bila ada.
#   b) POSISI DITUTUP — koin+sisi yang ada di state sebelumnya tapi hilang.
#   c) PERUBAHAN UKURAN BESAR — ukuran berubah >=20% DAN selisih >= $25.000.
# Hanya posisi >= WATCH_MIN_SIZE_USD (default $25.000) yang di-alert;
# posisi di bawah ambang tetap dicatat di state.
# Run PERTAMA (state kosong): simpan state saja, TANPA alert.
# Mode tes (TEST_MODE=true): kirim SATU pesan demo 🧪 TES berisi 1-2 posisi
# live wallet tersebut, tanpa mengubah state.
# TP/SL HANYA dari trigger orders asli whale — tidak pernah dikarang.
# CHAT TUJUAN TERPISAH: notif Bobby TIDAK dikirim ke chat Telegram utama.
# Chat/grup terpisah "Pantau Bobby", ID-nya dari Secret baru:
#   TELEGRAM_BOBBY_CHAT_ID (bot yang sama, TELEGRAM_BOT_TOKEN).
# Script HANYA mengirim ke BOBBY_CHAT_ID — TIDAK ada fallback ke chat
# utama. Bila Secret itu kosong, script berhenti tanpa mengirim apa pun.
# Rahasia TIDAK ditulis di file ini. Dari GitHub Secrets:
#   TELEGRAM_BOT_TOKEN, TELEGRAM_BOBBY_CHAT_ID
# Variable opsional: WATCH_WALLET, WATCH_MIN_SIZE_USD.
# State di .whale-bobby-state (actions/cache). Hanya library standar Python.

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
STATE_DIR = ".whale-bobby-state"
STATE_FILE = os.path.join(STATE_DIR, "state.json")
NL = chr(10)
SEP = "━━━━━━━━━━━━━━"

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
BOBBY_CHAT_ID = os.environ.get("BOBBY_CHAT_ID", "")
WATCH_WALLET = os.environ.get(
    "WATCH_WALLET", "0x7fdafde5cfb5465924316eced2d3715494c517d1"
).strip().lower()
WATCH_NAME = os.environ.get("WATCH_NAME", "BobbyBigSize")
MIN_SIZE_USD = float(os.environ.get("WATCH_MIN_SIZE_USD", "25000"))
RESIZE_MIN_PCT = 20.0          # perubahan ukuran minimal (%)
RESIZE_MIN_USD = 25000.0       # ...dan selisih minimal (USD)
RECENT_OPEN_HOURS = float(os.environ.get("RECENT_OPEN_HOURS", "24"))
TEST_MODE = os.environ.get("TEST_MODE", "") == "true"
MAX_ALERTS = 5


def esc(v):
    return html.escape(str(v), quote=False)


def post_json(url, payload, tries=3):
    """POST JSON dengan retry (jaringan runtime kadang memutus di tengah)."""
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=25) as res:
                return json.loads(res.read().decode())
        except Exception as e:
            last = e
            if i < tries - 1:
                time.sleep(1)
    raise last


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
    return "PAXG" if coin == "PAXG" else str(coin)


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
    return " — ".join(name + " $" + px(val) for name, val in pts)


def card_text(kind, p, extra_note=""):
    # kind: new | closed | resize | test
    subheaders = {
        "new": "🐳 <b>POSISI BARU</b>",
        "closed": "🚪 <b>POSISI DITUTUP</b>",
        "resize": "📏 <b>UKURAN BERUBAH</b>",
        "test": "🧪 <b>TES — contoh kartu posisi live</b>",
    }
    lines = []
    lines.append("👀 <b>PANTAU WHALE — " + esc(WATCH_NAME) + "</b>")
    lines.append(subheaders.get(kind, subheaders["new"]))
    lines.append(SEP)
    label = coin_label(p.get("coin", ""))
    side = p.get("side", "")
    side_emoji = "🟢" if side == "LONG" else "🔴"
    title_line = side_emoji + " <b>" + esc(label) + " " + esc(side) + " " + esc(p.get("leverage", "")) + "x</b>"
    if p.get("lev_type"):
        title_line += " (" + esc(p["lev_type"]) + ")"
    lines.append(title_line)
    lines.append(SEP)
    if kind in ("new", "test") and p.get("open_ms"):
        lines.append("🕐 Dibuka: <b>" + esc(fmt_wib(num(p["open_ms"]))) + "</b>")
    if kind == "resize" and p.get("old_size_usd") is not None:
        old = num(p["old_size_usd"])
        new = num(p["size_usd"])
        arrow = "📈 Tambah" if new > old else "📉 Kurang"
        delta = new - old
        pct = (delta / old * 100) if old else 0
        lines.append(arrow + ": <b>" + esc(usd(old)) + " → " + esc(usd(new)) + "</b> (" + esc(usd_signed(delta)) + ", " + ("+" if pct >= 0 else "") + format(pct, ".1f") + "%)")
    lines.append("💰 Ukuran: <b>" + esc(usd(num(p.get("size_usd")))) + "</b> — " + esc(format(num(p.get("size_coin")), ".4f")) + " " + esc(coin_short(p.get("coin", ""))))
    mark = num(p.get("mark"))
    entry = num(p.get("entry"))
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
    if kind != "closed":
        bar = price_bar(p)
        if bar:
            lines.append(SEP)
            lines.append("<code>" + esc(bar) + "</code>")
            lines.append("<i>Mark = harga kini · Entry = harga buka · Liq = harga likuidasi</i>")
    lines.append(SEP)
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
    if kind == "closed":
        lines.append(SEP)
        lines.append("Posisi sudah tutup. Ukuran &amp; entry di atas adalah yang terakhir tercatat.")
    if extra_note:
        lines.append(SEP)
        lines.append(esc(extra_note))
    lines.append(SEP)
    lines.append("Wallet: <code>" + esc(str(WATCH_WALLET)[:10]) + "...</code>")
    lines.append("<i>Hyperliquid · Futures Perp · Pantau 1 wallet</i>")
    return NL.join(lines)[:4000]


def send_telegram(text):
    if not BOT_TOKEN:
        print("TELEGRAM_BOT_TOKEN belum di-set di GitHub Secrets.")
        return False
    if not BOBBY_CHAT_ID:
        print("Secret TELEGRAM_BOBBY_CHAT_ID belum diisi — buat Secret itu di repo Settings > Secrets and variables > Actions")
        return False
    url = "https://api.telegram.org/bot" + BOT_TOKEN + "/sendMessage"
    try:
        data = post_json(url, {"chat_id": BOBBY_CHAT_ID, "text": text[:4096], "parse_mode": "HTML", "disable_web_page_preview": True})
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


def fetch_wallet_positions():
    """Ambil semua posisi futures SATU wallet + TP/SL dari trigger orders.
    Mengembalikan (positions, marks). Posisi di bawah ambang tetap disertakan
    supaya tercatat di state (tidak di-alert, tapi tidak dianggap 'baru'
    bila nanti membesar)."""
    state = post_json(HL_INFO, {"type": "clearinghouseState", "user": WATCH_WALLET})
    orders = post_json(HL_INFO, {"type": "frontendOpenOrders", "user": WATCH_WALLET})
    marks = fetch_marks()
    positions = []
    for ap in state.get("assetPositions", []) or []:
        p = ap.get("position", {})
        size_usd = num(p.get("positionValue"))
        szi = num(p.get("szi"))
        if szi == 0:
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
            "key": WATCH_WALLET + "|" + str(p.get("coin")) + "|" + side,
            "whale": WATCH_WALLET,
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


def save_state(positions):
    os.makedirs(STATE_DIR, exist_ok=True)
    state = {"positions": {p["key"]: p for p in positions}, "saved_at": int(time.time() * 1000)}
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def recent_open_time(addr, coin, side, hours):
    """Waktu (epoch ms) fill pembuka TERBARU untuk `coin` dalam `hours` jam
    terakhir, atau None bila tidak ada / pengecekan gagal."""
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


def main():
    if not BOBBY_CHAT_ID:
        print("Secret TELEGRAM_BOBBY_CHAT_ID belum diisi — buat Secret itu di repo Settings > Secrets and variables > Actions")
        print("Script berhenti tanpa mengirim apa pun.")
        return
    positions, marks = fetch_wallet_positions()
    print("Posisi", WATCH_NAME, "(" + WATCH_WALLET[:10] + "...):", len(positions),
          "total:", usd(sum(p["size_usd"] for p in positions)))

    if TEST_MODE:
        # Satu pesan demo dari data live, TANPA mengubah state.
        demo = sorted(positions, key=lambda p: -p["size_usd"])[:2]
        if not demo:
            print("Tidak ada posisi live untuk contoh tes.")
            return
        parts = [
            "👀 <b>PANTAU WHALE — " + esc(WATCH_NAME) + " — 🧪 TES berhasil</b>",
            SEP,
            "Workflow GitHub Actions nyambung ke Telegram.",
            "Notif posisi BobbyBigSize (baru, ditutup, ukuran berubah) akan dikirim dari workflow ini.",
            SEP,
            "<i>Contoh format kartu dari posisi live di bawah ini:</i>",
            "",
        ]
        for p in demo:
            pp = dict(p)
            pp["open_ms"] = recent_open_time(WATCH_WALLET, p["coin"], p["side"], RECENT_OPEN_HOURS)
            parts.append(card_text("test", pp))
            parts.append("")
        ok = send_telegram(NL.join(parts)[:4096])
        print("Test message:", ok)
        return

    state = load_state()
    if state is None:
        save_state(positions)
        print("Run pertama: state awal disimpan (" + str(len(positions)) + " posisi). Alert mulai run berikutnya.")
        return

    prev = state.get("positions", {}) or {}
    alerts = []  # (prioritas, ukuran, teks) — prioritas 0 = posisi baru dulu

    # a) POSISI BARU
    for p in positions:
        if p["key"] in prev:
            continue
        if p["size_usd"] < MIN_SIZE_USD:
            continue
        pp = dict(p)
        pp["open_ms"] = recent_open_time(WATCH_WALLET, p["coin"], p["side"], RECENT_OPEN_HOURS)
        alerts.append((0, p["size_usd"], card_text("new", pp)))

    # c) PERUBAHAN UKURAN BESAR
    cur_keys = {p["key"] for p in positions}
    for p in positions:
        old = prev.get(p["key"])
        if old is None:
            continue
        old_size = num(old.get("size_usd"))
        new_size = num(p["size_usd"])
        delta = new_size - old_size
        if old_size <= 0 or abs(delta) < RESIZE_MIN_USD:
            continue
        if abs(delta) / old_size * 100 < RESIZE_MIN_PCT:
            continue
        if max(old_size, new_size) < MIN_SIZE_USD:
            continue
        pp = dict(p)
        pp["old_size_usd"] = old_size
        alerts.append((1, new_size, card_text("resize", pp)))

    # b) POSISI DITUTUP
    for key, old in prev.items():
        if key in cur_keys:
            continue
        if num(old.get("size_usd")) < MIN_SIZE_USD:
            continue
        closed = dict(old)
        closed["mark"] = marks.get(old["coin"], num(old.get("entry")))
        alerts.append((2, num(old.get("size_usd")), card_text("closed", closed)))

    alerts.sort(key=lambda t: (t[0], -t[1]))
    sent = 0
    for _prio, _size, msg in alerts[:MAX_ALERTS]:
        if send_telegram(msg):
            sent += 1
    print("Alert terkumpul:", len(alerts), "- notif terkirim:", sent)
    save_state(positions)


if __name__ == "__main__":
    main()
