# CLAUDE.md — Dashboard Intelijen Komisi IV DPR RI (Fraksi Gerindra)

Dokumen konteks untuk sesi Claude Code. Diperbarui: 5 Juli 2026 (pasca-iterasi
alias + decode link). Proyek bus-factor 1 — jaga dokumen ini tetap akurat.

## 1. TUJUAN

Dashboard statis untuk memantau lanskap informasi seputar Komisi IV DPR RI
(Pertanian, Kehutanan, Kelautan/Perikanan, Ketahanan Pangan) bagi Fraksi
Gerindra: media monitoring mitra kerja (Kementan, Kemenhut, KKP, Barantin,
Bapanas, Bulog) dan 48 anggota Komisi IV per fraksi, plus snapshot statistik
sektoral ber-label kejujuran. Audiens = konteks parlemen (TA/pimpinan, sering
buka dari HP): akurasi & ketertelusuran > fitur.

## 2. ARSITEKTUR

Serverless, static-generated. Tidak ada backend/database/login.

- `engine.py` — Python 3.11: scraper + compiler (Google News RSS + decode link).
- `.github/workflows/auto_update.yml` — cron tiap jam + workflow_dispatch;
  `permissions: contents: write`; dependency dari `requirements.txt`
  (HANYA `feedparser` + `requests`).
- `live_data.json` — single source of truth, di-commit oleh Action.
- `aliases.json` — satu sumber kebenaran alias nama anggota (lihat §5).
- `index.html` — vanilla JS + CSS custom (tanpa Tailwind/framework). Semua
  fitur frontend hidup di file ini.
- Deploy: GitHub Pages. Repo: `kevinpribadi/Gerindra-Komisi-4`.

### Fase engine.py
1. FASE 1 — berita lembaga (6 mitra) → arsip bergulir.
2. FASE 2 — berita anggota (48 orang, query kanonik OR alias) → arsip bergulir.
3. FASE 2b — decode link Google News → `link_resolved` (aman-gagal, budget
   30/run, env `GNEWS_DECODE_BUDGET` untuk override; artikel ter-resolve tidak
   di-decode ulang).
4. FASE 3 — statistik makro: scaffold API BPS/Panel Harga **dimatikan**
   (`ENABLE_PANELHARGA_API = ENABLE_BPS_API = False`); semua nilai FALLBACK
   statis ber-label. JANGAN diaktifkan tanpa instruksi owner.
5. Kompilasi + commit-on-diff → tulis `live_data.json` hanya bila fingerprint
   konten (tanpa `fetched_at`/`last_updated`) berubah; jika sama → log
   `[SKIP]` dan file tidak ditulis (cegah commit kosong tiap jam).

## 3. SKEMA `live_data.json` (aktual)

```json
{
  "agency_news":  { "<lembaga>": [ {title, link, link_resolved, published, fetched_at}, ... ] },
  "member_news":  { "<nama kanonik>": [ {artikel...} ] },
  "harga_beras":  { "value", "source", "source_url", "confidence", "fetched_at" },
  "stok_bulog":   { ...sama... },
  "macro_stats":  { "ntp" | "luas_panen_padi" | ... 9 kunci: {value, source, source_url, confidence, fetched_at} },
  "scrape_status": { "phase_1_agency" | "phase_2_members" | "phase_3_macro": "ok|partial|failed", "errors": [...] },
  "last_updated": "ISO-8601"
}
```

Arsip berita: **array per entitas**, akumulasi lintas run, **dedup by link**
(versi lama menang agar `fetched_at` stabil → fingerprint stabil), **rolling
window 30 hari**, **cap 20 artikel/entitas** (worst-case 54×20=1080 artikel
≈ 620 KB, di bawah ambang 1 MB). `scrape_status` fase 3 berbasis kualitas
data: mode statis disengaja = "ok", bukan "failed".

## 4. ATURAN INTEGRITAS (jangan dilanggar)

1. Label confidence wajib di tiap statistik: `AUDITED` / `UNAUDITED` /
   `FALLBACK` (+`KLAIM_MANAJEMEN` tersedia). FALLBACK tampil ber-badge abu +
   "(acuan statis)" — TIDAK PERNAH tampil seolah data resmi.
2. FALLBACK DILARANG masuk: briefing WhatsApp, panel Analisis, atau konteks
   apa pun yang menghilangkan badge-nya.
3. Tautan sumber pada badge: FALLBACK → teks "sumber acuan ↗" (bukan
   "verifikasi"); UNAUDITED/AUDITED → "lihat sumber ↗"; tanpa `source_url` →
   teks polos tanpa `<a>`.
4. Dilarang mengarang angka/alias. Data tidak ada → katakan tidak ada.
5. Fitur analisis pro/kontra DIHAPUS PERMANEN (keputusan owner). Jangan
   dihidupkan kembali. Semua "analisis" = hitungan volume deterministik dari
   arsip berita nyata — bukan sentimen, bukan LLM.
