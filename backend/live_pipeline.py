#!/usr/bin/env python3
"""
live_pipeline.py — Pipeline Data Kualitas Udara Real-Time
==========================================================

Modul ini mengambil data kualitas udara dan cuaca secara live dari API eksternal,
mengolahnya, lalu menulis hasilnya ke file JSON yang dibaca oleh dashboard.

Alur kerja satu siklus refresh:
  1. Baca koordinat.json → ambil titik sampel per kecamatan
  2. Fetch data polutan 25 jam terakhir dari OpenWeather API (paralel per titik)
  3. Fetch data cuaca 25 jam terakhir dari Open-Meteo API (batch per 50 titik)
  4. Rata-rata: titik → kecamatan → kabupaten/kota (equal-district mean)
  5. Hitung AQI standar US EPA per kabupaten per jam
  6. Jalankan prediksi model ML jika model .joblib tersedia
  7. Tulis ulang: data/current.json, data/trend.json, data/pollutant_series.json,
     dan (jika model ada) data/prediction.json

Hanya butuh: requests (HTTP) + standard library.
"""

import os              # path file dan environment variable
import json            # baca/tulis file JSON
import time            # time.sleep untuk retry dan jeda
from collections import defaultdict          # dict dengan nilai default
from concurrent.futures import ThreadPoolExecutor, as_completed  # fetch paralel
from datetime import datetime, timedelta     # manipulasi waktu
from zoneinfo import ZoneInfo               # timezone WITA

import requests  # HTTP client untuk panggil OpenWeather dan Open-Meteo

# ── Path ──────────────────────────────────────────────────────────────────────
# Dua level di atas backend/ = root project
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Folder sumber: koordinat dan model ML
SRC = os.path.join(BASE, "pipeline", "source")

# Folder output: file JSON yang dibaca dashboard
OUT = os.path.join(BASE, "data")

# Path ke file koordinat kecamatan
COORD_PATH = os.path.join(SRC, "koordinat.json")

# Path ke folder model ML yang sudah ditraining
MODEL_DIR = os.path.join(SRC, "saved_best_models")

# Gunakan model ML jika folder tersedia (bisa dinonaktifkan via env USE_MODELS=0)
USE_MODELS = os.environ.get("USE_MODELS", "1") != "0"

# ── Konfigurasi API ───────────────────────────────────────────────────────────
# API key OpenWeather — bisa di-set via env OPENWEATHER_API_KEY
def get_api_key():
    return os.environ.get("OPENWEATHER_API_KEY", "")

# URL endpoint OpenWeather untuk data polutan historis
OPENWEATHER_AIR_URL = "http://api.openweathermap.org/data/2.5/air_pollution/history"

# URL endpoint Open-Meteo untuk data cuaca historis + forecast
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# Timezone Bali
TIMEZONE = "Asia/Makassar"
TZ = ZoneInfo(TIMEZONE)

# ── Konfigurasi Sampling ──────────────────────────────────────────────────────
# Jumlah titik koordinat yang di-fetch per kecamatan
# Lebih tinggi = lebih akurat tapi lebih banyak API call

# Jumlah thread paralel untuk fetch data polutan (lebih banyak = lebih cepat)
AIR_WORKERS = int(os.environ.get("AIR_WORKERS", "6"))

# Jumlah titik per batch untuk request Open-Meteo (max 50 per request)
WEATHER_BATCH = 50

# Timeout HTTP request dalam detik
REQUEST_TIMEOUT = 30

# Maksimum percobaan ulang jika request gagal
MAX_RETRIES = 4

# ── Konstanta Data ────────────────────────────────────────────────────────────
# Daftar polutan yang diambil dari API
POLLUTANTS = ["co", "no", "no2", "o3", "so2", "pm2_5", "pm10", "nh3"]

# Kecamatan Tabanan yang salah dimasukkan ke "Kabupaten Gianyar" di koordinat.json
# Titik-titik ini di-skip saat fetch agar AQI Gianyar tidak tercampur data Tabanan
TABANAN_KEC = {
    "Baturiti", "Kediri", "Kerambitan", "Marga", "Penebel", "Pupuan",
    "Selemadeg", "Selemadeg Barat", "Selemadeg Timur", "Tabanan",
}

