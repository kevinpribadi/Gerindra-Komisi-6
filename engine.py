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
MAX_ARTICLES_PER_ENTITY = 20   # cap keras per lembaga / per anggota (jaga ukuran file; worst-case 54x20=1080 artikel)
MAX_AGE_DAYS = 30              # rolling window: buang artikel lebih tua dari ini
AGENCY_FETCH_LIMIT = 10        # artikel baru maksimal per lembaga per run
MEMBER_FETCH_LIMIT = 5         # artikel baru maksimal per anggota per run

# ============================================================
# 1. DATA SUMBER: LEMBAGA & KEYWORD (KOMISI IV DPR RI PARTNERS)
# ============================================================
AGENCIES = {
    "Kementerian Pertanian": "Kementerian Pertanian OR Kementan",
    "Kementerian Kehutanan": "Kementerian Kehutanan OR Kemenhut",
    "Kementerian Kelautan dan Perikanan": "Kementerian Kelautan dan Perikanan OR KKP",
    "Badan Karantina Indonesia": "Badan Karantina Indonesia OR Barantin",
    "Badan Pangan Nasional": "Badan Pangan Nasional OR Bapanas",
    "Perum Bulog": "Perum Bulog OR Bulog"
}

# ============================================================
# 2. DATA ANGGOTA KOMISI IV DPR RI (2024-2029)
# ============================================================
KOMISI4_MEMBERS = {
    "PDI-P": ["Alex Indra Lukman", "Sonny T. Danaparamita", "Mayjen TNI (Purn) Sturman Panjaitan", "Rokhmin Dahuri", "I Nyoman Adi Wiryatama", "Paolus Hadi", "Agus Ambo Djiwa", "I Ketut Suwendra", "Edoardus Kaize"],
    "Golkar": ["Panggah Susanto", "Robert Joppy Kardinal", "Adrianus Asia Sidot", "Eko Wahyudi", "Firman Subagyo", "Alien Mus", "Dadang M Naser", "Ilham Pangestu"],
    "Gerindra": ["Siti Hediati Soeharto", "Darori Wonodipuro", "Dwita Ria Gunadi", "Endang Setyawati Thohari", "TA Khalid", "Sumail Abdullah", "Melati"],
    "NasDem": ["Sulaeman L Hamzah", "Ananda Tohpati", "Cindy Monica Salsabila Setiawan", "Rajiv", "Arief Rahman", "Muhammad Habibur Rochman"],
    "PKB": ["Jaelani", "Daniel Johan", "Hindun Anisah", "Usman Husin", "Rina Sa'adah"],
    "PKS": ["Abdul Kharis", "Slamet", "Johan Rosihan", "Riyono", "Rahmat Saleh"],
    "PAN": ["Ahmad Yohan", "Herry Dermawan", "Irham Jafar Lan Putra", "Ajbar"],
    "Demokrat": ["Bambang Purwanto", "Ellen Esther Pelealu", "Hasan Saleh", "Muhammad Zulfikar Suhardi"]
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
    all_members = [m for members in KOMISI4_MEMBERS.values() for m in members]
    print(f"  Memproses {len(all_members)} anggota Komisi IV...")
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
# 5. FASE 3: DATA MAKRO (API resmi + FALLBACK jujur)
# ============================================================
#
# Prinsip: hanya angka yang punya sumber API beneran yang jadi live.
# Sisanya FALLBACK berlabel, BUKAN scrape regex yang menebak.
#
# Confidence:
#   AUDITED         -> rilis resmi BPS (WebAPI)
#   UNAUDITED       -> data operasional harian (Panel Harga Bapanas)
#   FALLBACK        -> nilai statis manual, belum/ tidak ada API
#
# ------------------------------------------------------------
# 5a. PANEL HARGA BAPANAS (harga beras) — API harian, tanpa auth
# ------------------------------------------------------------
# >>> KONFIRMASI ENDPOINT (2 menit, wajib sebelum deploy):
#     1. Buka https://panelharga.badanpangan.go.id  -> menu Harga Eceran.
#     2. DevTools (F12) > tab Network > filter "Fetch/XHR".
#     3. Ganti tanggal/komoditas; lihat request yang muncul (biasanya ke
#        host api-panelhargav2.badanpangan.go.id). Salin URL + parameternya.
#     4. Tempel URL itu ke PANELHARGA_URL, sesuaikan PANELHARGA_PARAMS, dan
#        sesuaikan _parse_panelharga_beras() dengan bentuk JSON response asli.
#   Sampai dikonfirmasi, fungsi ini aman gagal -> harga_beras tetap FALLBACK.
# >>> SAKLAR: integrasi API yang belum dikonfirmasi dimatikan dulu (mode statis jujur).
#     Flip ke True setelah endpoint/credential dikonfirmasi (lihat report untuk atasan).
ENABLE_PANELHARGA_API = False
PANELHARGA_URL = "https://api-panelhargav2.badanpangan.go.id/api/front/harga-pangan-table"

def _parse_panelharga_beras(data):
    """Best-effort: telusuri JSON cari komoditas 'Beras' -> harga rata-rata nasional.
    SESUAIKAN dengan struktur response asli setelah cek DevTools."""
    candidates = []
    if isinstance(data, dict):
        candidates = data.get("data") or data.get("result") or data.get("list") or []
    elif isinstance(data, list):
        candidates = data
    for item in candidates:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("nama") or item.get("komoditas") or "").lower()
        if "beras" in name:
            for k in ("today", "harga", "price", "value", "gridharga", "harga_today"):
                v = item.get(k)
                if isinstance(v, (int, float)) and v > 0:
                    return int(v)
                if isinstance(v, str):
                    digits = re.sub(r"[^\d]", "", v)
                    if digits:
                        return int(digits)
    return None

