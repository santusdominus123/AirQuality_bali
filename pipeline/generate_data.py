#!/usr/bin/env python3
"""
generate_data.py — Generator JSON Baseline dari Dataset CSV
============================================================

Script ini membaca dataset historis kualitas udara dan menghasilkan
semua file JSON yang dibutuhkan dashboard, sebelum data live tersedia.

File yang dibaca:
  pipeline/source/Data_Final.csv   — data per jam: polutan + cuaca per kabupaten
  pipeline/source/koordinat.json   — peta koordinat kecamatan per kabupaten

File yang dihasilkan (dibaca oleh app.js):
  data/current.json         — snapshot terbaru per kabupaten + koordinat peta
  data/trend.json           — tren AQI/PM2.5/PM10 24 jam terakhir per kabupaten
  data/prediction.json      — prediksi 24 jam ke depan (rata-rata klimatologis)
  data/pollutant_series.json — tren + prediksi per polutan per kabupaten
  data/history.json         — tabel riwayat data historis (48 jam terakhir)
  data/points.json          — semua titik koordinat pantau di peta
  data/accuracy.json        — metrik evaluasi model ML (MAE, RMSE, R²)

Hanya menggunakan standard library Python — tanpa dependencies eksternal.
"""

import csv      # baca file CSV baris per baris
import json     # baca/tulis file JSON
import os       # operasi path dan file system
from collections import defaultdict  # dictionary dengan nilai default otomatis
from datetime import datetime, timedelta  # manipulasi waktu untuk prediksi

# Path ke folder root project (satu level di atas folder pipeline/)
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Folder sumber data (koordinat dan CSV)
SRC = os.path.join(BASE, "pipeline", "source")

# Folder output untuk file JSON hasil generate
OUT = os.path.join(BASE, "data")
os.makedirs(OUT, exist_ok=True)  # buat folder data/ jika belum ada

# Path ke file dataset dan koordinat
CSV_PATH = os.path.join(SRC, "Data_Final.csv")
COORD_PATH = os.path.join(SRC, "koordinat.json")

# Nama tampilan pendek untuk setiap kabupaten/kota (dari nama resmi ke nama singkat)
DISPLAY = {
    "Kota Denpasar":       "Denpasar",
    "Kabupaten Badung":    "Badung",
    "Kabupaten Gianyar":   "Gianyar",
    "Kabupaten Buleleng":  "Buleleng",
    "Kabupaten Jembrana":  "Jembrana",
    "Kabupaten Karangasem":"Karangasem",
    "Kabupaten Bangli":    "Bangli",
    "Kabupaten Klungkung": "Klungkung",
}

# Daftar nama kolom polutan di dataset
POLLUTANTS = ["co", "no", "no2", "o3", "so2", "pm2_5", "pm10", "nh3"]

# Koordinat ibukota/pusat kota per kabupaten — digunakan sebagai posisi marker peta
# Sengaja pakai ibukota (bukan centroid titik) agar marker tidak jatuh ke laut
# (centroid Klungkung misalnya bisa jatuh ke tengah Selat Badung karena Nusa Penida)
CAPITALS = {
    "Kota Denpasar":       (-8.6566, 115.2167),
    "Kabupaten Badung":    (-8.5778, 115.1775),
    "Kabupaten Gianyar":   (-8.5505, 115.3231),
    "Kabupaten Buleleng":  (-8.1120, 115.0891),
    "Kabupaten Jembrana":  (-8.3582, 114.6264),
    "Kabupaten Karangasem":(-8.4538, 115.6120),
    "Kabupaten Bangli":    (-8.4542, 115.3541),
    "Kabupaten Klungkung": (-8.5399, 115.4029),
}


# ==============================================================================
# FUNGSI HELPER
# ==============================================================================