# Kolom cuaca yang diambil dari Open-Meteo
WEATHER_COLS = [
    "temperature_2m",        # suhu udara 2 meter di atas tanah (°C)
    "relative_humidity_2m",  # kelembaban relatif 2m (%)
    "precipitation",         # curah hujan (mm)
    "surface_pressure",      # tekanan udara permukaan (hPa)
    "wind_speed_10m",        # kecepatan angin 10m (km/jam)
    "wind_direction_10m",    # arah angin 10m (derajat)
]

# Mapping nama resmi → nama singkat untuk tampilan
DISPLAY = {
    "Kota Denpasar":        "Denpasar",
    "Kabupaten Badung":     "Badung",
    "Kabupaten Gianyar":    "Gianyar",
    "Kabupaten Buleleng":   "Buleleng",
    "Kabupaten Jembrana":   "Jembrana",
    "Kabupaten Karangasem": "Karangasem",
    "Kabupaten Bangli":     "Bangli",
    "Kabupaten Klungkung":  "Klungkung",
    "Kabupaten Tabanan":    "Tabanan",
}

# Koordinat ibukota kabupaten untuk posisi marker peta (bukan centroid)
# Centroid bisa jatuh ke laut untuk kabupaten dengan pulau (mis. Klungkung + Nusa Penida)
CAPITALS = {
    "Kota Denpasar":        (-8.6566, 115.2167),
    "Kabupaten Badung":     (-8.5778, 115.1775),
    "Kabupaten Gianyar":    (-8.5505, 115.3231),
    "Kabupaten Buleleng":   (-8.1120, 115.0891),
    "Kabupaten Jembrana":   (-8.3582, 114.6264),
    "Kabupaten Karangasem": (-8.4538, 115.6120),
    "Kabupaten Bangli":     (-8.4542, 115.3541),
    "Kabupaten Klungkung":  (-8.5399, 115.4029),
    "Kabupaten Tabanan":    (-8.5410, 115.1240),
}


# ==============================================================================
# PERHITUNGAN AQI (US EPA)
# Sumber: EPA AQI Technical Assistance Document
# ==============================================================================

# Berat molekul polutan (g/mol) — untuk konversi satuan µg/m³ → ppb/ppm
MW = {"co": 28.01, "no2": 46.0055, "o3": 48.00, "so2": 64.066}

# Breakpoint AQI untuk PM2.5 (µg/m³) — tabel EPA
BP_PM25 = [
    (0.0,   12.0,   0,   50),   # Baik
    (12.1,  35.4,  51,  100),   # Sedang
    (35.5,  55.4, 101,  150),   # Tidak Sehat untuk Kelompok Sensitif
    (55.5, 150.4, 151,  200),   # Tidak Sehat
    (150.5, 250.4, 201, 300),   # Sangat Tidak Sehat
    (250.5, 350.4, 301, 400),   # Berbahaya
    (350.5, 500.4, 401, 500),   # Sangat Berbahaya
]

# Breakpoint AQI untuk PM10 (µg/m³)
BP_PM10 = [
    (0,    54,   0,   50),
    (55,  154,  51,  100),
    (155, 254, 101,  150),
    (255, 354, 151,  200),
    (355, 424, 201,  300),
    (425, 504, 301,  400),
    (505, 604, 401,  500),
]

# Breakpoint AQI untuk CO (ppm)
BP_CO = [
    (0.0,  4.4,   0,   50),
    (4.5,  9.4,  51,  100),
    (9.5,  12.4, 101, 150),
    (12.5, 15.4, 151, 200),
    (15.5, 30.4, 201, 300),
    (30.5, 40.4, 301, 400),
    (40.5, 50.4, 401, 500),
]

# Breakpoint AQI untuk O3 rata-rata 8 jam (ppm)
BP_O3_8H = [
    (0.000, 0.054,   0,  50),
    (0.055, 0.070,  51, 100),
    (0.071, 0.085, 101, 150),
    (0.086, 0.105, 151, 200),
    (0.106, 0.200, 201, 300),
]