def fetch_food_prices(now):
    errors = []
    harga_beras = _stat(15500, "BAPANAS, nilai fallback statis",
                        "https://panelharga.badanpangan.go.id/", "FALLBACK", now)
    if not ENABLE_PANELHARGA_API:
        return harga_beras, []   # mode statis disengaja, bukan kegagalan
    today = date.today().strftime("%d/%m/%Y")
    params = {"level_harga_id": 3, "period_date": f"{today} - {today}", "province_id": ""}
    print("  [STATS] Panel Harga Bapanas (harga beras)...")
    try:
        data = fetch_json(PANELHARGA_URL, params=params)
        val = _parse_panelharga_beras(data)
        if val:
            harga_beras = _stat(val, "BAPANAS Panel Harga (API)",
                                "https://panelharga.badanpangan.go.id/", "UNAUDITED", now)
            print(f"  [STATS] Harga beras (API): Rp {val}")
        else:
            errors.append("panelharga: komoditas 'beras' tidak ditemukan / parser belum disesuaikan")
    except Exception as e:
        errors.append(f"panelharga gagal: {e}")
        print(f"  [WARN] panelharga gagal: {e}")
    return harga_beras, errors

# ------------------------------------------------------------
# 5b. BPS WebAPI (NTP) — data resmi, BUTUH API KEY GRATIS
# ------------------------------------------------------------
# >>> SETUP (sekali):
#     1. Daftar di https://webapi.bps.go.id, buat aplikasi -> dapat API key.
#     2. Simpan key sebagai GitHub Secret bernama BPS_API_KEY
#        (Settings > Secrets and variables > Actions).
#     3. Cari variable ID untuk "Nilai Tukar Petani" di katalog WebAPI,
#        isi NTP_VAR_ID di bawah.
#   Tanpa key/var_id, NTP aman jatuh ke FALLBACK.
ENABLE_BPS_API = False
BPS_KEY = os.environ.get("BPS_API_KEY", "").strip()
NTP_VAR_ID = ""  # << isi var id NTP dari katalog BPS WebAPI

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

def fetch_bps_ntp(now):
    errors = []
    ntp = _stat(110.5, "BPS, nilai fallback statis", "https://www.bps.go.id/", "FALLBACK", now)
    if not ENABLE_BPS_API or not BPS_KEY or not NTP_VAR_ID:
        return ntp, errors   # mode statis disengaja, bukan kegagalan
    url = (f"https://webapi.bps.go.id/v1/api/list/model/data/lang/ind/"
           f"domain/0000/var/{NTP_VAR_ID}/key/{BPS_KEY}/")
    print("  [STATS] BPS WebAPI (NTP)...")
    try:
        data = fetch_json(url)
        val = _parse_bps_latest(data)
        if val is not None:
            ntp = _stat(round(val, 2), "BPS WebAPI", "https://www.bps.go.id/", "AUDITED", now)
            print(f"  [STATS] NTP (BPS API): {val}")
        else:
            errors.append("BPS: datacontent kosong / format tak terduga")
    except Exception as e:
        errors.append(f"BPS NTP gagal: {e}")
        print(f"  [WARN] BPS NTP gagal: {e}")
    return ntp, errors