6. Surface kegagalan: `scrape_status` + banner staleness di UI.

## 5. `aliases.json`

Format: `{ "Nama Kanonik": ["alias", ...] }`; kunci berawalan `_` = catatan.
Dipakai: (1) engine — query scraping anggota kanonik OR alias, hasil disimpan
di nama KANONIK (arsip/cap/dedup tidak pecah per alias); (2) frontend —
penanda "MENYEBUT GERINDRA" mencocokkan kanonik + alias.
Seed satu-satunya: `Siti Hediati Soeharto` → Titiek Soeharto/Titiek Suharto/
Siti Hediati. **47 entri lain masih kosong — DIISI OLEH OWNER.** Jangan
mengarang alias; alias generik satu kata berisiko false-positive.

## 6. FITUR TERPASANG (frontend, semua vanilla JS client-side)

- Pencarian live (berita lembaga/anggota/indikator) + hitung hasil.
- Asisten Data deterministik (bukan LLM; intent lembaga/anggota/indikator/
  status; angka selalu ber-label confidence).
- Ekspor PDF ringkasan 1 halaman (jsPDF, lazy-load CDN saat klik; FALLBACK
  ditandai "(acuan)").
- Briefing WhatsApp (global + per mitra): teks siap-tempel, TANPA statistik
  FALLBACK sama sekali.
- Widget status data (umur data hijau <2j / kuning 2-12j / merah >12j) +
  tombol buka halaman GitHub Actions (tanpa token di client).
- Isu Panas: ranking volume 7 hari (mitra/anggota/topik).
- Share of Voice per fraksi (7/30 hari) + top-5 anggota; Gerindra disorot.
- Sorotan Gerindra otomatis (judul menyebut nama kanonik/alias) + Watchlist
  keyword pribadi (localStorage `k4_watchlist`).
- Filter feed (mitra/tanggal/sort) + tag topik deterministik (`TOPIC_TAGS`
  di atas file, mudah diedit) + deep-link state filter di URL hash +
  "Salin Tautan Tampilan Ini".
- Timeline isu per tag/keyword (kronologis 30 hari, overlay).
- Fokus Mitra: berita + statistik terkait ber-label + mode cetak.
- Panel Analisis: tren volume harian (SVG chart manual), delta 7v7 hari per
  mitra/fraksi, matriks tag×mitra, ekspor CSV (artikel penuh + agregat harian;
  UTF-8 BOM; kolom link = `link_resolved || link`).
- Sumber statistik dapat diklik (lihat §4 poin 3).
- Responsif mobile 375px (tanpa overflow horizontal).

## 7. STATUS & KEPUTUSAN TERTUNDA (butuh owner)

- **API BPS / Panel Harga Bapanas**: scaffold siap di engine, flags False.
  Menunggu approval pimpinan + konfirmasi endpoint (instruksi setup ada di
  komentar engine.py). Sampai itu, statistik = FALLBACK ber-label.
- **Alias anggota**: 47/48 entri kosong menunggu owner (lihat §5).
- **Privasi/akses**: bila kelak dibutuhkan, jalurnya Cloudflare Access —
  gerbang password JS di static site DITOLAK owner (keamanan palsu).
- **Arsip historis >30 hari**: bila analisis tren jangka panjang dibutuhkan,
  pertimbangkan `archive/YYYY-MM.json` terpisah agar `live_data.json` ramping.
- **Decode link**: teknik batchexecute bisa berubah sewaktu-waktu di sisi
  Google. Aman-gagal (link_resolved=null, berita tetap jalan); jika mati
  massal, pertimbangkan disable via `GNEWS_DECODE_BUDGET=0` di workflow.

## 8. CARA KERJA / VERIFIKASI LOKAL

```bash
pip install -r requirements.txt
python engine.py            # run 2x: run kedua harus "[SKIP]" bila tak ada berita baru
python -m http.server 8787  # buka http://localhost:8787
```

Checklist tiap perubahan: console browser bersih; mobile 375px tanpa
overflow; commit-on-diff utuh; tidak ada FALLBACK keluar konteks ber-badge;
angka fitur bisa direkonsiliasi manual dari `live_data.json`. Perubahan
engine.py → picu `workflow_dispatch` dan verifikasi skema dari commit bot
(`git show <sha>:live_data.json`), bukan hanya run lokal.

## 9. LARANGAN TETAP

Vanilla JS (tanpa React/Vue/framework); tanpa backend/DB/login/LLM/secret di
client; `live_data.json` tetap single source of truth; requirements tetap
`feedparser`+`requests`; jangan aktifkan flags API; jangan hidupkan pro/kontra.