# Breakpoint AQI untuk O3 rata-rata 1 jam (ppm) — untuk nilai tinggi
BP_O3_1H = [
    (0.125, 0.164, 101, 150),
    (0.165, 0.204, 151, 200),
    (0.205, 0.404, 201, 300),
    (0.405, 0.504, 301, 400),
    (0.505, 0.604, 401, 500),
]

# Breakpoint AQI untuk SO2 (ppb)
BP_SO2 = [
    (0,    35,   0,   50),
    (36,   75,  51,  100),
    (76,  185, 101,  150),
    (186, 304, 151,  200),
    (305, 604, 201,  300),
    (605, 804, 301,  400),
    (805, 1004, 401, 500),
]

# Breakpoint AQI untuk NO2 (ppb)
BP_NO2 = [
    (0,    53,   0,   50),
    (54,  100,  51,  100),
    (101, 360, 101,  150),
    (361, 649, 151,  200),
    (650, 1249, 201, 300),
    (1250, 1649, 301, 400),
    (1650, 2049, 401, 500),
]


def ug_to_ppb(x, mw):
    """Konversi µg/m³ → ppb menggunakan volume molar udara (24.45 L/mol pada 25°C, 1 atm)."""
    return x * 24.45 / mw


def ug_to_ppm(x, mw):
    """Konversi µg/m³ → ppm (ppb / 1000)."""
    return x * 24.45 / (mw * 1000)


def calc_aqi(C, breakpoints):
    """
    Hitung nilai AQI dari konsentrasi C menggunakan tabel breakpoint EPA.

    Formula EPA:
        AQI = (I_high - I_low) / (C_high - C_low) * (C - C_low) + I_low

    Return:
        int: nilai AQI (0-500), atau None jika di luar semua breakpoint
    """
    if C is None:
        return None
    for c_low, c_high, i_low, i_high in breakpoints:
        if c_low <= C <= c_high:
            # Interpolasi linear antara dua breakpoint
            return round((i_high - i_low) / (c_high - c_low) * (C - c_low) + i_low)
    if C > breakpoints[-1][1]:
        return 500  # cap di 500 jika melampaui breakpoint tertinggi
    return None


def aqi_from_record(rec):
    """
    Hitung AQI keseluruhan dari satu record polutan.

    AQI keseluruhan = nilai sub-index tertinggi (worst pollutant wins).
    Polutan dengan sub-index tertinggi disebut "dominant pollutant".

    Parameter:
        rec (dict): data polutan per jam (co, no2, o3, so2, pm2_5, pm10, nh3)

    Return:
        (aqi, dominant): nilai AQI total dan nama polutan yang menjadi penentu
    """
    subs = {}  # sub-index AQI per polutan

    # Hitung sub-index tiap polutan (dengan konversi satuan yang sesuai)
    if rec.get("pm2_5") is not None:
        subs["pm2_5"] = calc_aqi(rec["pm2_5"], BP_PM25)   # langsung µg/m³

    if rec.get("pm10") is not None:
        subs["pm10"] = calc_aqi(rec["pm10"], BP_PM10)     # langsung µg/m³

    if rec.get("co") is not None:
        subs["co"] = calc_aqi(ug_to_ppm(rec["co"], MW["co"]), BP_CO)  # µg/m³ → ppm

    if rec.get("o3") is not None:
        o3_ppm = ug_to_ppb(rec["o3"], MW["o3"]) / 1000    # µg/m³ → ppm
        # O3: ambil max antara perhitungan 8-jam dan 1-jam
        o3_vals = [v for v in (calc_aqi(o3_ppm, BP_O3_8H), calc_aqi(o3_ppm, BP_O3_1H)) if v is not None]
        if o3_vals:
            subs["o3"] = max(o3_vals)

    if rec.get("so2") is not None:
        subs["so2"] = calc_aqi(ug_to_ppb(rec["so2"], MW["so2"]), BP_SO2)  # µg/m³ → ppb

    if rec.get("no2") is not None:
        subs["no2"] = calc_aqi(ug_to_ppb(rec["no2"], MW["no2"]), BP_NO2)  # µg/m³ → ppb

    # Buang sub-index yang None (polutan tidak tersedia)
    subs = {k: v for k, v in subs.items() if v is not None}

    if not subs:
        return None, None  # tidak ada data polutan sama sekali

    # AQI total = sub-index tertinggi; dominant = polutan yang menghasilkannya
    dom = max(subs, key=subs.get)
    return subs[dom], dom