def fnum(value, default=0.0):
    """
    Konversi nilai ke float dengan aman.

    Mengembalikan default jika nilai kosong atau tidak bisa dikonversi.
    Digunakan karena nilai CSV bisa berupa string kosong atau None.
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return default  # kembalikan 0.0 jika konversi gagal


def regency_centroids():
    """
    Hitung titik tengah geografis (centroid) per kabupaten dari koordinat.json.

    Digunakan sebagai fallback posisi marker jika kabupaten tidak ada di CAPITALS.
    Centroid dihitung sebagai rata-rata lat dan lon semua titik pantau di kabupaten itu.

    Return:
        dict: {nama_kabupaten: (lat, lon)}
    """
    with open(COORD_PATH, encoding="utf-8") as f:
        regions = json.load(f)   # baca semua koordinat

    centroids = {}
    for daerah, kecamatans in regions.items():
        lats, lons = [], []
        for points in kecamatans.values():  # loop tiap kecamatan
            for p in points:               # loop tiap titik koordinat
                lats.append(p["lat"])
                lons.append(p["lon"])
        if lats:  # hanya hitung jika ada titik (hindari division by zero)
            centroids[daerah] = (sum(lats) / len(lats), sum(lons) / len(lons))
    return centroids


def load_rows():
    """
    Baca seluruh dataset CSV dan kelompokkan per kabupaten, diurutkan berdasarkan waktu.

    Return:
        dict: {nama_kabupaten: [list_row_dict]} — terurut dari lama ke baru
    """
    by_regency = defaultdict(list)  # dict: key=nama kabupaten, value=list baris

    with open(CSV_PATH, encoding="utf-8") as f:
        for row in csv.DictReader(f):   # baca per baris, header jadi key dict
            by_regency[row["regency"]].append(row)

    # Urutkan tiap kabupaten berdasarkan timestamp dari yang paling lama
    for reg in by_regency:
        by_regency[reg].sort(key=lambda r: r["timestamp_hour"])

    return by_regency


def hour_label(ts):
    """
    Ekstrak label jam "HH:MM" dari string timestamp "YYYY-MM-DD HH:MM:SS".

    Digunakan untuk label sumbu X pada grafik trend dan prediksi.
    """
    return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").strftime("%H:%M")


def relative_age(latest_ts):
    """
    Kembalikan label usia data relatif (misal: "5 menit lalu").

    Karena data ini historis (bukan live), selalu kembalikan "baru saja"
    sebagai teks placeholder agar dashboard tetap tampil informatif.
    """
    return "baru saja"  # data historis — anggap selalu "baru saja"


# ==============================================================================
# FUNGSI BUILD — Bangun tiap file JSON
# ==============================================================================

def build_current(by_regency, centroids):
    """
    Bangun data current.json — snapshot kondisi terkini per kabupaten.

    Mengambil baris terakhir (terbaru) dari tiap kabupaten dan
    memformat semua nilai polutan, cuaca, dan koordinat peta.

    Return:
        list: daftar dict per kabupaten, diurutkan alfabetis berdasarkan nama
    """
    out = []
    for reg, rows in by_regency.items():
        last = rows[-1]  # ambil baris paling baru (terakhir setelah diurutkan)

        # Gunakan koordinat ibukota; fallback ke centroid jika tidak ada
        lat, lon = CAPITALS.get(reg, centroids.get(reg, (None, None)))

        out.append({
            "name":          DISPLAY.get(reg, reg),          # nama tampilan singkat
            "regency":       reg,                             # nama resmi lengkap
            "lat":           lat,                             # latitude marker peta
            "lng":           lon,                             # longitude marker peta
            "timestamp":     last["timestamp_hour"],          # waktu data ini
            "update":        relative_age(last["timestamp_hour"]),  # label "X menit lalu"
            "aqi":           round(fnum(last["aqi_score"])), # nilai AQI (0-500)
            "aqiIndex":      last.get("aqi_index", ""),       # kategori AQI (Baik/Sedang/dll)
            "dominant":      last.get("dominant_pollutant", ""),  # polutan yang dominan
            "pm25":          round(fnum(last["pm2_5"]), 1),   # konsentrasi PM2.5 (µg/m³)
            "pm10":          round(fnum(last["pm10"]), 1),    # konsentrasi PM10 (µg/m³)
            "pm25Max":       round(fnum(last["pm2_5_max"]), 1),  # PM2.5 tertinggi titik
            "pm25Min":       round(fnum(last["pm2_5_min"]), 1),  # PM2.5 terendah titik
            "co":            round(fnum(last["co"]), 1),      # karbon monoksida (µg/m³)
            "no":            round(fnum(last["no"]), 2),      # nitric oxide (µg/m³)
            "no2":           round(fnum(last["no2"]), 2),     # nitrogen dioksida (µg/m³)
            "o3":            round(fnum(last["o3"]), 1),      # ozon (µg/m³)
            "so2":           round(fnum(last["so2"]), 2),     # sulfur dioksida (µg/m³)
            "nh3":           round(fnum(last["nh3"]), 2),     # amonia (µg/m³)
            "temp":          round(fnum(last["temperature_2m"])),         # suhu udara (°C)
            "humidity":      round(fnum(last["relative_humidity_2m"])),   # kelembaban (%)
            "precipitation": round(fnum(last["precipitation"]), 2),       # curah hujan (mm)
            "pressure":      round(fnum(last["surface_pressure"])),        # tekanan udara (hPa)
            "windSpeed":     round(fnum(last["wind_speed_10m"]), 1),       # kecepatan angin (km/jam)
            "windDir":       round(fnum(last["wind_direction_10m"])),      # arah angin (derajat)
        })

    # Urutkan berdasarkan nama tampilan agar konsisten di dashboard
    out.sort(key=lambda d: d["name"])
    return out


def build_trend(by_regency):
    """
    Bangun trend.json — data 24 jam terakhir per kabupaten untuk grafik tren.

    Return:
        dict: {nama_singkat: {labels, aqi, pm25, pm10}}
    """
    trend = {}
    for reg, rows in by_regency.items():
        last24 = rows[-24:]  # ambil 24 baris terakhir (24 jam terakhir)
        trend[DISPLAY.get(reg, reg)] = {
            "labels": [hour_label(r["timestamp_hour"]) for r in last24],  # label jam "HH:MM"
            "aqi":   [round(fnum(r["aqi_score"])) for r in last24],        # nilai AQI per jam
            "pm25":  [round(fnum(r["pm2_5"]), 1) for r in last24],         # PM2.5 per jam
            "pm10":  [round(fnum(r["pm10"]), 1) for r in last24],          # PM10 per jam
        }
    return trend


def build_prediction(by_regency):
    """
    Bangun prediction.json — prediksi 24 jam ke depan menggunakan
    rata-rata klimatologis (pola diurnal historis 30 hari terakhir).

    Metode:
      Untuk setiap jam ke depan (1..24), hitung rata-rata nilai polutan
      pada jam yang sama (hour-of-day) dari 30 hari data historis.
      Ini adalah baseline sederhana yang transparan dan akurat untuk
      pola yang berulang tiap hari (seperti puncak polusi jam macet pagi).

    Return:
        dict: {nama_singkat: {labels, aqi, pm25, pm10}}
    """
    pred = {}
    for reg, rows in by_regency.items():
        # Ambil 30 hari terakhir data (720 jam) untuk menghitung rata-rata per jam-dalam-sehari
        recent = rows[-24 * 30:]

        # Kelompokkan nilai per hour-of-day (0-23)
        buckets = defaultdict(lambda: defaultdict(list))
        for r in recent:
            h = datetime.strptime(r["timestamp_hour"], "%Y-%m-%d %H:%M:%S").hour
            buckets[h]["aqi"].append(fnum(r["aqi_score"]))
            buckets[h]["pm25"].append(fnum(r["pm2_5"]))
            buckets[h]["pm10"].append(fnum(r["pm10"]))

        # Titik waktu mulai prediksi = 1 jam setelah data terbaru
        last_ts = datetime.strptime(rows[-1]["timestamp_hour"], "%Y-%m-%d %H:%M:%S")

        def avg(vals):
            """Rata-rata list angka; kembalikan 0.0 jika kosong."""
            return sum(vals) / len(vals) if vals else 0.0

        labels, aqi, pm25, pm10 = [], [], [], []
        for step in range(1, 25):                     # 24 langkah ke depan
            t = last_ts + timedelta(hours=step)       # waktu target prediksi
            h = t.hour                                # jam dalam sehari (0-23)
            labels.append(t.strftime("%H:%M"))        # label jam
            aqi.append(round(avg(buckets[h]["aqi"])))         # rata-rata AQI jam itu
            pm25.append(round(avg(buckets[h]["pm25"]), 1))    # rata-rata PM2.5 jam itu
            pm10.append(round(avg(buckets[h]["pm10"]), 1))    # rata-rata PM10 jam itu

        pred[DISPLAY.get(reg, reg)] = {
            "labels": labels, "aqi": aqi, "pm25": pm25, "pm10": pm10,
        }
    return pred


def build_pollutant_series(by_regency):
    """
    Bangun pollutant_series.json — data tren + prediksi per polutan per kabupaten.

    Digunakan oleh halaman "Info Polutan" di dashboard untuk menampilkan
    grafik tren dan prediksi semua 8 polutan (CO, NO, NO2, O3, SO2, PM2.5, PM10, NH3).

    Return:
        dict: {nama_singkat: {labelsCurrent, labelsForecast, pollutants: {co: {current, forecast}, ...}}}
    """
    cols = ["co", "no", "no2", "o3", "so2", "pm2_5", "pm10", "nh3"]  # semua polutan
    series = {}

    for reg, rows in by_regency.items():
        last24 = rows[-24:]     # 24 jam terakhir untuk tren aktual
        labels_cur = [hour_label(r["timestamp_hour"]) for r in last24]  # label waktu aktual

        # Siapkan bucket rata-rata per hour-of-day untuk semua polutan
        recent = rows[-24 * 30:]
        buckets = defaultdict(lambda: defaultdict(list))
        for r in recent:
            h = datetime.strptime(r["timestamp_hour"], "%Y-%m-%d %H:%M:%S").hour
            for c in cols:
                buckets[h][c].append(fnum(r[c]))

        last_ts = datetime.strptime(rows[-1]["timestamp_hour"], "%Y-%m-%d %H:%M:%S")

        # Label waktu untuk 24 jam prediksi ke depan
        labels_fc = [(last_ts + timedelta(hours=s)).strftime("%H:%M") for s in range(1, 25)]

        def avg(vals):
            return sum(vals) / len(vals) if vals else 0.0

        def round_for(col, v):
            """Bulatkan 2 desimal untuk gas, 1 desimal untuk partikel."""
            return round(v, 2) if col in ("no", "no2", "so2", "nh3") else round(v, 1)

        # Bangun data current + forecast untuk setiap polutan
        per_pollutant = {}
        for c in cols:
            # Data aktual 24 jam terakhir
            current = [round_for(c, fnum(r[c])) for r in last24]

            # Prediksi 24 jam ke depan berdasarkan rata-rata klimatologis
            forecast = []
            for s in range(1, 25):
                h = (last_ts + timedelta(hours=s)).hour
                forecast.append(round_for(c, avg(buckets[h][c])))

            per_pollutant[c] = {"current": current, "forecast": forecast}

        series[DISPLAY.get(reg, reg)] = {
            "labelsCurrent":  labels_cur,     # label waktu untuk data aktual
            "labelsForecast": labels_fc,      # label waktu untuk prediksi
            "pollutants":     per_pollutant,  # data per polutan
        }
    return series


# Kecamatan ini sebenarnya milik Kabupaten Tabanan tapi di koordinat.json
# salah dimasukkan ke "Kabupaten Gianyar" — disembunyikan agar peta tidak
# menampilkan lokasi Tabanan seolah-olah berada di wilayah Gianyar.
TABANAN_KEC = {
    "Baturiti", "Kediri", "Kerambitan", "Marga", "Penebel", "Pupuan",
    "Selemadeg", "Selemadeg Barat", "Selemadeg Timur", "Tabanan",
}


def is_misfiled(daerah, district):
    """
    Cek apakah kecamatan ini adalah kecamatan Tabanan yang salah masuk ke data Gianyar.

    Return:
        bool: True jika kecamatan harus disembunyikan dari output
    """
    return daerah == "Kabupaten Gianyar" and district in TABANAN_KEC


def build_points():
    """
    Bangun points.json — semua titik koordinat pantau yang ditampilkan di peta.

    Setiap titik mewakili satu lokasi pengukuran (satu titik di kecamatan tertentu).
    Titik Tabanan yang salah masuk ke Gianyar difilter agar peta akurat.

    Return:
        list: [{lat, lng, regency, district, desc}, ...]
    """
    with open(COORD_PATH, encoding="utf-8") as f:
        regions = json.load(f)

    points = []
    for daerah, kecamatans in regions.items():
        name = DISPLAY.get(daerah, daerah)  # nama singkat untuk tampilan
        for district, pts in kecamatans.items():
            if is_misfiled(daerah, district):
                continue    # skip kecamatan Tabanan yang salah masuk Gianyar
            for p in pts:
                points.append({
                    "lat":      p["lat"],                        # latitude titik pantau
                    "lng":      p["lon"],                        # longitude titik pantau
                    "regency":  name,                            # nama kabupaten singkat
                    "district": district,                        # nama kecamatan
                    "desc":     p.get("description", ""),        # deskripsi lokasi
                })
    return points


def build_history(by_regency, hours=48):
    """
    Bangun history.json — riwayat data per jam (48 jam terakhir) untuk tabel histori.

    Menggabungkan data dari semua kabupaten, diurutkan dari yang terbaru.

    Return:
        list: [{location, regency, time, aqi, pm25, dominant, aqiIndex}, ...]
    """
    rows_out = []
    for reg, rows in by_regency.items():
        for r in rows[-hours:]:  # ambil N jam terakhir per kabupaten
            rows_out.append({
                "location":  DISPLAY.get(reg, reg),           # nama singkat
                "regency":   reg,                              # nama resmi
                "time":      r["timestamp_hour"],              # waktu data
                "aqi":       round(fnum(r["aqi_score"])),     # nilai AQI
                "pm25":      round(fnum(r["pm2_5"]), 1),      # nilai PM2.5
                "dominant":  r.get("dominant_pollutant", ""), # polutan dominan
                "aqiIndex":  r.get("aqi_index", ""),          # kategori AQI
            })

    # Urutkan dari yang terbaru ke yang paling lama
    rows_out.sort(key=lambda d: d["time"], reverse=True)
    return rows_out


# Metrik akurasi model ML — diambil dari hasil evaluasi di train.ipynb
# Menampilkan performa terbaik antara RandomForest vs XGBoost per polutan
# berdasarkan nilai RMSE terendah pada test set yang dipegang (held-out)
ACCURACY = {
    "generatedFrom": "train.ipynb (RandomForest vs XGBoost, best per pollutant by RMSE)",
    "models": [
        # Format: polutan, model terbaik, MAE, RMSE, R² (semakin tinggi R² semakin baik)
        {"pollutant": "co",    "model": "Random Forest", "mae": 3.230530, "rmse": 9.265182,  "r2": 0.978166},
        {"pollutant": "no",    "model": "XGBoost",       "mae": 0.002214, "rmse": 0.005719,  "r2": 0.805051},
        {"pollutant": "no2",   "model": "Random Forest", "mae": 0.021022, "rmse": 0.062066,  "r2": 0.959024},
        {"pollutant": "o3",    "model": "Random Forest", "mae": 0.781717, "rmse": 1.745969,  "r2": 0.984892},
        {"pollutant": "so2",   "model": "Random Forest", "mae": 0.008916, "rmse": 0.021745,  "r2": 0.970619},
        {"pollutant": "pm2_5", "model": "Random Forest", "mae": 0.330867, "rmse": 0.857883,  "r2": 0.967864},
        {"pollutant": "pm10",  "model": "Random Forest", "mae": 0.558215, "rmse": 0.955397,  "r2": 0.988816},
        {"pollutant": "nh3",   "model": "XGBoost",       "mae": 0.021602, "rmse": 0.049405,  "r2": 0.905252},
    ],
}


def write(name, obj):
    """
    Tulis objek Python sebagai file JSON ke folder data/.

    Menggunakan separators=(",", ":") untuk menghemat ukuran file
    (tanpa spasi di antara elemen JSON).
    """
    path = os.path.join(OUT, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    print(f"  wrote {name} ({os.path.getsize(path)} bytes)")  # log ukuran file


# ==============================================================================
# ENTRY POINT — Jalankan semua build dan tulis semua file JSON
# ==============================================================================

def main():
    """Jalankan seluruh pipeline: baca CSV → build semua JSON → tulis ke data/."""
    print("Loading coordinates...")
    centroids = regency_centroids()   # hitung centroid tiap kabupaten dari koordinat.json

    print("Loading CSV...")
    by_regency = load_rows()          # baca dan kelompokkan semua baris CSV per kabupaten
    print(f"  {len(by_regency)} regencies, "
          f"{sum(len(v) for v in by_regency.values())} rows total")

    # Timestamp data terbaru dari semua kabupaten
    newest = max(rows[-1]["timestamp_hour"] for rows in by_regency.values())

    # Metadata yang disertakan di semua file JSON
    meta = {
        "generatedAt": datetime.now().isoformat(timespec="seconds"),  # waktu generate
        "dataLatest":  newest,                                         # timestamp data terbaru
        "regencies":   len(by_regency),                               # jumlah kabupaten
    }

    print("Building outputs...")

    # Tulis semua file JSON — urutan penting: current harus duluan karena yang lain bergantung
    write("current.json",          {"meta": meta, "locations": build_current(by_regency, centroids)})
    write("trend.json",            build_trend(by_regency))
    write("prediction.json",       build_prediction(by_regency))
    write("pollutant_series.json", build_pollutant_series(by_regency))
    write("history.json",          {"meta": meta, "rows": build_history(by_regency)})
    write("points.json",           {"points": build_points()})
    write("accuracy.json",         ACCURACY)

    print("Done.")


if __name__ == "__main__":
    main()
