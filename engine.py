import feedparser
import json
import os
import time
import urllib.parse
from datetime import datetime

import yfinance as yf

# ============================================================
# 1. DATA SUMBER: LEMBAGA & KEYWORD
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
# 2. DATA ANGGOTA KOMISI VI DPR RI (2024-2029) — DATA RESMI
#    Komisi VI membidangi: BUMN, Perdagangan, Koperasi, Investasi
#    Ketua: Anggia Erma Rini (PKB)
#    Wakil Ketua: Nurdin Halid (Golkar), Adisatrya Suryo Sulisto (PDI-P),
#                 Eko Patrio (PAN), Andre Rosiade (Gerindra)
# ============================================================
KOMISI6_MEMBERS = {
    "Gerindra": [
        "Andre Rosiade", "Khilmi", "Muhammad Husein Fadlulloh",
        "Mulan Jameela", "Kawendra Lukistian", "Unru Baso"
    ],
    "PDI-P": [
        "Adisatrya Suryo Sulisto", "Mufti Anam", "Darmadi Durianto",
        "Rieke Diah Pitaloka", "I Gusti Ngurah Kesuma Kelakan",
        "Sadarestuwati", "Ida Nurlaela", "Budi Sulistyono",
        "G. M. Totok Hedi Santosa"
    ],
    "Golkar": [
        "Nurdin Halid", "Gde Sumarjaya Linggih", "Ahmad Labib",
        "Sarifah Suraidah", "Doni Akbar", "Firnando Hadityo Ganinduto",
        "Rizki Faisal", "Muhammad Sarmuji"
    ],
    "PKB": [
        "Anggia Erma Rini", "Rivqy Abdul Halim", "M. Nasim Khan",
        "Ida Fauziyah", "Imas Aan Ubudiah"
    ],
    "NasDem": [
        "Rachmad Gobel", "Asep Wahyuwijaya", "I Nengah Senantara",
        "Randi Zulmariadi", "Rudi Hartono Bangun", "Subardi"
    ],
    "PKS": [
        "Amin Ak.", "Rizal Bawazier", "Ghufran", "Ismail"
    ],
    "PAN": [
        "Eko Patrio", "Nasril Bahar", "Abdul Hakim Bafagih", "Iskandar"
    ],
    "Demokrat": [
        "Sartono", "Ni Putu Tutik", "Herman Khaeron", "Faujia Helga"
    ]
}

# ============================================================
# 3. HELPER: ANALISIS OTOMATIS PER LEMBAGA
# ============================================================
def generate_analysis(agency, title):
    templates = {
        "Kementerian Perdagangan": {
            "pro": ["Kebijakan berpotensi menstabilkan harga pasok domestik.", "Membuka peluang peningkatan neraca dagang positif."],
            "kontra": ["Risiko distorsi harga jika intervensi pasar tidak tepat sasaran.", "Biasanya menekan marjin pelaku usaha menengah di tahap awal."],
            "tanya": ["Apakah sudah ada mitigasi anti-monopoli pada rantai pasok?", "Bagaimana respon asosiasi pengusaha terkait?"]
        },
        "Danantara": {
            "pro": ["Konsolidasi aset raksasa meningkatkan leverage investasi skala makro.", "Mempermudah pendanaan proyek strategis hilirisasi."],
            "kontra": ["Tumpang tindih yurisdiksi dengan Kementerian BUMN masih rawan terjadi.", "Transparansi audit bagi sovereign wealth fund mendapat sorotan keras."],
            "tanya": ["Bagaimana skema dewan pengawas independen bagi Danantara?"]
        },
        "KPPU": {
            "pro": ["Iklim usaha yang lebih adil bagi pelaku UMKM di tengah gempuran korporasi.", "Mencegah permainan kartel harga strategis."],
            "kontra": ["Investigasi KPPU seringkali memakan waktu bertahun-tahun untuk vonis inkrah.", "Sanksi denda terkadang terlalu kecil bagi konglomerat raksasa."],
            "tanya": ["Adakah kasus monopoli komoditas pokok yang sedang aktif dikawal?"]
        }
    }
    default = {
        "pro": [f"Langkah {agency} membawa efek katalis pada operasional lembaga.", "Sejalan dengan amanat penyelarasan regulasi strategis."],
        "kontra": ["Hambatan birokratis pada tingkat daerah untuk eksekusi putusan ini.", "Potensi kekurangan fleksibilitas karena rigiditas birokrasi."],
        "tanya": ["Sejauh mana efektivitas putusan ini divalidasi?"]
    }
    return templates.get(agency, default)

# ============================================================
# 4. FASE 1: TARIK BERITA LEMBAGA
# ============================================================
def fetch_agency_news():
    results = []
    for agency, query in AGENCIES.items():
        url = f"https://news.google.com/rss/search?q={urllib.parse.quote(query)}&hl=id&gl=ID&ceid=ID:id"
        print(f"  [LEMBAGA] Menarik data: {agency}...")
        try:
            feed = feedparser.parse(url)
            if feed.entries and len(feed.entries) > 0:
                entry = feed.entries[0]
                results.append({
                    "agency": agency,
                    "title": entry.title,
                    "link": entry.link,
                    "published": entry.published,
                    "analysis": generate_analysis(agency, entry.title)
                })
            else:
                results.append({
                    "agency": agency,
                    "title": f"[Sistem Alert] Tidak ada berita terbaru untuk keyword {agency}.",
                    "link": "#",
                    "published": datetime.now().strftime("%a, %d %b %Y %H:%M:%S GMT"),
                    "analysis": generate_analysis(agency, "")
                })
        except Exception as e:
            print(f"  [WARN] Error fetching {agency}: {e}")
            results.append({
                "agency": agency,
                "title": f"[Error] Gagal menarik data: {e}",
                "link": "#",
                "published": datetime.now().strftime("%a, %d %b %Y %H:%M:%S GMT"),
                "analysis": generate_analysis(agency, "")
            })
    return results

