#!/usr/bin/env python3
"""
predict_models.py — Modul Prediksi Kualitas Udara 24 Jam ke Depan
==================================================================

Modul ini bertugas menghasilkan prediksi kadar polutan udara untuk 24 jam
ke depan menggunakan model Machine Learning yang sudah dilatih sebelumnya.

Alur kerja:
  1. Muat model ML yang sudah disimpan (format .joblib) dari folder model
  2. Siapkan fitur-fitur input dari data historis (lag, rolling average, waktu)
  3. Jalankan prediksi per polutan per kabupaten/kota
  4. Kembalikan hasil prediksi ke pemanggil (live_pipeline.py)

Polutan yang didukung: CO, NO, NO2, O3, SO2, PM2.5, PM10, NH3

Catatan: Model hanya diload sekali lalu disimpan di cache (_cache) agar
         tidak perlu baca ulang file setiap ada request prediksi.
"""

import json       # untuk membaca file metadata.json
import os         # untuk operasi path dan cek keberadaan file
import warnings   # untuk menyembunyikan peringatan saat load model

import numpy as np    # operasi matematika array (misal: np.maximum untuk pastikan nilai >= 0)
import pandas as pd   # manipulasi data tabular (DataFrame)
import joblib         # load model ML yang sudah disimpan dalam format .joblib


# ==============================================================================
# KONSTANTA — Konfigurasi global yang dipakai di seluruh modul
# ==============================================================================

# Daftar nama kolom polutan yang akan diprediksi
POLLUTANT_COLS = ["co", "no", "no2", "o3", "so2", "pm2_5", "pm10", "nh3"]

# Lag (selisih waktu ke belakang) dalam jam yang dijadikan fitur input model
# Contoh: co_lag_1 = nilai CO 1 jam lalu, co_lag_24 = nilai CO kemarin jam yang sama
LAG_HOURS = [1, 3, 6, 12, 24]

# Ukuran jendela (window) untuk menghitung rata-rata bergerak (rolling average)
# Contoh: co_rolling_avg_6 = rata-rata CO dari 6 jam terakhir
ROLLING_WINDOWS = [3, 6, 24]

# Jumlah desimal untuk pembulatan hasil prediksi per polutan
# Polutan gas (NO, NO2, SO2, NH3) dibulatkan 2 desimal; lainnya 1 desimal
DECIMALS = {"no": 2, "no2": 2, "so2": 2, "nh3": 2}

# Cache in-memory: menyimpan model dan daftar fitur agar tidak perlu load ulang dari disk
# Diisi pertama kali load_models() dipanggil, lalu dipakai terus selama server berjalan
_cache = {"models": None, "features": None}


# ==============================================================================
# FUNGSI 1: load_models — Muat model ML dari disk (dengan caching)
# ==============================================================================

def load_models(model_dir):
    """
    Muat semua model ML yang tersedia dari folder model_dir.

    Model disimpan dalam format .joblib, satu file per polutan.
    Fungsi ini hanya membaca dari disk SEKALI — setelah itu hasil disimpan
    di _cache dan langsung dikembalikan tanpa baca ulang (lebih efisien).

    Parameter:
        model_dir (str): Path ke folder berisi file .joblib dan metadata.json

    Return:
        models   (dict): {nama_polutan: objek_model}, hanya polutan yang file-nya ada
        features (list): Daftar nama fitur yang dibutuhkan model saat prediksi
    """

    # Jika model sudah pernah dimuat sebelumnya, langsung kembalikan dari cache
    # (tidak perlu baca disk lagi — menghemat waktu dan I/O)
    if _cache["models"] is not None:
        return _cache["models"], _cache["features"]

    # Buka file metadata.json yang berisi info fitur dan daftar polutan
    with open(os.path.join(model_dir, "metadata.json"), encoding="utf-8") as f:
        meta = json.load(f)  # parse JSON menjadi dictionary Python

    # Ambil daftar nama fitur yang dipakai saat training model
    features = meta["feature_names"]

    # Siapkan dictionary kosong untuk menampung model yang berhasil dimuat
    models = {}

    # Loop tiap polutan yang terdaftar di metadata
    for pollutant in meta["pollutant_cols"]:

        # Bentuk path lengkap ke file model polutan ini
        # Contoh: /path/to/models/co_best_model.joblib
        path = os.path.join(model_dir, f"{pollutant}_best_model.joblib")

        # Hanya load jika file model-nya memang ada di disk
        if os.path.exists(path):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")   # sembunyikan peringatan versi sklearn
                models[pollutant] = joblib.load(path)  # muat model ke memori

    # Simpan hasil ke cache agar tidak perlu load ulang di pemanggilan berikutnya
    _cache["models"], _cache["features"] = models, features

    # Cetak info ke terminal: polutan apa saja yang berhasil dimuat
    print(f"[models] loaded {sorted(models)} ({len(features)} features)", flush=True)

    return models, features