# ==============================================================================
# FUNGSI KOORDINAT
# ==============================================================================

def load_points():
    """
    Baca koordinat.json dan buat daftar titik sampel per kecamatan.

    Untuk tiap kecamatan, ambil POINTS_PER_KECAMATAN titik pertama saja
    (untuk menghemat API call). Juga hitung centroid per kabupaten.

    Return:
        points    (list): [{regency, district, lat, lon}, ...]
        centroids (dict): {nama_kabupaten: (lat, lon)}
    """
    with open(COORD_PATH, encoding="utf-8") as f:
        regions = json.load(f)

    points = []
    acc = defaultdict(lambda: [[], []])  # akumulator lat/lon per kabupaten untuk centroid

    for regency, districts in regions.items():
        for district, pts in districts.items():
            # Koreksi: kecamatan Tabanan yang salah masuk Gianyar → pindahkan ke Tabanan
            reg = (
                "Kabupaten Tabanan"
                if regency == "Kabupaten Gianyar" and district in TABANAN_KEC
                else regency
            )
            # Kumpulkan semua koordinat untuk hitung centroid
            for p in pts:
                acc[reg][0].append(p["lat"])
                acc[reg][1].append(p["lon"])

            # Ambil titik per kecamatan untuk di-fetch
            for p in pts:
                points.append({
                    "regency":  reg,
                    "district": district,
                    "lat":      p["lat"],
                    "lon":      p["lon"],
                })

    # Hitung centroid tiap kabupaten (rata-rata lat dan lon semua titik)
    centroids = {
        r: (sum(la) / len(la), sum(lo) / len(lo))
        for r, (la, lo) in acc.items()
        if la  # skip jika tidak ada titik
    }
    return points, centroids


# ==============================================================================
# FUNGSI FETCH — Ambil data dari API
# ==============================================================================

