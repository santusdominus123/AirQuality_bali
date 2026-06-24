#!/usr/bin/env python3
"""
news_pipeline.py — Pipeline Berita + Analisis LLM per Kabupaten/Kota
======================================================================

Modul ini menggabungkan scraping berita, data cuaca, dan prediksi PM2.5
untuk menghasilkan analisis kualitas udara berbasis AI per kabupaten di Bali.

Alur kerja satu siklus refresh:
  1. Scrape berita Bali hari ini dari berbagai sumber online
  2. Filter hanya berita bertanggal hari ini
  3. Per kabupaten:
       a. Filter berita yang relevan dengan lokasi itu
       b. Ambil data cuaca dan prediksi PM2.5 dari file data/current.json
       c. Kirim ke LLM untuk dianalisis → ringkasan, faktor, rekomendasi, urgensi
  4. Tulis hasil ke data/news.json untuk ditampilkan di dashboard

Konfigurasi via environment variable:
  LLM_PROVIDER       : provider LLM (default "groq")
  GROQ_API_KEY       : API key Groq (wajib jika pakai Groq)
  GROQ_API_KEYS      : banyak API key dipisah koma (untuk rotasi otomatis)
  LLM_MODEL          : nama model LLM (opsional, default per provider)
  NEWS_SOURCES       : sumber berita aktif dipisah koma (default semua)
  NEWS_MAX_ARTICLES  : batas total artikel (default 150)
"""

import json      # baca/tulis file JSON
import os        # path file dan environment variable
import time      # jeda antar call LLM agar tidak kena rate limit
from datetime import datetime   # timestamp generatedAt
from zoneinfo import ZoneInfo   # timezone WITA

import pandas as pd  # manipulasi DataFrame untuk filter berita

# Import modul internal project
from news_scraper_input import NewsInputModel   # scraper multi-sumber berita Bali
import news_loader                              # filter berita per lokasi kabupaten
from llm_model import AirQualityLLM, CuacaData # LLM analyzer dan data class cuaca

# Path ke folder root project dan folder output data
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "data")

# Timezone Bali (WITA = UTC+8)
TZ = ZoneInfo("Asia/Makassar")

# Provider LLM yang digunakan (bisa diganti via env)
PROVIDER = os.environ.get("LLM_PROVIDER", "groq")

# Ambil API key dari environment — support banyak key dipisah koma untuk rotasi
# jika satu key kena rate limit, otomatis tukar ke key berikutnya
_keys_raw = (os.environ.get("GROQ_API_KEYS") or
             os.environ.get("GROQ_API_KEY") or
             os.environ.get("LLM_API_KEY") or "")
API_KEYS = [k.strip() for k in _keys_raw.split(",") if k.strip()]  # list bersih tanpa spasi
API_KEY = API_KEYS[0] if API_KEYS else ""   # key pertama untuk backward compatibility

# Model LLM yang dipakai — 8b-instant dipilih karena lebih cepat dan rate limit lebih tinggi
# dari 70b, cukup untuk 9 call (satu per kabupaten) per siklus refresh
MODEL = os.environ.get("LLM_MODEL") or "llama-3.1-8b-instant"

# Batas maksimum artikel berita yang di-scrape per siklus
MAX_ARTICLES = int(os.environ.get("NEWS_MAX_ARTICLES", "150"))

# Sumber berita aktif (None = semua sumber aktif)
_sources_env = os.environ.get("NEWS_SOURCES", "").strip()
SOURCES = [s.strip() for s in _sources_env.split(",")] if _sources_env else None

# Daftar kabupaten/kota yang dianalisis — harus cocok dengan nama di current.json
REGENCIES = [
    "Denpasar", "Badung", "Gianyar", "Buleleng", "Jembrana",
    "Karangasem", "Bangli", "Klungkung", "Tabanan",
]


# ==============================================================================
# FUNGSI HELPER
# ==============================================================================