# ==============================================================================
# FUNGSI HELPER: _season — Tentukan musim berdasarkan bulan
# ==============================================================================

def _season(month):
    """
    Klasifikasi musim di Bali berdasarkan bulan.

    Return:
        0 = musim kemarau  (April–Oktober, bulan 4–10)
        1 = musim hujan    (November–Maret, bulan 11–3)

    Fitur musim ini membantu model memahami pola polutan yang
    berbeda antara musim kemarau dan musim hujan.
    """
    return 0 if month in (4, 5, 6, 7, 8, 9, 10) else 1


# ==============================================================================
# FUNGSI 2: build_features — Siapkan semua fitur input untuk model ML
# ==============================================================================

def build_features(records, features):
    """
    Ubah data historis mentah menjadi tabel fitur siap pakai untuk prediksi.

    Fitur yang dibuat:
      - Fitur waktu    : jam, hari, bulan, hari-dalam-minggu, is_weekend, musim
      - Fitur lag      : nilai polutan X jam yang lalu (1, 3, 6, 12, 24 jam)
      - Rolling average: rata-rata polutan dalam jendela 3, 6, 24 jam terakhir
      - One-hot regency: kolom biner per kabupaten/kota (misal: regency_badung = 1/0)

    Parameter:
        records  (list of dict): Data historis per jam, tiap record berisi
                                 timestamp_hour, regency, nilai polutan, dan cuaca
        features (list of str) : Nama-nama fitur yang dibutuhkan model

    Return:
        df (DataFrame): Tabel fitur lengkap siap dipakai model.predict()
    """

    # Ubah list of dict menjadi DataFrame pandas untuk kemudahan manipulasi
    df = pd.DataFrame(records)

    # Pastikan kolom waktu bertipe datetime agar bisa diekstrak jam/hari/bulannya
    df["timestamp_hour"] = pd.to_datetime(df["timestamp_hour"])

    # Urutkan data berdasarkan wilayah lalu waktu — penting agar lag dihitung dengan benar
    df = df.sort_values(["regency", "timestamp_hour"]).reset_index(drop=True)

    # --- Fitur Temporal (berbasis waktu) ---
    df["hour"]      = df["timestamp_hour"].dt.hour       # jam dalam sehari (0–23)
    df["day"]       = df["timestamp_hour"].dt.day        # tanggal dalam bulan (1–31)
    df["month"]     = df["timestamp_hour"].dt.month      # bulan (1–12)
    df["dayofweek"] = df["timestamp_hour"].dt.dayofweek  # hari dalam minggu (0=Senin, 6=Minggu)
    df["is_weekend"] = df["dayofweek"].isin([5, 6]).astype(int)  # 1 jika Sabtu/Minggu, 0 jika tidak
    df["season"]    = df["month"].apply(_season)         # 0=kemarau, 1=hujan

    # --- Fitur Lag (nilai polutan di masa lalu) ---
    # Untuk setiap polutan dan setiap jarak waktu, buat kolom baru
    # Pengelompokan per regency agar lag tidak "bocor" antar wilayah yang berbeda
    for col in POLLUTANT_COLS:
        for lag in LAG_HOURS:
            # Contoh: co_lag_6 = nilai CO dari 6 jam sebelumnya, per wilayah
            df[f"{col}_lag_{lag}"] = df.groupby("regency")[col].shift(lag)

    # --- Fitur Rolling Average (rata-rata bergerak) ---
    # shift(1) dulu agar nilai saat ini tidak ikut dihitung (hindari data leakage)
    for col in POLLUTANT_COLS:
        for window in ROLLING_WINDOWS:
            # Contoh: pm2_5_rolling_avg_24 = rata-rata PM2.5 dari 24 jam terakhir
            df[f"{col}_rolling_avg_{window}"] = (
                df.groupby("regency")[col]
                .transform(lambda x: x.shift(1).rolling(window, min_periods=1).mean())
            )

    # --- Fitur One-Hot Encoding untuk Kabupaten/Kota (Regency) ---
    # Simpan nama asli regency sebelum diubah menjadi kolom biner
    df["regency_original"] = df["regency"]

    # Buat kolom biner per wilayah (1 jika data dari wilayah itu, 0 jika tidak)
    # Contoh: regency_badung = 1 untuk baris dari Badung, 0 untuk baris dari wilayah lain
    for col in features:
        if col.startswith("regency_"):
            name = col[len("regency_"):]               # ambil nama wilayah dari nama kolom
            df[col] = (df["regency_original"] == name).astype(int)

    # --- Pastikan Semua Kolom yang Dibutuhkan Model Ada ---
    # Jika ada fitur yang belum ada di DataFrame, isi dengan 0
    # (misalnya fitur wilayah yang tidak ada di data saat ini)
    for col in features:
        if col not in df.columns:
            df[col] = 0

    # --- Tangani Nilai Kosong (NaN) ---
    # Nilai lag/rolling di baris-baris awal akan NaN karena tidak ada data sebelumnya
    # Isi dengan rata-rata kolom tersebut; jika rata-ratanya pun NaN, isi 0
    numeric = df.select_dtypes(include=[np.number]).columns  # ambil hanya kolom angka
    df[numeric] = df[numeric].fillna(df[numeric].mean()).fillna(0)

    return df


