import feedparser
import json
import os
import re
import time
import hashlib
import urllib.parse
from datetime import datetime, date, timedelta, timezone
from email.utils import parsedate_to_datetime
import requests

# ============================================================
# 0. KONFIGURASI ARSIP BERITA (anti-bloat)
# ============================================================
MAX_ARTICLES_PER_ENTITY = 20   # cap keras per lembaga / per anggota (jaga ukuran file; worst-case 56x20=1120 artikel)
MAX_AGE_DAYS = 30              # rolling window: buang artikel lebih tua dari ini
AGENCY_FETCH_LIMIT = 10        # artikel baru maksimal per lembaga per run
MEMBER_FETCH_LIMIT = 5         # artikel baru maksimal per anggota per run

# ============================================================
# 1. DATA SUMBER: LEMBAGA & KEYWORD (KOMISI VI DPR RI PARTNERS)
#    Bidang: BUMN, Perdagangan, Investasi, Koperasi/UMKM,
#    Persaingan Usaha, Kawasan Perdagangan Bebas.
# ============================================================
AGENCIES = {
    "Kementerian BUMN": "Kementerian BUMN",
    "Kementerian Perdagangan": "Kementerian Perdagangan OR Kemendag",
    "Kementerian Koperasi": "Kementerian Koperasi OR Kemenkop",
    "Danantara": "Badan Pengelola Investasi Daya Anagata Nusantara OR Danantara",
    "BPKN": "Badan Perlindungan Konsumen Nasional OR BPKN",
    "KPPU": "Komisi Pengawas Persaingan Usaha OR KPPU",
    "BP Batam": "BP Batam OR Badan Pengusahaan Kawasan Perdagangan Bebas",
    "BPK Sabang": "Badan Pengusahaan Kawasan Sabang OR BPK Sabang",
    "Dekopin": "Dewan Koperasi Indonesia OR Dekopin",
    "Badan Pengaturan BUMN": "Badan Pengaturan BUMN"
}

# ============================================================
# 2. DATA ANGGOTA KOMISI VI DPR RI (2024-2029)
# ------------------------------------------------------------
# Divalidasi 5 Jul 2026: fraksigerindra.id (Gerindra, 7 anggota —
# Mulyadi TAMBAHAN dari daftar lama), nasdemdprri.id (NasDem, cocok),
# fpd-dpr.com (Demokrat: Tutik Kusuma Wardhani PINDAH ke Komisi IX
# Feb 2025 -> dikeluarkan, pengganti belum terkonfirmasi), Wikipedia.
# Golkar: sumber bertentangan -> daftar lama dipertahankan, MERAGUKAN.
# Owner (TA Komisi VI) mengonfirmasi final — lihat laporan port.
# ============================================================
KOMISI6_MEMBERS = {
    "Gerindra": ["Andre Rosiade", "Khilmi", "Muhammad Husein Fadlulloh", "Mulan Jameela", "Kawendra Lukistian", "Unru Baso", "Mulyadi"],
    "PDI-P": ["Adisatrya Suryo Sulisto", "Mufti Anam", "Darmadi Durianto", "Rieke Diah Pitaloka", "I Gusti Ngurah Kesuma Kelakan", "Sadarestuwati", "Ida Nurlaela", "Budi Sulistyono", "Totok Hedi Santosa"],
    "Golkar": ["Nurdin Halid", "Gde Sumarjaya Linggih", "Ahmad Labib", "Sarifah Suraidah", "Doni Akbar", "Firnando Hadityo Ganinduto", "Rizki Faisal", "Muhammad Sarmuji"],
    "NasDem": ["Rachmat Gobel", "Asep Wahyuwijaya", "I Nengah Senantara", "Randi Zulmariadi", "Rudi Hartono Bangun", "Subardi"],
    "PKB": ["Anggia Erma Rini", "Rivqy Abdul Halim", "Nasim Khan", "Ida Fauziyah", "Imas Aan Ubudiah"],
    "PKS": ["Amin Ak.", "Rizal Bawazier", "Ghufran", "Ismail"],
    "PAN": ["Eko Patrio", "Nasril Bahar", "Abdul Hakim Bafagih", "Iskandar"],
    "Demokrat": ["Sartono", "Herman Khaeron", "Faujia Helga"]
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}

# ============================================================
# HELPERS
# ============================================================
def safe_print(s):
    """Console Windows (cp1252) bisa gagal print karakter unicode judul berita."""
    try:
        print(s)
    except UnicodeEncodeError:
        print(s.encode("ascii", "replace").decode())