def _category(aqi):
    """
    Konversi nilai AQI numerik ke kategori teks (standar US EPA).

    Digunakan untuk menentukan risk_level yang dikirim ke LLM
    agar analisis bisa menyebut "Tidak Sehat" atau "Berbahaya" secara eksplisit.
    """
    if aqi <= 50:   return "Baik"
    if aqi <= 100:  return "Sedang"
    if aqi <= 150:  return "Tidak Sehat (Populasi Sensitif)"
    if aqi <= 200:  return "Tidak Sehat"
    if aqi <= 300:  return "Sangat Tidak Sehat"
    return "Berbahaya"


def _load(name):
    """
    Muat file JSON dari folder data/.

    Mengembalikan dict kosong jika file belum ada
    (aman dipakai saat server baru pertama kali start).
    """
    path = os.path.join(OUT, name)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _articles_df(articles):
    """
    Ubah list NewsArticle menjadi DataFrame pandas.

    Memastikan semua kolom yang dibutuhkan ada dan bertipe string
    (menghindari error saat filter/manipulasi dengan pandas).
    """
    # Konversi list artikel ke DataFrame; jika kosong buat DataFrame kosong
    df = pd.DataFrame([a.to_dict() for a in articles]) if articles else pd.DataFrame()

    # Pastikan semua kolom yang dibutuhkan ada (isi "" jika tidak ada)
    for col in ["lokasi", "judul", "konten", "kategori", "ringkasan",
                "sumber", "tanggal", "kata_kunci"]:
        if col not in df.columns:
            df[col] = ""            # tambah kolom kosong jika belum ada
        df[col] = df[col].fillna("").astype(str)  # ganti NaN dengan "" dan pastikan string
    return df


# ==============================================================================
# FUNGSI UTAMA
# ==============================================================================

