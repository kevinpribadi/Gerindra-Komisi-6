# CLAUDE.md — Dashboard Intelijen Komisi VI DPR RI (Fraksi Gerindra)

Dokumen konteks untuk sesi Claude Code. Diperbarui: 5 Juli 2026 (hasil port
dari template Komisi IV). Proyek bus-factor 1 — jaga dokumen ini tetap akurat.

## 1. TUJUAN

Dashboard statis untuk memantau lanskap informasi seputar Komisi VI DPR RI
(BUMN, Perdagangan, Investasi, Koperasi/UMKM, Persaingan Usaha) bagi Fraksi
Gerindra: media monitoring mitra kerja (Kementerian BUMN, Kemendag, Kemenkop,
Danantara, BPKN, KPPU, BP Batam, BPK Sabang, Dekopin, Badan Pengaturan BUMN)
dan 46 anggota Komisi VI per fraksi, plus snapshot statistik ekonomi ber-label
kejujuran. Audiens = konteks parlemen (TA/pimpinan, sering buka dari HP):
akurasi & ketertelusuran > fitur.

## 2. ARSITEKTUR

Serverless, static-generated. Tidak ada backend/database/login. Diporting
UTUH dari template Komisi IV (repo `kevinpribadi/Gerindra-Komisi-4`) — semua
logika arsip/decode/commit-on-diff/fitur frontend identik; yang ditukar hanya
data domain.

- `engine.py` — Python 3.11: scraper + compiler (Google News RSS + decode link).
- `.github/workflows/auto_update.yml` — cron tiap jam + workflow_dispatch;
  `permissions: contents: write`; dependency dari `requirements.txt`
  (`feedparser` + `requests` + `yfinance`).
- `live_data.json` — single source of truth, di-commit oleh Action.
- `aliases.json` — satu sumber kebenaran alias nama anggota (lihat §5).
- `index.html` — vanilla JS + CSS custom (tanpa Tailwind/framework). Semua
  fitur frontend hidup di file ini.
- Deploy: GitHub Pages. Repo: `kevinpribadi/Gerindra-Komisi-6`.

### Fase engine.py
1. FASE 1 — berita lembaga (10 mitra) → arsip bergulir.
2. FASE 2 — berita anggota (46 orang, query kanonik OR alias) → arsip bergulir.
3. FASE 2b — decode link Google News → `link_resolved` (aman-gagal, budget
   30/run, env `GNEWS_DECODE_BUDGET` untuk override; artikel ter-resolve tidak
   di-decode ulang).
4. FASE 3 — statistik (SEMUA data pasar yfinance, saklar `ENABLE_MARKET_DATA`):
   (a) Kurs USD/IDR (IDR=X) + IHSG (^JKSE): sukses → `UNAUDITED` + sumber
   "Yahoo Finance (data pasar, bukan rilis resmi)"; gagal → `FALLBACK` statis
   ber-label. (b) SAHAM 10 BUMN (`BUMN_TICKERS`, acuan papan IDXBUMN20 di
   bumn.go.id/portofolio/informasi-saham; keputusan owner 6 Jul 2026
   menggantikan indikator makro lama — realisasi investasi bukan ranah
   Komisi VI): sukses → `UNAUDITED` + `source_url` Google Finance emiten;
   gagal → value null ("Data tidak tersedia") + `FALLBACK`, TANPA harga
   statis. Scaffold API resmi BPS/BKPM/Kemenkop DIHAPUS (pulihkan dari
   commit 4fed607 bila dibutuhkan lagi).
5. Kompilasi + commit-on-diff → tulis `live_data.json` hanya bila fingerprint
   konten (tanpa `fetched_at`/`last_updated`) berubah; jika sama → log
   `[SKIP]` dan file tidak ditulis (cegah commit kosong tiap jam).
6. SNAPSHOT BULANAN — `archive/monthly/YYYY-MM.json`: agregat hitungan
   (party/agency/topic/member/early-warning counts + 15 top_headlines),
   BUKAN duplikasi arsip. Ditulis HANYA saat `live_data.json` ikut ditulis
   → tidak pernah memicu commit sendiri (commit-on-diff utuh). Aman-gagal.
   `SNAPSHOT_TOPIC_TAGS`/`SNAPSHOT_EW_KEYWORDS` di engine.py WAJIB dijaga
   sinkron manual dengan `TOPIC_TAGS`/`EW_KEYWORDS` di index.html.

## 3. SKEMA `live_data.json` (aktual)

```json
{
  "agency_news":  { "<lembaga>": [ {title, link, link_resolved, published, fetched_at}, ... ] },
  "member_news":  { "<nama kanonik>": [ {artikel...} ] },
  "kurs_usd_idr": { "value", "source", "source_url", "confidence", "fetched_at" },
  "ihsg":         { ...sama... },
  "bumn_stocks":  { "<KODE IDX>": {name, value, source, source_url(Google Finance), confidence, fetched_at} },
  "scrape_status": { "phase_1_agency" | "phase_2_members" | "phase_3_macro": "ok|partial|failed", "errors": [...] },
  "last_updated": "ISO-8601"
}
```

Arsip berita: **array per entitas**, akumulasi lintas run, **dedup by link**
(versi lama menang agar `fetched_at` stabil → fingerprint stabil), **rolling
window 30 hari**, **cap 20 artikel/entitas** (worst-case 56×20=1120 artikel,
di bawah ambang 1 MB). `scrape_status` fase 3 berbasis kualitas data: target
live = ticker yfinance (default aktif); scaffold resmi yang dimatikan
disengaja tidak dihitung gagal.

## 4. ATURAN INTEGRITAS (jangan dilanggar)