# ============================================================
# 5. FASE 2: TARIK BERITA ANGGOTA KOMISI VI
# ============================================================
def fetch_member_news():
    """
    Menarik berita Google News untuk setiap anggota Komisi VI DPR RI.
    Kebal error: jika Google News memblokir atau terjadi timeout,
    proses berlanjut ke anggota berikutnya tanpa crash.
    """
    results = {}
    all_members = [m for members in KOMISI6_MEMBERS.values() for m in members]

    print(f"  Memproses {len(all_members)} anggota Komisi VI...")
    for member in all_members:
        try:
            # Gunakan nama dalam tanda kutip + kata kunci DPR untuk presisi
            query = urllib.parse.quote(f'"{member}" DPR OR Komisi')
            url = f"https://news.google.com/rss/search?q={query}&hl=id&gl=ID&ceid=ID:id"
            feed = feedparser.parse(url)

            if feed.entries and len(feed.entries) > 0:
                entry = feed.entries[0]
                results[member] = {
                    "title": entry.title,
                    "link": entry.link,
                    "published": entry.get("published", "N/A"),
                    "fetched_at": datetime.now().isoformat()
                }
                print(f"  [OK] {member}: {entry.title[:60]}...")
            else:
                print(f"  [INFO] Tidak ada berita untuk: {member}")

        except Exception as e:
            # Kebal error: catat tapi tidak crash
            print(f"  [WARN] Gagal menarik berita untuk {member}: {e}")

        # Jeda 0.5 detik antar request agar tidak diblokir Google
        time.sleep(0.5)

    return results

# ============================================================
# 6. FASE 3: AI DITUTUP
# ============================================================
def generate_ai_briefing(agency_news, member_news):
    return "Sistem intelijen aktif. Fitur AI telah dinonaktifkan."

# ============================================================
# 7. FASE 4: MENGAMBIL DATA PASAR (YFINANCE)
# ============================================================
def fetch_market_data():
    market_data = {
        "live_kurs": 15500,
        "live_minyak": 80
    }
    print("  [MARKET] Mengambil data kurs USD/IDR...")
    try:
        usd_idr = yf.Ticker("IDR=X")
        kurs = usd_idr.history(period="1d")['Close'].iloc[-1]
        market_data["live_kurs"] = int(round(float(kurs)))
        print(f"  [MARKET] Kurs USD/IDR: Rp {market_data['live_kurs']}")
    except Exception as e:
        print(f"  [WARN] Gagal mengambil kurs USD/IDR, menggunakan fallback 15500: {e}")

    print("  [MARKET] Mengambil data harga Minyak WTI...")
    try:
        wti_oil = yf.Ticker("CL=F")
        oil = wti_oil.history(period="1d")['Close'].iloc[-1]
        market_data["live_minyak"] = int(round(float(oil)))
        print(f"  [MARKET] Minyak WTI: USD {market_data['live_minyak']}")
    except Exception as e:
        print(f"  [WARN] Gagal mengambil harga minyak WTI, menggunakan fallback 80: {e}")
        
    return market_data

# ============================================================
# 8. MAIN: GABUNGKAN SEMUA OUTPUT KE live_data.json
# ============================================================
def fetch_data():
    print("=" * 50)
    print("NPCC ENGINE DIMULAI")
    print("=" * 50)

    print("\n>>> FASE 1: Menarik Data Berita Lembaga...")
    agency_news = fetch_agency_news()
    print(f"[FASE 1 SELESAI] {len(agency_news)} lembaga berhasil diproses.")

    print("\n>>> FASE 2: Menarik Berita Anggota Komisi VI...")
    member_news = fetch_member_news()
    print(f"[FASE 2 SELESAI] {len(member_news)} anggota berhasil diproses.")

    print("\n>>> FASE 3: Menghasilkan AI Briefing (Gemini)...")
    ai_briefing = generate_ai_briefing(agency_news, member_news)

    print("\n>>> FASE 4: Menarik Data Pasar (Kurs & Minyak)...")
    market_data = fetch_market_data()

    # Gabungkan ke satu output
    output = {
        "agency_news": agency_news,
        "member_news": member_news,
        "ai_briefing": ai_briefing,
        "live_kurs": market_data.get("live_kurs"),
        "live_minyak": market_data.get("live_minyak"),
        "last_updated": datetime.now().isoformat()
    }

    output_path = os.path.join(os.path.dirname(__file__), 'live_data.json')
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False, indent=4)
        print(f"\n[SUKSES] live_data.json diperbarui pada {datetime.now().strftime('%H:%M:%S WIB')}")
        print(f"         - {len(agency_news)} berita lembaga")
        print(f"         - {len(member_news)} berita anggota")
    except Exception as e:
        print(f"[GAGAL] Error saat menyimpan data: {e}")


if __name__ == "__main__":
    fetch_data()