def refresh_news(progress_cb=None):
    """
    Satu siklus refresh berita + analisis LLM untuk semua kabupaten.

    Dipanggil oleh server.py secara berkala (tiap NEWS_REFRESH_MINUTES menit)
    atau saat ada request ke /api/refresh-news.

    Parameter:
        progress_cb: callback opsional untuk progress bar (dipakai jika dipanggil dari UI)

    Return:
        meta (dict): info hasil refresh (waktu, provider, jumlah artikel)

    Raise:
        RuntimeError: jika API key belum di-set
    """
    # Validasi API key sebelum mulai (kecuali Ollama yang jalan lokal)
    if not API_KEY and PROVIDER != "ollama":
        raise RuntimeError("GROQ_API_KEY belum di-set (env)")

    # Muat data terkini dari file JSON yang sudah ada
    current = _load("current.json")       # data polutan dan cuaca per kabupaten
    prediction = _load("prediction.json") # prediksi PM2.5/AQI 24 jam ke depan

    # Buat lookup cepat: nama kabupaten -> data lokasi
    loc_by_name = {l["name"]: l for l in current.get("locations", [])}

    # ── LANGKAH 1: Scrape berita hari ini ──────────────────────────────────────
    scraper = NewsInputModel(
        max_articles=MAX_ARTICLES,  # batas total artikel
        sumber_aktif=SOURCES,       # sumber aktif (None = semua)
        progress_cb=progress_cb,    # callback progress opsional
    )
    articles = scraper.fetch()  # jalankan scraping dari semua sumber aktif

    # Filter hanya berita hari ini (beberapa scraper mungkin ambil kemarin)
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    articles = [a for a in articles if a.tanggal == today]

    # Ubah ke DataFrame untuk kemudahan filter per lokasi
    df = _articles_df(articles)

    # Inisialisasi LLM dengan max_tokens lebih rendah (900) agar lebih hemat kuota
    llm = AirQualityLLM(provider=PROVIDER, api_keys=API_KEYS, model=MODEL, max_tokens=900)

    # ── LANGKAH 2: Analisis per kabupaten ──────────────────────────────────────
    out = {}  # tempat menyimpan hasil analisis per kabupaten
    for idx, name in enumerate(REGENCIES):
        # Jeda 1.2 detik antar call LLM agar tidak kena rate limit
        # (kecuali iterasi pertama)
        if idx:
            time.sleep(1.2)

        # Ambil data lokasi kabupaten ini dari current.json
        loc = loc_by_name.get(name, {})

        # Filter berita yang relevan untuk kabupaten ini (maks 8 berita per kabupaten)
        items, _stats = news_loader.filter_berita_lokasi(df, name, max_berita=8)

        # Susun data cuaca dari current.json untuk kabupaten ini
        cuaca = CuacaData(
            suhu_celsius=loc.get("temp"),                                        # suhu udara dalam Celsius
            kelembaban_persen=loc.get("humidity"),                               # kelembaban relatif (%)
            kecepatan_angin_ms=(loc.get("windSpeed") or 0) / 3.6,               # konversi km/jam → m/s
            kondisi="hujan" if (loc.get("precipitation") or 0) > 0.1 else "cerah berawan",  # kondisi cuaca
        )

        # Ambil prediksi PM2.5 dari prediction.json — ambil nilai maksimum 24 jam ke depan
        pred = prediction.get(name, {})
        pm25_list = pred.get("pm25") or [loc.get("pm25", 0)]  # fallback ke nilai saat ini
        pm25_pred = max(pm25_list) if pm25_list else loc.get("pm25", 0)  # ambil yang terparah

        # Hitung kategori risiko berdasarkan AQI saat ini
        risk = _category(loc.get("aqi", 0))

        # Kumpulkan data polutan saat ini untuk dikirim ke LLM sebagai konteks
        polutan = {k: loc.get(k) for k in ("co", "no2", "o3", "so2", "pm2_5", "pm10", "nh3")}
        polutan["pm2_5"] = loc.get("pm25")         # current.json pakai key "pm25" bukan "pm2_5"
        polutan["dominant"] = loc.get("dominant")  # polutan dominan saat ini

        # Kirim ke LLM untuk dianalisis
        res = llm.analyze(
            items,                  # daftar berita relevan
            lokasi=name,            # nama kabupaten
            cuaca=cuaca,            # data cuaca
            pm25_pred=pm25_pred,    # prediksi PM2.5 tertinggi
            risk_level=risk,        # kategori risiko AQI
            polutan=polutan,        # konsentrasi polutan saat ini
        )

        # Simpan hasil analisis kabupaten ini
        out[name] = {
            "ringkasan":    res.ringkasan_berita,        # paragraf ringkasan situasi
            "faktor":       res.faktor_penyebab,         # list faktor penyebab polusi
            "rekomendasi":  res.rekomendasi_mitigasi,    # list rekomendasi tindakan
            "urgensi":      res.tingkat_urgensi,         # level urgensi: rendah/sedang/tinggi/kritis
            "kelompok":     res.kelompok_rentan,         # kelompok rentan yang perlu diperhatikan
            "beritaCount":  len(items),                  # jumlah berita yang dianalisis
            "sumber": [                                  # daftar sumber berita yang dipakai
                {
                    "judul":   b.judul,
                    "sumber":  b.sumber,
                    "url":     b.url,
                    "tanggal": b.tanggal,
                }
                for b in items
            ],
            "error": res.error,  # None jika sukses, pesan error jika LLM gagal
        }

    # ── LANGKAH 3: Tulis hasil ke data/news.json ───────────────────────────────
    meta = {
        "generatedAt":   datetime.now(TZ).isoformat(timespec="seconds"),  # waktu generate
        "provider":      f"{PROVIDER}/{llm.model}",                        # provider dan model yang dipakai
        "totalArticles": len(articles),                                     # total berita hari ini
    }

    # Buat folder data/ jika belum ada
    os.makedirs(OUT, exist_ok=True)

    # Tulis ke news.json — dibaca oleh app.js untuk menampilkan kartu berita di dashboard
    with open(os.path.join(OUT, "news.json"), "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "regencies": out}, f, ensure_ascii=False)

    # Hitung berapa kabupaten yang berhasil dianalisis (tanpa error)
    ok = sum(1 for v in out.values() if not v["error"])
    print(
        f"[news] {len(articles)} artikel; {ok}/{len(out)} kabupaten dianalisis LLM "
        f"({meta['provider']})",
        flush=True,
    )
    return meta


# ==============================================================================
# ENTRY POINT — Jalankan sekali secara manual
# ==============================================================================

if __name__ == "__main__":
    # Bisa dijalankan langsung: python3 backend/news_pipeline.py
    refresh_news()