1. Label confidence wajib di tiap statistik TERMASUK data pasar: `AUDITED` /
   `UNAUDITED` / `FALLBACK` (+`KLAIM_MANAJEMEN` tersedia). Data pasar Yahoo
   maksimal `UNAUDITED` (bukan rilis resmi BI/BEI). FALLBACK tampil ber-badge
   abu + "(acuan statis)" — TIDAK PERNAH tampil seolah data resmi/live.
2. FALLBACK DILARANG masuk: briefing WhatsApp, panel Analisis, atau konteks
   apa pun yang menghilangkan badge-nya.
3. Tautan sumber pada badge: FALLBACK → teks "sumber acuan ↗" (bukan
   "verifikasi"); UNAUDITED/AUDITED → "lihat sumber ↗"; tanpa `source_url` →
   teks polos tanpa `<a>`.
4. Dilarang mengarang angka/alias/roster. Data tidak ada → katakan tidak ada.
5. Fitur analisis pro/kontra & `ai_briefing` DIHAPUS PERMANEN (ada di file
   pra-kerja Komisi VI lama — commit baseline 1553025 — jangan dihidupkan
   kembali). Semua "analisis" = hitungan volume deterministik dari arsip
   berita nyata — bukan sentimen, bukan LLM.
6. Surface kegagalan: `scrape_status` + banner staleness di UI.

## 5. `aliases.json`

Format: `{ "Nama Kanonik": ["alias", ...] }`; kunci berawalan `_` = catatan.
Dipakai: (1) engine — query scraping anggota kanonik OR alias, hasil disimpan
di nama KANONIK; (2) frontend — penanda "MENYEBUT GERINDRA" mencocokkan
kanonik + alias. Seed satu-satunya: `Eko Patrio` → [Eko Hendro Purnomo]
(nama resmi DPR; Eko Patrio = nama panggung yang dipakai media).
**45 entri lain masih kosong — DIISI OLEH OWNER.** Nama kanonik satu kata
(Khilmi, Mulyadi, Ghufran, Ismail, Iskandar, Subardi, Sartono) rawan
false-positive; jangan tambah alias generik.

## 6. FITUR TERPASANG (frontend, semua vanilla JS client-side)

Identik dengan Komisi IV (diporting verbatim):
- Pencarian live + hitung hasil; Asisten Data deterministik (bukan LLM);
  Ekspor PDF ringkasan (jsPDF lazy-load); Briefing WhatsApp global & per
  mitra (TANPA statistik FALLBACK); Widget status data + tombol GitHub
  Actions; Isu Panas 7 hari; Share of Voice per fraksi + top-5 anggota
  (Gerindra disorot); Sorotan Gerindra + Watchlist (localStorage
  `k6_watchlist`); Filter feed + tag topik (`TOPIC_TAGS` di atas file,
  owner-tunable) + deep-link hash + salin tautan; Timeline isu; Fokus Mitra +
  cetak; Panel Analisis (tren SVG, delta 7v7, matriks tag×mitra, ekspor CSV);
  Sumber statistik dapat diklik; Responsif mobile 375px.

Tambahan Komisi VI — **Intelijen Bulanan Fraksi** (`#intel-block`, lapisan
internal "bahan pimpinan/bahan rapat", SEMUA deterministik dari arsip berita,
TANPA statistik FALLBACK, data kurang → "data tidak cukup"):
Scorecard fraksi (SoV/peringkat/volume/delta Naik-Turun-Stabil); Pembanding
antarfraksi (baris Gerindra disorot); Peta paparan isu tag×fraksi (toggle
7/30/bulan berjalan); Sinyal Perhatian 5 grup keyword judul (`EW_KEYWORDS`
owner-tunable; kata pendek ambigu demo/kur/bpk/phk/kpk match per-kata utuh);
Buat Brief Rapat (Salin/WA/TXT/Cetak, bank pertanyaan RDP template);
Unduh Laporan Bulanan TXT ber-disclaimer. Bukan sentimen, bukan LLM,
bukan pro/kontra — hanya hitungan volume + bukti headline.

## 7. STATUS & KEPUTUSAN TERTUNDA (butuh owner)

- **Roster Komisi VI**: divalidasi 5 Jul 2026 (fraksigerindra.id: Gerindra 7
  anggota termasuk Mulyadi; Demokrat: Tutik Kusuma Wardhani pindah Komisi IX
  Feb 2025, pengganti belum terkonfirmasi; Golkar: sumber bertentangan).
  Owner konfirmasi final.
- **Indikator pasar**: kurs USD/IDR + IHSG + saham 10 BUMN (`BUMN_TICKERS`,
  owner boleh ganti daftar emiten); CPO/Brent tersedia sebagai opsi
  (`MARKET_TICKERS`). Indikator makro lama (investasi/neraca/koperasi/UMKM)
  DICABUT owner 6 Jul 2026 — scaffold API resminya dihapus dari kode.
- **Alias anggota**: 45/46 entri kosong menunggu owner (lihat §5).
- **Keyword topik** (`TOPIC_TAGS`): seed domain BUMN/dagang/investasi/koperasi;
  owner boleh tambah/ubah.
- **Privasi/akses**: bila kelak dibutuhkan, jalurnya Cloudflare Access —
  gerbang password JS di static site DITOLAK (keamanan palsu).
- **Arsip historis >30 hari**: bila perlu, pertimbangkan `archive/YYYY-MM.json`.
- **Decode link**: teknik batchexecute bisa berubah di sisi Google. Aman-gagal;
  jika mati massal, pertimbangkan `GNEWS_DECODE_BUDGET=0` di workflow.

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
`feedparser`+`requests`+`yfinance`; data pasar maksimal `UNAUDITED`; jangan
hidupkan pro/kontra maupun `ai_briefing`.