# ------------------------------------------------------------
# 5c. STAT FALLBACK JUJUR (belum ada API publik bersih)
#     Update manual berkala. Label tetap FALLBACK -> UI menandai abu-abu.
# ------------------------------------------------------------
def build_macro_stats(now, ntp_stat):
    return {
        "ntp":               ntp_stat,
        "luas_panen_padi":   _stat("10.2 Juta Ha",        "BPS, fallback statis",     "https://www.bps.go.id/",        "FALLBACK", now),
        "produksi_beras":    _stat("31.5 Juta Ton",       "BPS, fallback statis",     "https://www.bps.go.id/",        "FALLBACK", now),
        "luas_panen_jagung": _stat("4.1 Juta Ha",         "BPS, fallback statis",     "https://www.bps.go.id/",        "FALLBACK", now),
        "produksi_jagung":   _stat("14.4 Juta Ton",       "BPS, fallback statis",     "https://www.bps.go.id/",        "FALLBACK", now),
        "kampung_nelayan":   _stat("12 Lokasi Selesai",   "KKP, fallback statis",     "https://kkp.go.id/",            "FALLBACK", now),
        "harga_pangan_avg":  _stat("Stabil (Inflasi 0.2%)","BAPANAS, fallback statis","https://badanpangan.go.id/",    "FALLBACK", now),
        "bantuan_pangan":    _stat("85% Tersalurkan",     "BAPANAS, fallback statis", "https://badanpangan.go.id/",    "FALLBACK", now),
        "realisasi_sphp":    _stat("750.000 Ton",         "Bulog, fallback statis",   "https://www.bulog.co.id/",      "FALLBACK", now),
    }

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
    print("ENGINE KOMISI IV DIMULAI")
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
    total_members = sum(len(v) for v in KOMISI4_MEMBERS.values())
    phase2 = "failed" if len(member_news) == 0 else (
        "partial" if (e2 or mem_ok < total_members * 0.5) else "ok")
    total_mem_arts = sum(len(v) for v in member_news.values())
    print(f"[FASE 2] {mem_ok}/{total_members} anggota dapat berita baru; total arsip {total_mem_arts} artikel.")

    print("\n>>> FASE 2b: Decode link Google News (aman-gagal, budget terbatas)...")
    dec_ok, dec_try = resolve_links(agency_news, member_news)
    if dec_try > 0 and dec_ok == 0:
        all_errors.append(f"decode link gagal massal (0/{dec_try}); link_resolved tetap null, berita tidak terdampak")

    print("\n>>> FASE 3: Data Makro (API + fallback)...")
    harga_beras, e3a = fetch_food_prices(now)
    ntp_stat, e3b = fetch_bps_ntp(now)
    all_errors += e3a + e3b
    macro_stats = build_macro_stats(now, ntp_stat)
    stok_bulog = _stat(1250000, "Bulog, nilai fallback statis", "https://www.bulog.co.id/", "FALLBACK", now)

    # status fase 3 = berapa target API aktif yang benar-benar live.
    # Kalau semua API dimatikan (mode statis disengaja), itu bukan kegagalan -> "ok".
    live_targets = []
    if ENABLE_PANELHARGA_API:
        live_targets.append(harga_beras)
    if ENABLE_BPS_API and BPS_KEY and NTP_VAR_ID:
        live_targets.append(ntp_stat)
    if not live_targets:
        phase3 = "ok"
        print("[FASE 3] Mode statis (FALLBACK), tidak ada target API aktif.")
    else:
        live_ok = sum(1 for s in live_targets if s["confidence"] in ("UNAUDITED", "AUDITED"))
        phase3 = "failed" if live_ok == 0 else ("partial" if live_ok < len(live_targets) else "ok")
        print(f"[FASE 3] {live_ok}/{len(live_targets)} target API live.")

    output = {
        "agency_news": agency_news,
        "member_news": member_news,
        "harga_beras": harga_beras,
        "stok_bulog":  stok_bulog,
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
    except Exception as e:
        print(f"[GAGAL] Simpan data: {e}")

if __name__ == "__main__":
    fetch_data()