def fetch_json(url, params):
    """
    Fetch URL dengan retry otomatis jika gagal.

    Retry hingga MAX_RETRIES kali dengan jeda makin lama setiap kali.
    Mengembalikan None jika semua percobaan gagal.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                return r.json()  # sukses: kembalikan data JSON
        except requests.RequestException:
            pass  # koneksi gagal — coba lagi
        time.sleep(1.5 * attempt)  # tunggu makin lama setiap kali gagal

    print("STATUS:", r.status_code)
    print("RESPONSE:", r.text[:200])
    return None  # semua percobaan gagal


def fetch_air(points, start_unix, end_unix):
    """
    Fetch data polutan historis dari OpenWeather Air Pollution API.

    Setiap titik di-fetch secara paralel menggunakan ThreadPoolExecutor
    agar proses tidak terlalu lama (fetch sekuensial terlalu lambat untuk 100+ titik).

    Parameter:
        points     (list): daftar titik [{lat, lon, regency, district}]
        start_unix (int) : waktu mulai dalam Unix timestamp
        end_unix   (int) : waktu selesai dalam Unix timestamp

    Return:
        list: semua baris data polutan per titik per jam
    """
    rows = []

    def one(pt):
        """Fetch satu titik dan kembalikan list baris data."""
        data = fetch_json(OPENWEATHER_AIR_URL, {
            "lat":   pt["lat"],
            "lon":   pt["lon"],
            "start": start_unix,
            "end":   end_unix,
            "appid": get_api_key(),
        })
        out = []
        if not data or "list" not in data:
            return out  # gagal fetch atau data kosong

        for item in data["list"]:
            comp = item.get("components", {})
            # Konversi Unix timestamp → string "YYYY-MM-DD HH:00:00" zona WITA
            ts = datetime.fromtimestamp(item["dt"], tz=TZ).strftime("%Y-%m-%d %H:00:00")
            out.append({
                "timestamp_hour": ts,
                "regency":        pt["regency"],
                "district":       pt["district"],
                **{c: comp.get(c) for c in POLLUTANTS},  # nilai setiap polutan
            })
        return out

    # Fetch semua titik secara paralel
    with ThreadPoolExecutor(max_workers=AIR_WORKERS) as ex:
        for fut in as_completed([ex.submit(one, p) for p in points]):
            rows.extend(fut.result())
    return rows


def fetch_weather(points):
    """
    Fetch data cuaca historis dari Open-Meteo API (gratis, tidak perlu API key).

    Open-Meteo mendukung batch request (banyak koordinat sekaligus),
    jadi titik di-batch per 50 untuk efisiensi.

    Parameter:
        points (list): daftar titik [{lat, lon, regency, district}]

    Return:
        list: baris data cuaca per titik per jam
    """
    rows = []
    for i in range(0, len(points), WEATHER_BATCH):
        batch = points[i:i + WEATHER_BATCH]  # ambil batch 50 titik

        # Format parameter: lat/lon dipisah koma untuk semua titik dalam batch
        params = {
            "latitude":       ",".join(str(p["lat"]) for p in batch),
            "longitude":      ",".join(str(p["lon"]) for p in batch),
            "hourly":         ",".join(WEATHER_COLS),   # variabel cuaca yang diminta
            "timezone":       TIMEZONE,
            "past_days":      1,    # 1 hari ke belakang
            "forecast_days":  1,    # 1 hari ke depan
        }

        data = fetch_json(OPEN_METEO_URL, params)
        if data is None:
            continue  # batch ini gagal, lanjut ke batch berikutnya

        # Respons bisa berupa list (batch) atau dict tunggal (satu titik)
        results = data if isinstance(data, list) else [data]

        for pt, res in zip(batch, results):
            hourly = res.get("hourly") if isinstance(res, dict) else None
            if not hourly or "time" not in hourly:
                continue  # data tidak valid, skip titik ini

            for j, t in enumerate(hourly["time"]):
                ts = t.replace("T", " ") + ":00"  # "2024-01-15T10:00" → "2024-01-15 10:00:00"
                row = {
                    "timestamp_hour": ts,
                    "regency":        pt["regency"],
                    "district":       pt["district"],
                }
                # Ambil nilai setiap variabel cuaca untuk jam ini
                for c in WEATHER_COLS:
                    vals = hourly.get(c)
                    row[c] = vals[j] if vals and j < len(vals) else None
                rows.append(row)
    return rows


# ==============================================================================
# FUNGSI AGREGASI — Rata-rata titik → kabupaten
# ==============================================================================

def mean(xs):
    """
    Rata-rata list angka, mengabaikan nilai None.

    Mengembalikan None jika semua nilai kosong (tidak ada data sama sekali).
    """
    xs = [x for x in xs if x is not None]  # filter nilai kosong
    return sum(xs) / len(xs) if xs else None


def aggregate(rows, cols):
    """
    Rata-rata langsung semua titik dalam satu kabupaten.
    """
    regency = defaultdict(lambda: defaultdict(list))

    for r in rows:
        key = (r["timestamp_hour"], r["regency"])
        for c in cols:
            regency[key][c].append(r.get(c))

    return {
        k: {c: mean(v) for c, v in cv.items()}
        for k, cv in regency.items()
    }


def relative_age(latest_ts):
    """
    Hitung usia data relatif terhadap waktu sekarang dalam bahasa Indonesia.

    Contoh: "5 menit lalu", "2 jam lalu", "baru saja"
    """
    try:
        dt = datetime.strptime(latest_ts, "%Y-%m-%d %H:00:00").replace(tzinfo=TZ)
        mins = int((datetime.now(TZ) - dt).total_seconds() // 60)  # selisih dalam menit
    except ValueError:
        return "baru saja"

    if mins <= 1:      return "baru saja"
    if mins < 60:      return f"{mins} menit lalu"
    return f"{mins // 60} jam lalu"


def rnd(v, n=1):
    """
    Bulatkan nilai ke n desimal, kembalikan 0.0 jika nilai None.

    Nilai None bisa terjadi jika API tidak mengembalikan data polutan tertentu.
    """
    return round(v, n) if v is not None else 0.0


# ==============================================================================
# FUNGSI BUILD — Bangun dan tulis semua file JSON
# ==============================================================================

def build_and_write(air_reg, wx_reg, centroids, air_rows):
    """
    Gabungkan data polutan + cuaca, jalankan model ML, dan tulis semua file JSON.

    Parameter:
        air_reg  (dict): {(timestamp, regency): {polutan: nilai}} — sudah diagregasi
        wx_reg   (dict): {(timestamp, regency): {cuaca: nilai}}   — sudah diagregasi
        centroids(dict): {regency: (lat, lon)} — centroid geografis
        air_rows (list): data mentah per titik (untuk hitung spread PM2.5)

    Return:
        meta (dict): informasi waktu generate dan jumlah wilayah
    """
    # ── Hitung spread PM2.5/PM10 antar titik per (kabupaten, jam) ──────────────
    # Digunakan untuk kartu "Max PM2.5" dan "Min PM2.5" di dashboard
    spread = defaultdict(lambda: {"pm2_5": [], "pm10": []})
    for r in air_rows:
        key = (r["regency"], r["timestamp_hour"])
        if r.get("pm2_5") is not None:
            spread[key]["pm2_5"].append(r["pm2_5"])
        if r.get("pm10") is not None:
            spread[key]["pm10"].append(r["pm10"])

    # ── Gabungkan polutan + cuaca per kabupaten per jam ────────────────────────
    by_regency = defaultdict(list)                           # {regency: [sorted records]}
    timestamps = sorted({ts for (ts, _reg) in air_reg})     # semua timestamp unik
    regencies  = sorted({reg for (_ts, reg) in air_reg})    # semua kabupaten unik

    for reg in regencies:
        for ts in timestamps:
            if (ts, reg) not in air_reg:
                continue  # tidak ada data untuk kombinasi ini

            rec = dict(air_reg[(ts, reg)])           # data polutan kabupaten ini
            rec.update(wx_reg.get((ts, reg), {}))    # tambahkan data cuaca (merge)
            rec["timestamp_hour"] = ts
            rec["regency"] = reg

            # Hitung AQI dan tentukan polutan dominan untuk record ini
            aqi, dom = aqi_from_record(rec)
            rec["aqi"] = aqi
            rec["dominant"] = dom

            by_regency[reg].append(rec)

    if not by_regency:
        raise RuntimeError("Live aggregation produced no records")

    # ── Prediksi model ML (jika tersedia) ─────────────────────────────────────
    model_forecasts, model_labels, model_pollutants = {}, {}, []
    if USE_MODELS and os.path.isdir(MODEL_DIR):
        try:
            import predict_models  # lazy import
            all_records = [r for rows in by_regency.values() for r in rows]
            model_forecasts, model_labels, model_pollutants = predict_models.predict(
                all_records, MODEL_DIR)
            if model_pollutants:
                print(f"[live] model forecast for {sorted(model_pollutants)}", flush=True)
        except Exception as exc:
            # Model gagal → tetap lanjut dengan data klimatologis dari generate_data.py
            print(f"[live] model forecast skipped: {type(exc).__name__}: {exc}", flush=True)

    # Timestamp data terbaru dari semua kabupaten (untuk metadata)
    newest = max(rows[-1]["timestamp_hour"] for rows in by_regency.values())
    meta = {
        "generatedAt": datetime.now(TZ).isoformat(timespec="seconds"),  # waktu generate
        "dataLatest":  newest,                                            # data terbaru
        "regencies":   len(by_regency),                                  # jumlah kabupaten
        "source":      "live",                                            # sumber data
    }

    # ── Tulis current.json — snapshot terkini per kabupaten ───────────────────
    locations = []
    for reg, rows in by_regency.items():
        # Ambil record terbaru yang punya nilai AQI
        last = next((r for r in reversed(rows) if r.get("aqi") is not None), rows[-1])
        lat, lon = CAPITALS.get(reg, centroids.get(reg, (None, None)))

        # Hitung spread PM2.5 untuk record terbaru ini
        sp = spread.get((reg, last["timestamp_hour"]), {"pm2_5": [], "pm10": []})
        pm25_max = max(sp["pm2_5"]) if sp["pm2_5"] else last.get("pm2_5")
        pm25_min = min(sp["pm2_5"]) if sp["pm2_5"] else last.get("pm2_5")

        locations.append({
            "name":          DISPLAY.get(reg, reg),
            "regency":       reg,
            "lat":           lat,
            "lng":           lon,
            "timestamp":     last["timestamp_hour"],
            "update":        relative_age(last["timestamp_hour"]),
            "aqi":           round(last["aqi"]) if last.get("aqi") is not None else 0,
            "aqiIndex":      "",
            "dominant":      (last.get("dominant") or "").lower(),
            "pm25":          rnd(last.get("pm2_5")),
            "pm10":          rnd(last.get("pm10")),
            "pm25Max":       rnd(pm25_max),
            "pm25Min":       rnd(pm25_min),
            "co":            rnd(last.get("co")),
            "no":            rnd(last.get("no"), 2),
            "no2":           rnd(last.get("no2"), 2),
            "o3":            rnd(last.get("o3")),
            "so2":           rnd(last.get("so2"), 2),
            "nh3":           rnd(last.get("nh3"), 2),
            "temp":          round(last["temperature_2m"])          if last.get("temperature_2m")          is not None else 0,
            "humidity":      round(last["relative_humidity_2m"])    if last.get("relative_humidity_2m")    is not None else 0,
            "precipitation": rnd(last.get("precipitation"), 2),
            "pressure":      round(last["surface_pressure"])        if last.get("surface_pressure")        is not None else 0,
            "windSpeed":     rnd(last.get("wind_speed_10m")),
            "windDir":       round(last["wind_direction_10m"])      if last.get("wind_direction_10m")      is not None else 0,
        })

    locations.sort(key=lambda d: d["name"])  # urutkan alfabetis
    write("current.json", {"meta": meta, "locations": locations})

    # ── Tulis trend.json — tren 24 jam terakhir per kabupaten ─────────────────
    trend = {}
    for reg, rows in by_regency.items():
        last24 = rows[-24:]  # 24 jam terakhir
        trend[DISPLAY.get(reg, reg)] = {
            "labels": [r["timestamp_hour"][11:16] for r in last24],        # "HH:MM"
            "aqi":    [round(r["aqi"]) if r.get("aqi") is not None else 0 for r in last24],
            "pm25":   [rnd(r.get("pm2_5")) for r in last24],
            "pm10":   [rnd(r.get("pm10")) for r in last24],
        }
    write("trend.json", trend)

    # ── Tulis pollutant_series.json — tren + prediksi per polutan ─────────────
    ps_path = os.path.join(OUT, "pollutant_series.json")
    # Baca file yang sudah ada (hasil generate_data.py) untuk bagian prediksi klimatologis
    base = json.load(open(ps_path, encoding="utf-8")) if os.path.exists(ps_path) else {}

    model_keys = set(model_pollutants)  # polutan yang punya model ML

    for reg, rows in by_regency.items():
        name = DISPLAY.get(reg, reg)
        last24 = rows[-24:]
        entry = base.get(name) or {"pollutants": {}}
        entry["labelsCurrent"] = [r["timestamp_hour"][11:16] for r in last24]  # label tren aktual

        reg_forecast = model_forecasts.get(reg, {})  # prediksi model ML untuk kabupaten ini
        for key in POLLUTANTS:
            decimals = 2 if key in ("no", "no2", "so2", "nh3") else 1
            cur = [rnd(r.get(key), decimals) for r in last24]  # data aktual 24 jam terakhir
            slot = entry.setdefault("pollutants", {}).setdefault(key, {})
            slot["current"] = cur

            if key in model_keys and key in reg_forecast:
                # Pakai prediksi dari model ML jika tersedia
                slot["forecast"] = reg_forecast[key]
                slot["forecastSource"] = "model"
            else:
                # Fallback ke prediksi klimatologis dari generate_data.py
                slot.setdefault("forecast", cur)
                slot.setdefault("forecastSource", "climatology")

        # Label waktu prediksi: dari model (jam target +24h) atau fallback ke label aktual
        entry["labelsForecast"] = model_labels.get(reg, entry["labelsCurrent"])
        base[name] = entry

    write("pollutant_series.json", base)

    # ── Tulis prediction.json — AQI/PM2.5/PM10 prediksi 24 jam ───────────────
    # Hanya ditulis jika model ML tersedia dan semua polutan yang dibutuhkan ada
    needed = {"co", "no2", "o3", "so2", "pm2_5", "pm10"}  # polutan minimum untuk hitung AQI
    if model_forecasts:
        pred_path = os.path.join(OUT, "prediction.json")
        pred_base = json.load(open(pred_path, encoding="utf-8")) if os.path.exists(pred_path) else {}
        covered = 0
        for reg in by_regency:
            name   = DISPLAY.get(reg, reg)
            fc     = model_forecasts.get(reg, {})
            labels = model_labels.get(reg, [])
            if not labels or not needed.issubset(fc):
                continue  # skip jika data kurang lengkap

            aqi, pm25, pm10 = [], [], []
            for i in range(len(labels)):
                rec = {p: fc[p][i] for p in fc}  # rekonstruksi record polutan jam ke-i
                a, _ = aqi_from_record(rec)       # hitung AQI dari prediksi polutan
                aqi.append(round(a) if a is not None else 0)
                pm25.append(fc["pm2_5"][i])
                pm10.append(fc["pm10"][i])

            pred_base[name] = {"labels": labels, "aqi": aqi, "pm25": pm25, "pm10": pm10}
            covered += 1

        if covered:
            write("prediction.json", pred_base)
            print(f"[live] prediction (AQI) from model for {covered} regencies", flush=True)

    return meta


def write(name, obj):
    """
    Tulis objek Python sebagai file JSON ke folder data/.

    Tanpa spasi di JSON (separators) untuk menghemat ukuran file.
    ensure_ascii=False agar karakter Indonesia (é, ñ, dll) tidak di-escape.
    """
    os.makedirs(OUT, exist_ok=True)  # pastikan folder ada
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


# ==============================================================================
# FUNGSI UTAMA — Entry point untuk satu siklus refresh
# ==============================================================================

def refresh():
    """
    Jalankan satu siklus refresh data kualitas udara live.

    Dipanggil oleh server.py di background thread setiap REFRESH_MINUTES menit.

    Return:
        meta (dict): metadata hasil refresh (waktu, jumlah kabupaten, dll)

    Raise:
        RuntimeError: jika tidak ada data polutan yang berhasil di-fetch
    """
    # Muat koordinat dan hitung centroid
    points, centroids = load_points()

    # Tentukan rentang waktu fetch: 25 jam ke belakang dari sekarang
    end_dt   = datetime.now(TZ)
    start_dt = end_dt - timedelta(hours=25)

    # Fetch data polutan (paralel) dan data cuaca (batch)
    air     = fetch_air(points, int(start_dt.timestamp()), int(end_dt.timestamp()))
    if not air:
        raise RuntimeError("No air-quality data fetched (check API key / connectivity)")

    weather = fetch_weather(points)

    # Agregasi: titik → kecamatan → kabupaten
    air_reg = aggregate(air, POLLUTANTS)
    wx_reg  = aggregate(weather, WEATHER_COLS) if weather else {}

    # Build dan tulis semua file JSON
    meta = build_and_write(air_reg, wx_reg, centroids, air)

    print(
        f"[live] refreshed: {meta['regencies']} regencies, latest {meta['dataLatest']}, "
        f"{len(points)} points, {len(air)} air rows",
        flush=True,
    )
    return meta


# ==============================================================================
# ENTRY POINT — Jalankan sekali secara manual
# ==============================================================================

if __name__ == "__main__":
    # Bisa dijalankan langsung: python3 backend/live_pipeline.py
    refresh()