def _stat(value, source, source_url, confidence, fetched_at):
    return {
        "value": value,
        "source": source,
        "source_url": source_url,
        "confidence": confidence,   # AUDITED | KLAIM_MANAJEMEN | UNAUDITED | FALLBACK
        "fetched_at": fetched_at,
    }

def fetch_json(url, params=None, timeout=15):
    r = requests.get(url, headers=HEADERS, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()

# ============================================================
# ARSIP BERITA: dedup by link + rolling window + cap
# ============================================================
def _article_dt(art):
    """Tanggal artikel: published (RSS) dulu, fallback fetched_at. Aware UTC."""
    p = art.get("published")
    if p and p != "N/A":
        try:
            d = parsedate_to_datetime(p)
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return d
        except Exception:
            pass
    f = art.get("fetched_at")
    if f:
        try:
            d = datetime.fromisoformat(f)
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return d
        except Exception:
            pass
    return None

def merge_archive(old_arts, new_arts):
    """Gabung arsip lama + artikel baru: dedup by link (versi lama menang, agar
    fetched_at stabil), buang > MAX_AGE_DAYS, urutkan terbaru dulu, cap keras."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
    seen = {}
    for art in (old_arts or []):
        link = art.get("link")
        if link and link != "#" and link not in seen:
            seen[link] = art
    for art in (new_arts or []):
        link = art.get("link")
        if link and link != "#" and link not in seen:
            seen[link] = art
    kept = []
    for art in seen.values():
        d = _article_dt(art)
        if d is None or d >= cutoff:   # tanggal tak terbaca -> jangan buang, cap yang membatasi
            kept.append(art)
    kept.sort(key=lambda a: ((_article_dt(a) or datetime.min.replace(tzinfo=timezone.utc)).isoformat(),
                             a.get("link", "")), reverse=True)
    return kept[:MAX_ARTICLES_PER_ENTITY]

def load_aliases():
    """Baca aliases.json (satu sumber kebenaran alias nama anggota).
    Kunci berawalan '_' = catatan, diabaikan. Gagal baca -> {} (aman-gagal)."""
    path = os.path.join(os.path.dirname(__file__), "aliases.json")
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        return {k: v for k, v in raw.items()
                if not k.startswith("_") and isinstance(v, list)}
    except Exception as e:
        print(f"  [WARN] aliases.json tidak terbaca ({e}); lanjut tanpa alias.")
        return {}

def load_previous_archives(path):
    """Baca live_data.json lama. Backward-compatible: agency_news bisa list
    (skema lama, 1 artikel per lembaga) atau dict-of-array (skema baru);
    member_news value bisa objek tunggal atau array."""
    try:
        with open(path, encoding="utf-8") as f:
            old = json.load(f)
    except Exception:
        return {}, {}
    ag = {}
    raw = old.get("agency_news")
    if isinstance(raw, dict):
        for k, v in raw.items():
            ag[k] = v if isinstance(v, list) else ([v] if isinstance(v, dict) else [])
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("agency"):
                art = {k2: v2 for k2, v2 in item.items() if k2 != "agency"}
                ag.setdefault(item["agency"], []).append(art)
    mem = {}
    rawm = old.get("member_news")
    if isinstance(rawm, dict):
        for k, v in rawm.items():
            mem[k] = v if isinstance(v, list) else ([v] if isinstance(v, dict) else [])
    return ag, mem

# ============================================================
# 3. FASE 1: BERITA LEMBAGA (Google News RSS) -> arsip bergulir
# ============================================================
def fetch_agency_news(prev_archive):
    archive = {}
    errors = []
    ok_count = 0
    for agency, query in AGENCIES.items():
        url = f"https://news.google.com/rss/search?q={urllib.parse.quote(query)}&hl=id&gl=ID&ceid=ID:id"
        print(f"  [LEMBAGA] Menarik data: {agency}...")
        new_arts = []
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:AGENCY_FETCH_LIMIT]:
                new_arts.append({
                    "title": entry.title,
                    "link": entry.link,
                    "link_resolved": None,
                    "published": entry.get("published", "N/A"),
                    "fetched_at": datetime.now().isoformat(),
                })
            if new_arts:
                ok_count += 1
        except Exception as e:
            msg = f"Gagal fetch {agency}: {e}"
            safe_print(f"  [WARN] {msg}")
            errors.append(msg)
        archive[agency] = merge_archive(prev_archive.get(agency), new_arts)
        safe_print(f"    -> {len(new_arts)} baru, arsip {len(archive[agency])} artikel")
    return archive, errors, ok_count

# ============================================================
# 4. FASE 2: BERITA ANGGOTA (Google News RSS) -> arsip bergulir
# ============================================================
def fetch_member_news(prev_archive):
    archive = {}
    errors = []
    ok_count = 0
    aliases = load_aliases()
    all_members = [m for members in KOMISI6_MEMBERS.values() for m in members]
    print(f"  Memproses {len(all_members)} anggota Komisi VI...")
    for member in all_members:
        new_arts = []
        try:
            # Query mencakup nama kanonik OR tiap alias; hasil TETAP disimpan
            # di bawah nama kanonik (arsip/cap/dedup tidak pecah per alias).
            member_aliases = aliases.get(member, [])
            if member_aliases:
                names_or = " OR ".join('"' + n + '"' for n in [member] + member_aliases)
                q = f'({names_or}) DPR OR Komisi'
            else:
                q = f'"{member}" DPR OR Komisi'   # identik dgn query lama bila tanpa alias
            query = urllib.parse.quote(q)
            url = f"https://news.google.com/rss/search?q={query}&hl=id&gl=ID&ceid=ID:id"
            feed = feedparser.parse(url)
            for entry in feed.entries[:MEMBER_FETCH_LIMIT]:
                new_arts.append({
                    "title": entry.title,
                    "link": entry.link,
                    "link_resolved": None,
                    "published": entry.get("published", "N/A"),
                    "fetched_at": datetime.now().isoformat(),
                })
            if new_arts:
                ok_count += 1
                safe_print(f"  [OK] {member}: {new_arts[0]['title'][:60]}... (+{len(new_arts)} baru)")
            else:
                print(f"  [INFO] Tidak ada berita untuk: {member}")
        except Exception as e:
            msg = f"Gagal fetch anggota {member}: {e}"
            safe_print(f"  [WARN] {msg}")
            errors.append(msg)
        merged = merge_archive(prev_archive.get(member), new_arts)
        if merged:
            archive[member] = merged
        time.sleep(0.5)
    return archive, errors, ok_count

# ============================================================
# 4b. DECODE LINK GOOGLE NEWS -> link_resolved (AMAN-GAGAL)
# ------------------------------------------------------------
# Link RSS Google News terenkripsi (news.google.com/rss/articles/CBMi...).
# Decode memakai endpoint internal batchexecute (teknik publik yang dipakai
# pustaka googlenewsdecoder; bisa berubah sewaktu-waktu di sisi Google).
# PRINSIP: kegagalan apa pun -> link_resolved tetap None, scraping lanjut.
# Budget per run membatasi biaya + rate limit; artikel terbaru diprioritaskan.
# Artikel yang sudah punya link_resolved TIDAK di-decode ulang.
# ============================================================
DECODE_BUDGET_DEFAULT = 30    # maks percobaan decode per run (override: env GNEWS_DECODE_BUDGET)
DECODE_SLEEP = 0.4            # jeda sopan antar-request

def _gnews_article_id(link):
    m = re.search(r'news\.google\.com/rss/articles/([^?/]+)', link or '')
    return m.group(1) if m else None

def _decode_one(session, art_id):
    """Resolve satu artikel. Return URL asli atau None. Exception ditelan pemanggil."""
    r = session.get(f"https://news.google.com/rss/articles/{art_id}",
                    headers=HEADERS, timeout=10)
    r.raise_for_status()
    sg = re.search(r'data-n-a-sg="([^"]+)"', r.text)
    ts = re.search(r'data-n-a-ts="([^"]+)"', r.text)
    if not sg or not ts:
        return None
    inner = ('["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,'
             'null,null,null,null,null,0,1],"X","X",1,[1,1,1],1,1,null,0,0,null,0],'
             f'"{art_id}",{ts.group(1)},"{sg.group(1)}"]')
    payload = json.dumps([[["Fbv4je", inner, None, "generic"]]])
    resp = session.post(
        "https://news.google.com/_/DotsSplashUi/data/batchexecute",
        headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
        data="f.req=" + urllib.parse.quote(payload), timeout=10)
    resp.raise_for_status()
    for line in resp.text.splitlines():
        if "garturlres" not in line:
            continue
        try:
            outer = json.loads(line)
            decoded = json.loads(outer[0][2])
            if isinstance(decoded, list) and len(decoded) > 1 and str(decoded[1]).startswith("http"):
                return decoded[1]
        except Exception:
            pass
    m = re.search(r'garturlres\\",\\"(https?://[^"\\\\]+)', resp.text)
    return m.group(1) if m else None

def resolve_links(agency_arch, member_arch):
    """Isi link_resolved untuk artikel yang masih None, terbaru dulu, dibatasi budget."""
    try:
        budget = int(os.environ.get("GNEWS_DECODE_BUDGET", DECODE_BUDGET_DEFAULT))
    except Exception:
        budget = DECODE_BUDGET_DEFAULT
    if budget <= 0:
        return 0, 0
    pending = []
    for arch in (agency_arch, member_arch):
        for arts in arch.values():
            for a in arts:
                if not a.get("link_resolved") and str(a.get("link", "")).startswith("https://news.google.com/"):
                    pending.append(a)
    pending.sort(key=lambda a: (_article_dt(a) or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    if not pending:
        print("  [DECODE] Semua link sudah ter-resolve / tidak ada kandidat.")
        return 0, 0
    todo = pending[:budget]
    print(f"  [DECODE] {len(pending)} link belum ter-resolve; mencoba {len(todo)} (budget {budget})...")
    ok = 0
    session = requests.Session()
    for i, a in enumerate(todo):
        art_id = _gnews_article_id(a["link"])
        if not art_id:
            continue
        try:
            real = _decode_one(session, art_id)
            if real:
                a["link_resolved"] = real
                ok += 1
        except Exception:
            pass   # aman-gagal: link_resolved tetap None, lanjut artikel berikutnya
        if (i + 1) % 25 == 0:
            print(f"  [DECODE] progres {i+1}/{len(todo)} (berhasil {ok})...")
        time.sleep(DECODE_SLEEP)
    print(f"  [DECODE] Selesai: {ok}/{len(todo)} berhasil.")
    return ok, len(todo)

# ============================================================
# 5. FASE 3: STATISTIK (data pasar live + FALLBACK jujur)
# ============================================================
#
# Prinsip: hanya angka yang punya sumber live beneran yang jadi live.
# Sisanya FALLBACK berlabel, BUKAN scrape regex yang menebak.
#
# Confidence:
#   AUDITED         -> rilis resmi (BPS WebAPI, dst.)
#   UNAUDITED       -> data operasional/pasar (Yahoo Finance)
#   FALLBACK        -> nilai statis manual, belum/tidak ada API
#
# ------------------------------------------------------------
# 5a. DATA PASAR (yfinance) — Kurs USD/IDR + IHSG
# ------------------------------------------------------------
# Data pasar Yahoo Finance = indikatif, BUKAN rilis resmi BI/BEI,
# karena itu labelnya UNAUDITED (tidak pernah AUDITED).
# Gagal fetch (rate limit / jaringan / pustaka berubah) -> nilai
# FALLBACK statis ber-label; TIDAK PERNAH tampil seolah live.
# (Perbaikan bug versi pra-kerja: live_kurs=15500 tanpa label.)
# Matikan seluruh lapisan pasar dengan ENABLE_MARKET_DATA = False.
ENABLE_MARKET_DATA = True
MARKET_TICKERS = {
    # kunci output: (ticker Yahoo, desimal, nilai fallback statis, label tampil)
    "kurs_usd_idr": ("IDR=X", 0, 16000, "Kurs USD/IDR"),
    "ihsg":         ("^JKSE", 2, 7000,  "IHSG"),
    # Opsional (keputusan owner, belum diaktifkan):
    # "brent":      ("BZ=F",  2, 80,    "Minyak Brent"),
}

def fetch_market_data(now):
    """Tarik data pasar via yfinance. Sukses -> UNAUDITED + sumber Yahoo
    Finance; kegagalan APA PUN -> FALLBACK statis ber-label (aman-gagal)."""
    stats = {}
    errors = []
    live_ok = 0
    def _fallback(key, tick, fb, note):
        return _stat(fb, f"Nilai acuan statis ({note}; bukan data live)",
                     f"https://finance.yahoo.com/quote/{urllib.parse.quote(tick)}/",
                     "FALLBACK", now)
    if not ENABLE_MARKET_DATA:
        for key, (tick, nd, fb, label) in MARKET_TICKERS.items():
            stats[key] = _fallback(key, tick, fb, "lapisan pasar dimatikan")
        return stats, errors, live_ok
    try:
        import yfinance as yf
    except Exception as e:
        errors.append(f"yfinance import gagal: {e}")
        for key, (tick, nd, fb, label) in MARKET_TICKERS.items():
            stats[key] = _fallback(key, tick, fb, "yfinance tidak tersedia")
        return stats, errors, live_ok
    for key, (tick, nd, fb, label) in MARKET_TICKERS.items():
        print(f"  [PASAR] {label} ({tick})...")
        try:
            hist = yf.Ticker(tick).history(period="5d")
            val = float(hist["Close"].dropna().iloc[-1])
            val = round(val, nd) if nd else int(round(val))
            stats[key] = _stat(val, "Yahoo Finance (data pasar, bukan rilis resmi)",
                               f"https://finance.yahoo.com/quote/{urllib.parse.quote(tick)}/",
                               "UNAUDITED", now)
            live_ok += 1
            print(f"  [PASAR] {label}: {val} (UNAUDITED)")
        except Exception as e:
            msg = f"yfinance {label} ({tick}) gagal: {e}"
            errors.append(msg)
            safe_print(f"  [WARN] {msg} -> FALLBACK statis ber-label")
            stats[key] = _fallback(key, tick, fb, "yfinance gagal")
    return stats, errors, live_ok

# ------------------------------------------------------------
# 5b. STATISTIK RESMI — scaffold, MENUNGGU KEPUTUSAN PIMPINAN
# ------------------------------------------------------------
# Pola sama dengan Komisi IV: flag False -> nilai FALLBACK ber-label.
# JANGAN diaktifkan tanpa instruksi owner.
#
# >>> BPS WebAPI (neraca perdagangan) — BUTUH API KEY GRATIS:
#     1. Daftar di https://webapi.bps.go.id, buat aplikasi -> dapat API key.
#     2. Simpan key sebagai GitHub Secret bernama BPS_API_KEY
#        (Settings > Secrets and variables > Actions).
#     3. Cari variable ID "Neraca Perdagangan" di katalog WebAPI,
#        isi NERACA_VAR_ID di bawah, lalu ENABLE_BPS_API = True.
#   Tanpa key/var_id, neraca aman jatuh ke FALLBACK.
ENABLE_BPS_API = False
BPS_KEY = os.environ.get("BPS_API_KEY", "").strip()
NERACA_VAR_ID = ""  # << isi var id neraca perdagangan dari katalog BPS WebAPI

def _parse_bps_latest(data):
    """Ambil nilai periode terbaru dari response 'dynamic data' BPS.
    Struktur BPS rumit (datacontent ber-key gabungan id); VERIFIKASI dengan
    response asli. Heuristik: ambil value pada key dengan tahun/periode terbesar."""
    dc = data.get("datacontent") if isinstance(data, dict) else None
    if not isinstance(dc, dict) or not dc:
        return None
    try:
        latest_key = sorted(dc.keys())[-1]   # heuristik kasar: key terbesar = periode terbaru
        return float(dc[latest_key])
    except Exception:
        return None

def fetch_bps_neraca(now):
    errors = []
    neraca = _stat("Surplus US$ 31,04 M (2024)", "BPS, nilai fallback statis",
                   "https://www.bps.go.id/", "FALLBACK", now)
    if not ENABLE_BPS_API or not BPS_KEY or not NERACA_VAR_ID:
        return neraca, errors   # mode statis disengaja, bukan kegagalan
    url = (f"https://webapi.bps.go.id/v1/api/list/model/data/lang/ind/"
           f"domain/0000/var/{NERACA_VAR_ID}/key/{BPS_KEY}/")
    print("  [STATS] BPS WebAPI (neraca perdagangan)...")
    try:
        data = fetch_json(url)
        val = _parse_bps_latest(data)
        if val is not None:
            neraca = _stat(val, "BPS WebAPI", "https://www.bps.go.id/", "AUDITED", now)
            print(f"  [STATS] Neraca perdagangan (BPS API): {val}")
        else:
            errors.append("BPS: datacontent kosong / format tak terduga")
    except Exception as e:
        errors.append(f"BPS neraca gagal: {e}")
        print(f"  [WARN] BPS neraca gagal: {e}")
    return neraca, errors

# >>> BKPM / Kementerian Investasi (realisasi investasi):
#     Belum ada API publik bersih. Bila pimpinan setuju: konfirmasi
#     endpoint via DevTools (F12 > Network > Fetch/XHR) di situs BKPM,
#     isi BKPM_API_URL + sesuaikan parser, baru flip flag ke True.
ENABLE_BKPM_API = False
BKPM_API_URL = ""  # << isi setelah endpoint dikonfirmasi owner

def fetch_bkpm_investasi(now):
    errors = []
    inv = _stat("Rp 1.714 Triliun (2024)", "Kementerian Investasi/BKPM, nilai fallback statis",
                "https://www.bkpm.go.id/", "FALLBACK", now)
    if not ENABLE_BKPM_API or not BKPM_API_URL:
        return inv, errors   # mode statis disengaja, bukan kegagalan
    print("  [STATS] BKPM (realisasi investasi)...")
    try:
        data = fetch_json(BKPM_API_URL)
        # << SESUAIKAN parser dengan bentuk response asli, lalu set:
        # inv = _stat(val, "BKPM (API)", "https://www.bkpm.go.id/", "UNAUDITED", now)
        errors.append("BKPM: parser belum disesuaikan dengan response asli")
    except Exception as e:
        errors.append(f"BKPM gagal: {e}")
        print(f"  [WARN] BKPM gagal: {e}")
    return inv, errors

# >>> Kemenkop (jumlah koperasi aktif / UMKM): belum ada API publik
#     bersih (ODS Kemenkop butuh konfirmasi endpoint). Pola sama.
ENABLE_KEMENKOP_API = False
KEMENKOP_API_URL = ""  # << isi setelah endpoint dikonfirmasi owner

def fetch_kemenkop_stats(now):
    errors = []
    koperasi = _stat("±130 Ribu Unit Aktif", "Kemenkop, nilai fallback statis",
                     "https://kemenkop.go.id/", "FALLBACK", now)
    umkm = _stat("±65 Juta Unit", "Kemenkop/BPS, nilai fallback statis",
                 "https://kemenkop.go.id/", "FALLBACK", now)
    if not ENABLE_KEMENKOP_API or not KEMENKOP_API_URL:
        return koperasi, umkm, errors   # mode statis disengaja, bukan kegagalan
    print("  [STATS] Kemenkop (koperasi/UMKM)...")
    try:
        data = fetch_json(KEMENKOP_API_URL)
        # << SESUAIKAN parser dengan bentuk response asli, lalu set koperasi/umkm
        errors.append("Kemenkop: parser belum disesuaikan dengan response asli")
    except Exception as e:
        errors.append(f"Kemenkop gagal: {e}")
        print(f"  [WARN] Kemenkop gagal: {e}")
    return koperasi, umkm, errors

# ------------------------------------------------------------
# 5c. KOMPILASI STAT RESMI (semua FALLBACK sampai flag diaktifkan)
# ------------------------------------------------------------
def build_macro_stats(now, invest_stat, neraca_stat, koperasi_stat, umkm_stat):
    return {
        "realisasi_investasi": invest_stat,
        "neraca_perdagangan":  neraca_stat,
        "jumlah_koperasi":     koperasi_stat,
        "jumlah_umkm":         umkm_stat,
    }

# ============================================================
# 5d. SNAPSHOT ARSIP BULANAN — archive/monthly/YYYY-MM.json
# ------------------------------------------------------------
# Aset data jangka panjang untuk lapisan "Intelijen Bulanan Fraksi":
# AGREGAT (hitungan) + 15 headline teratas, BUKAN duplikasi arsip penuh.
# Skema live_data.json TIDAK berubah.
#
# COMMIT-ON-DIFF AMAN: snapshot HANYA ditulis pada jalur ketika
# live_data.json juga ditulis (konten berubah). Saat [SKIP], snapshot
# tidak disentuh, jadi tidak pernah memicu commit sendiri walau
# hitungan jendela 7/30 hari bergeser karena waktu berjalan.
#
# JAGA SINKRON MANUAL dengan TOPIC_TAGS dan EW_KEYWORDS di index.html
# (keduanya owner-tunable). Drift hanya memengaruhi isi snapshot,
# tidak memengaruhi UI dashboard.
# ============================================================
ARCHIVE_DIR = os.path.join(os.path.dirname(__file__), "archive", "monthly")

SNAPSHOT_TOPIC_TAGS = {
    "danantara":       ["danantara"],
    "bumn":            ["bumn", "badan usaha milik negara"],
    "merger-akuisisi": ["merger", "akuisisi"],
    "dividen":         ["dividen"],
    "ipo":             ["ipo"],
    "kartel-monopoli": ["kartel", "monopoli", "persaingan usaha"],
    "koperasi":        ["koperasi", "kopdes"],
    "umkm":            ["umkm", "usaha mikro", "usaha kecil"],
    "investasi":       ["investasi", "penanaman modal", "investor"],
    "ekspor":          ["ekspor"],
    "impor":           ["impor"],
    "perdagangan":     ["perdagangan", "tarif", "ritel"],
}
SNAPSHOT_EW_KEYWORDS = {
    "Hukum/Audit":    ["korupsi", "kpk", "audit", "bpk", "penyidikan", "tersangka", "dugaan", "gratifikasi"],
    "Konflik Publik": ["demo", "protes", "tuntut", "tolak", "polemik", "kisruh", "ricuh"],
    "Kinerja BUMN":   ["rugi", "kerugian", "utang", "restrukturisasi", "efisiensi", "phk", "komisaris"],
    "Perdagangan":    ["impor", "ekspor", "harga", "minyakita", "surplus", "defisit", "bea masuk"],
    "Koperasi/UMKM":  ["koperasi", "umkm", "pembiayaan", "kredit", "kur"],
}
# Kata pendek/ambigu dicocokkan per-kata utuh agar tidak false-positive:
# 'demo' ~ "Demokrat", 'kur' ~ "kurs", 'bpk' ~ "BPKN", dst.
EW_WORD_ONLY = {"demo", "kur", "bpk", "phk", "kpk"}

def _kw_match(title_lower, kw):
    if kw in EW_WORD_ONLY:
        return re.search(r"\b" + re.escape(kw) + r"\b", title_lower) is not None
    return kw in title_lower

def build_monthly_snapshot(agency_news, member_news, now_iso):
    now = datetime.now(timezone.utc)
    party_of = {m: p for p, members in KOMISI6_MEMBERS.items() for m in members}
    def within(a, days):
        d = _article_dt(a)
        return d is not None and d >= now - timedelta(days=days)
    mem_arts = [(nm, a) for nm, arts in (member_news or {}).items() for a in (arts or [])]
    ag_arts = [(ag, a) for ag, arts in (agency_news or {}).items() for a in (arts or [])]
    party7, party30, member30, agency30 = {}, {}, {}, {}
    for nm, a in mem_arts:
        if within(a, 30):
            p = party_of.get(nm, "Lainnya")
            party30[p] = party30.get(p, 0) + 1
            member30[nm] = member30.get(nm, 0) + 1
            if within(a, 7):
                party7[p] = party7.get(p, 0) + 1
    for ag, a in ag_arts:
        if within(a, 30):
            agency30[ag] = agency30.get(ag, 0) + 1
    topic30, ew30 = {}, {}
    all30 = [(ent, a) for ent, a in ag_arts + mem_arts if within(a, 30)]
    for _, a in all30:
        t = str(a.get("title", "")).lower()
        for tag, kws in SNAPSHOT_TOPIC_TAGS.items():
            if any(k in t for k in kws):
                topic30[tag] = topic30.get(tag, 0) + 1
        for grp, kws in SNAPSHOT_EW_KEYWORDS.items():
            if any(_kw_match(t, k) for k in kws):
                ew30[grp] = ew30.get(grp, 0) + 1
    latest = sorted(
        all30,
        key=lambda ea: ((_article_dt(ea[1]) or datetime.min.replace(tzinfo=timezone.utc)).isoformat(),
                        str(ea[1].get("link", ""))),
        reverse=True)[:15]
    return {
        "period": date.today().strftime("%Y-%m"),
        "generated_at": now_iso,
        "party_counts_7d": party7,
        "party_counts_30d": party30,
        "agency_counts_30d": agency30,
        "topic_counts_30d": topic30,
        "member_counts_30d": member30,
        "early_warning_counts": ew30,
        "top_headlines": [
            {"title": a.get("title"), "entity": ent,
             "kind": "anggota" if ent in party_of else "lembaga",
             "published": a.get("published"),
             "link": a.get("link_resolved") or a.get("link")}
            for ent, a in latest
        ],
    }

def write_monthly_snapshot(snap):
    """Aman-gagal: kegagalan apa pun dicatat tapi tidak menghentikan engine
    dan tidak memengaruhi live_data.json."""
    try:
        os.makedirs(ARCHIVE_DIR, exist_ok=True)
        path = os.path.join(ARCHIVE_DIR, snap["period"] + ".json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, indent=2)
        print(f"[ARSIP] Snapshot bulanan ditulis: archive/monthly/{snap['period']}.json")
    except Exception as e:
        safe_print(f"[WARN] Gagal tulis snapshot bulanan: {e} (live_data.json tidak terpengaruh)")

# ============================================================
# 6. COMMIT-ON-DIFF: skip tulis kalau konten (tanpa timestamp) sama
# ============================================================
def _strip_volatile(obj):
    if isinstance(obj, dict):
        return {k: _strip_volatile(v) for k, v in obj.items()
                if k not in ("fetched_at", "last_updated")}
    if isinstance(obj, list):
        return [_strip_volatile(x) for x in obj]
    return obj

def _fingerprint(output):
    norm = _strip_volatile(output)
    blob = json.dumps(norm, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()

# ============================================================
# 7. MAIN
# ============================================================
def fetch_data():
    print("=" * 50)
    print("ENGINE KOMISI VI DIMULAI")
    print("=" * 50)
    now = datetime.now().isoformat()
    all_errors = []

    output_path = os.path.join(os.path.dirname(__file__), "live_data.json")
    prev_agency, prev_member = load_previous_archives(output_path)

    print("\n>>> FASE 1: Berita Lembaga Mitra (arsip bergulir)...")
    agency_news, e1, ag_ok = fetch_agency_news(prev_agency)
    all_errors += e1
    phase1 = "failed" if ag_ok == 0 else ("partial" if ag_ok < len(AGENCIES) else "ok")
    total_ag_arts = sum(len(v) for v in agency_news.values())
    print(f"[FASE 1] {ag_ok}/{len(AGENCIES)} lembaga dapat berita baru; total arsip {total_ag_arts} artikel.")

    print("\n>>> FASE 2: Berita Anggota (arsip bergulir)...")
    member_news, e2, mem_ok = fetch_member_news(prev_member)
    all_errors += e2
    total_members = sum(len(v) for v in KOMISI6_MEMBERS.values())
    phase2 = "failed" if len(member_news) == 0 else (
        "partial" if (e2 or mem_ok < total_members * 0.5) else "ok")
    total_mem_arts = sum(len(v) for v in member_news.values())
    print(f"[FASE 2] {mem_ok}/{total_members} anggota dapat berita baru; total arsip {total_mem_arts} artikel.")

    print("\n>>> FASE 2b: Decode link Google News (aman-gagal, budget terbatas)...")
    dec_ok, dec_try = resolve_links(agency_news, member_news)
    if dec_try > 0 and dec_ok == 0:
        all_errors.append(f"decode link gagal massal (0/{dec_try}); link_resolved tetap null, berita tidak terdampak")

    print("\n>>> FASE 3: Statistik (data pasar live + fallback)...")
    market_stats, e3a, market_ok = fetch_market_data(now)
    invest_stat, e3b = fetch_bkpm_investasi(now)
    neraca_stat, e3c = fetch_bps_neraca(now)
    koperasi_stat, umkm_stat, e3d = fetch_kemenkop_stats(now)
    all_errors += e3a + e3b + e3c + e3d
    macro_stats = build_macro_stats(now, invest_stat, neraca_stat, koperasi_stat, umkm_stat)

    # status fase 3 = berapa target live aktif yang benar-benar live.
    # Data pasar (yfinance) aktif default; API resmi menunggu approval pimpinan.
    # Kalau semua dimatikan (mode statis disengaja), itu bukan kegagalan -> "ok".
    live_targets = []
    if ENABLE_MARKET_DATA:
        live_targets += list(market_stats.values())
    if ENABLE_BPS_API and BPS_KEY and NERACA_VAR_ID:
        live_targets.append(neraca_stat)
    if ENABLE_BKPM_API and BKPM_API_URL:
        live_targets.append(invest_stat)
    if ENABLE_KEMENKOP_API and KEMENKOP_API_URL:
        live_targets += [koperasi_stat, umkm_stat]
    if not live_targets:
        phase3 = "ok"
        print("[FASE 3] Mode statis (FALLBACK), tidak ada target live aktif.")
    else:
        live_ok = sum(1 for s in live_targets if s["confidence"] in ("UNAUDITED", "AUDITED"))
        phase3 = "failed" if live_ok == 0 else ("partial" if live_ok < len(live_targets) else "ok")
        print(f"[FASE 3] {live_ok}/{len(live_targets)} target live.")

    output = {
        "agency_news": agency_news,
        "member_news": member_news,
        # dua indikator pasar utama di top-level (pola harga_beras/stok_bulog
        # Komisi IV); ticker tambahan (mis. brent) tinggal diwire bila owner setuju
        "kurs_usd_idr": market_stats.get("kurs_usd_idr"),
        "ihsg":         market_stats.get("ihsg"),
        "macro_stats": macro_stats,
        "scrape_status": {
            "phase_1_agency":  phase1,
            "phase_2_members": phase2,
            "phase_3_macro":   phase3,
            "errors": all_errors[-20:],
        },
        "last_updated": now,
    }

    # commit-on-diff: bandingkan fingerprint tanpa timestamp
    new_fp = _fingerprint(output)
    old_fp = None
    if os.path.exists(output_path):
        try:
            with open(output_path, encoding="utf-8") as f:
                old_fp = _fingerprint(json.load(f))
        except Exception:
            old_fp = None
    if new_fp == old_fp:
        print("\n[SKIP] Konten tidak berubah, live_data.json tidak ditulis ulang "
              "(mencegah commit kosong tiap jam).")
        return

    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=4)
        size_kb = os.path.getsize(output_path) / 1024
        print(f"\n[SUKSES] live_data.json diperbarui {datetime.now().strftime('%H:%M:%S')} "
              f"({size_kb:.0f} KB, {total_ag_arts + total_mem_arts} artikel)")
        # Snapshot bulanan HANYA di jalur ini (live_data.json baru saja ditulis)
        # sehingga tidak pernah memicu commit sendirian (commit-on-diff utuh).
        write_monthly_snapshot(build_monthly_snapshot(agency_news, member_news, now))
    except Exception as e:
        print(f"[GAGAL] Simpan data: {e}")

if __name__ == "__main__":
    fetch_data()