# ==============================================================================
# FUNGSI 3: predict — Fungsi utama yang dipanggil dari luar modul ini
# ==============================================================================

def predict(records, model_dir):
    """
    Hasilkan prediksi kualitas udara 24 jam ke depan untuk semua wilayah.

    Fungsi ini adalah entry point utama modul ini. Dipanggil oleh live_pipeline.py
    setiap kali ada data baru yang perlu diprediksi.

    Alur:
      1. Load model ML (dari cache atau disk)
      2. Bangun fitur dari data historis
      3. Untuk setiap wilayah, ambil 24 baris terakhir dan jalankan prediksi
      4. Kembalikan hasil dalam format yang siap ditampilkan di dashboard

    Parameter:
        records   (list of dict): Data historis per jam per wilayah
        model_dir (str)         : Path ke folder berisi model .joblib

    Return:
        forecasts (dict): {nama_wilayah: {polutan: [24 nilai prediksi]}}
                          Contoh: {"Badung": {"co": [0.3, 0.4, ...], "pm2_5": [12.1, ...]}}
        labels    (dict): {nama_wilayah: ["00:00", "01:00", ..., "23:00"]}
                          Label waktu untuk 24 jam ke depan (jam target prediksi)
        available (list): Daftar polutan yang berhasil diprediksi oleh model
                          (hanya polutan yang file model-nya tersedia)
    """

    # Muat model dan daftar fitur (dari cache jika sudah pernah dimuat)
    models, features = load_models(model_dir)

    # Jika tidak ada model yang tersedia sama sekali, kembalikan hasil kosong
    if not models:
        return {}, {}, []

    # Bangun tabel fitur lengkap dari data historis mentah
    df = build_features(records, features)

    # Siapkan tempat menyimpan hasil prediksi dan label waktu
    forecasts, labels = {}, {}

    # Loop tiap wilayah (kabupaten/kota) secara terpisah
    for regency, group in df.groupby("regency_original"):

        # Ambil 24 data terakhir per wilayah (1 hari terakhir, diurutkan dari lama ke baru)
        group = group.sort_values("timestamp_hour").tail(24)

        # Ambil hanya kolom fitur yang dibutuhkan model (urutan harus sama persis dengan training)
        X = group[features]

        # Hitung label waktu prediksi: timestamp data + 24 jam ke depan
        future = group["timestamp_hour"] + pd.Timedelta(hours=24)

        # Format label waktu menjadi string "HH:MM" untuk ditampilkan di grafik dashboard
        labels[regency] = [t.strftime("%H:%M") for t in future]

        # Jalankan prediksi untuk setiap polutan yang modelnya tersedia
        preds = {}
        for pollutant, model in models.items():
            decimals = DECIMALS.get(pollutant, 1)           # tentukan jumlah desimal pembulatan
            values = np.maximum(model.predict(X), 0)        # prediksi, paksa nilai minimum = 0 (tidak boleh negatif)
            preds[pollutant] = [round(float(v), decimals) for v in values]  # bulatkan dan simpan sebagai list

        # Simpan hasil prediksi semua polutan untuk wilayah ini
        forecasts[regency] = preds

    # Kembalikan: prediksi, label waktu, dan daftar polutan yang tersedia
    return forecasts, labels, list(models.keys())
