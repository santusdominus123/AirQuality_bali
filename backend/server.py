#!/usr/bin/env python3
"""
server.py — Server Utama EcoMonitor Kualitas Udara Bali
=========================================================

File ini adalah titik masuk (entry point) aplikasi. Tugasnya:
  1. Menyajikan halaman web dashboard (index.html, app.js, styles.css)
  2. Menjalankan pipeline data udara secara otomatis di background setiap 60 menit
  3. Menjalankan pipeline berita LLM secara otomatis setiap 180 menit
  4. Menyediakan endpoint API untuk refresh manual dari dashboard

Cara menjalankan:
    python3 backend/server.py

Server berjalan di port 4173 secara default.
"""

import os           # operasi sistem: baca env, path file
import subprocess   # jalankan script python lain sebagai subprocess
import sys          # akses ke executable python saat ini (sys.executable)
import threading    # jalankan pipeline di background thread agar server tidak hang
import time         # time.sleep untuk interval refresh
from datetime import datetime  # timestamp refresh

from flask import Flask, jsonify, send_from_directory  # framework web
from zoneinfo import ZoneInfo   # timezone WITA (Asia/Makassar)

import live_pipeline  # modul pipeline data kualitas udara real-time

# Path root project (dua level di atas file ini: backend/ -> root/)
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Folder tempat file JSON data disimpan dan dibaca dashboard
DATA_DIR = os.path.join(BASE, "data")

# Timezone Bali (WITA = UTC+8)
TZ = ZoneInfo("Asia/Makassar")


# ==============================================================================
# KONFIGURASI — Muat secrets dari file .env
# ==============================================================================

def _load_secrets():
    """
    Baca file backend/secrets.env dan masukkan ke environment variable.

    Format file: KEY=VALUE per baris (baris kosong dan # diabaikan).
    Fungsi ini aman dijalankan meski file tidak ada (tidak akan error).
    """
    # Path ke file secrets.env di folder yang sama dengan server.py
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "secrets.env")

    # Jika file tidak ada, skip (tidak wajib ada)
    if not os.path.exists(path):
        return

    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()                          # hapus spasi/newline di tepi
            if not line or line.startswith("#") or "=" not in line:
                continue                                  # skip baris kosong/komentar/invalid
            key, val = line.split("=", 1)                # pisah KEY dan VALUE di tanda = pertama
            os.environ.setdefault(key.strip(), val.strip())  # set env hanya jika belum ada


# Jalankan saat startup — env harus siap sebelum konstanta di bawah dibaca
_load_secrets()

# Port server — bisa di-override via env PORT (berguna saat deploy di cloud)
PORT = int(os.environ.get("PORT", "4173"))

# Interval refresh data kualitas udara dalam menit (default 60 menit = 1 jam)
REFRESH_MINUTES = int(os.environ.get("REFRESH_MINUTES", "60"))

# Interval refresh berita LLM dalam menit (default 180 menit = 3 jam)
NEWS_REFRESH_MINUTES = int(os.environ.get("NEWS_REFRESH_MINUTES", "180"))

# Buat aplikasi Flask — static_folder=None karena kita serve file manual lewat route
app = Flask(__name__, static_folder=None)


@app.after_request
def _strip_content_disposition(resp):
    """
    Hapus header Content-Disposition dari semua respons.

    Werkzeug 3.1+ otomatis menambahkan 'Content-Disposition: inline; filename=...'
    pada send_from_directory. Beberapa proxy/tunnel (mis. VS Code Dev Tunnels)
    memperlakukan header ini sebagai perintah UNDUH, sehingga halaman HTML/JS
    malah ter-download alih-alih ditampilkan. Aplikasi ini tidak pernah
    menyajikan file untuk diunduh dari server (unduhan CSV dibuat di sisi
    browser), jadi header ini aman dihapus total.
    """
    resp.headers.pop("Content-Disposition", None)
    return resp


# ==============================================================================
# STATE — Status pipeline disimpan di sini (diakses antar thread)
# ==============================================================================

# Status pipeline data kualitas udara
_status = {
    "lastRefresh": None,    # waktu terakhir refresh berhasil (format ISO)
    "lastAttempt": None,    # waktu terakhir dicoba refresh (berhasil atau gagal)
    "mode": "starting",     # mode saat ini: "live" | "fallback" | "starting"
    "dataLatest": None,     # timestamp data terbaru yang berhasil diambil
    "error": None,          # pesan error terakhir (None jika tidak ada error)
    "refreshing": False,    # True jika sedang dalam proses refresh
}

# Status pipeline berita LLM
_news_status = {
    "lastRefresh": None,    # waktu terakhir berita berhasil diperbarui
    "lastAttempt": None,    # waktu terakhir dicoba refresh berita
    "error": None,          # pesan error terakhir pipeline berita
    "refreshing": False,    # True jika sedang scraping/analisis LLM
    "articles": 0,          # jumlah artikel berita yang berhasil diproses
}

# Lock untuk mencegah race condition saat dua thread baca/tulis _status bersamaan
_lock = threading.Lock()


# ==============================================================================
# FUNGSI PIPELINE — Logika refresh data
# ==============================================================================

def ensure_baseline():
    """
    Hasilkan file JSON awal dari dataset CSV sebelum data live tersedia.

    Menjalankan pipeline/generate_data.py sebagai subprocess.
    Dipanggil sekali saat server pertama kali start, agar dashboard
    sudah punya data untuk ditampilkan bahkan sebelum API live berjalan.
    """
    # Path ke script generator data baseline
    script = os.path.join(BASE, "pipeline", "generate_data.py")
    try:
        # Jalankan script dengan python yang sama, tunggu selesai (timeout 5 menit)
        subprocess.run([sys.executable, script], check=True,
                       capture_output=True, text=True, timeout=300)
        print("[backend] baseline JSON ready (CSV).", flush=True)
    except Exception as exc:
        # Baseline gagal tidak fatal — server tetap jalan, data live akan mengisi nanti
        print(f"[backend] baseline generation failed: {exc}", flush=True)


def do_refresh():
    """
    Lakukan satu siklus refresh data kualitas udara dari API OpenWeather.

    Menggunakan lock agar hanya satu refresh berjalan dalam satu waktu.
    Jika refresh sedang berjalan, request berikutnya langsung ditolak.

    Return:
        dict: {"ok": True/False, "message": ..., "meta": ...}
    """
    # Cek apakah sudah ada refresh yang sedang berjalan (gunakan lock untuk thread-safety)
    with _lock:
        if _status["refreshing"]:
            # Tolak jika sudah ada yang berjalan — hindari dua refresh sekaligus
            return {"ok": False, "message": "refresh already in progress"}
        _status["refreshing"] = True  # tandai sedang berjalan

    # Catat waktu percobaan refresh (sebelum eksekusi)
    _status["lastAttempt"] = datetime.now(TZ).isoformat(timespec="seconds")

    try:
        # Jalankan pipeline live — ambil data dari API dan tulis ke data/*.json
        meta = live_pipeline.refresh()

        # Refresh berhasil: update status ke "live"
        with _lock:
            _status.update(
                mode="live",                                                    # mode live aktif
                error=None,                                                     # hapus error sebelumnya
                lastRefresh=datetime.now(TZ).isoformat(timespec="seconds"),    # catat waktu sukses
                dataLatest=meta.get("dataLatest"),                             # timestamp data terbaru
            )
        return {"ok": True, "meta": meta}

    except Exception as exc:
        # Refresh gagal: log error, tapi tetap serve data lama ke dashboard
        msg = f"{type(exc).__name__}: {exc}"
        print(f"[backend] live refresh failed: {msg}", flush=True)
        with _lock:
            _status.update(error=msg)
            if _status["mode"] == "starting":
                # Jika belum pernah sukses sama sekali, mode jadi "fallback" (pakai data CSV)
                _status["mode"] = "fallback"
        return {"ok": False, "message": msg}

    finally:
        # Selalu reset flag refreshing, baik sukses maupun gagal
        with _lock:
            _status["refreshing"] = False


def scheduler():
    """
    Thread background untuk refresh data kualitas udara secara berkala.

    Alur:
      1. Generate baseline JSON dari CSV (agar dashboard tidak kosong)
      2. Langsung refresh data live pertama kali
      3. Loop: tidur REFRESH_MINUTES menit, lalu refresh lagi selamanya
    """
    ensure_baseline()       # siapkan data awal dari CSV
    do_refresh()            # ambil data live pertama kali saat startup
    while True:
        time.sleep(REFRESH_MINUTES * 60)  # tunggu interval (dalam detik)
        do_refresh()                       # refresh data live lagi


def do_news_refresh():
    """
    Lakukan satu siklus refresh berita + analisis LLM per kabupaten.

    Mirip dengan do_refresh() tapi untuk pipeline berita.
    Import news_pipeline dilakukan di dalam fungsi (lazy import)
    agar server tetap bisa start meski modul LLM belum tersedia.

    Return:
        dict: {"ok": True/False, ...}
    """
    # Tolak jika sudah ada refresh berita yang berjalan
    with _lock:
        if _news_status["refreshing"]:
            return {"ok": False, "message": "news refresh already in progress"}
        _news_status["refreshing"] = True

    # Catat waktu percobaan
    _news_status["lastAttempt"] = datetime.now(TZ).isoformat(timespec="seconds")

    try:
        import news_pipeline  # lazy import: hanya load jika dipanggil
        meta = news_pipeline.refresh_news()  # scrape berita + analisis LLM semua kabupaten
        with _lock:
            _news_status.update(
                error=None,
                articles=meta.get("totalArticles", 0),                         # jumlah artikel hari ini
                lastRefresh=datetime.now(TZ).isoformat(timespec="seconds"),
            )
        return {"ok": True, "meta": meta}

    except Exception as exc:
        # Gagal: dashboard akan tampilkan template berita fallback
        msg = f"{type(exc).__name__}: {exc}"
        print(f"[backend] news refresh failed: {msg}", flush=True)
        with _lock:
            _news_status.update(error=msg)
        return {"ok": False, "message": msg}

    finally:
        with _lock:
            _news_status["refreshing"] = False


def news_scheduler():
    """
    Thread background untuk refresh berita + LLM secara berkala.

    Menunggu 90 detik dulu sebelum refresh pertama — memberi waktu
    pipeline data udara (do_refresh) selesai duluan, karena pipeline
    berita butuh data current.json dan prediction.json yang dihasilkan
    pipeline data udara.
    """
    time.sleep(90)              # tunggu pipeline data udara selesai startup
    do_news_refresh()           # refresh berita pertama kali
    while True:
        time.sleep(NEWS_REFRESH_MINUTES * 60)  # tunggu interval berita
        do_news_refresh()


# ==============================================================================
# ROUTES — Endpoint URL yang dilayani server
# ==============================================================================

@app.route("/")
def index():
    """Sajikan halaman utama dashboard (index.html)."""
    return send_from_directory(BASE, "index.html")


@app.route("/data/<path:filename>")
def data_files(filename):
    """
    Sajikan file JSON data dari folder data/.

    Cache-Control: no-store memastikan browser selalu ambil versi terbaru
    (tidak cache file JSON yang isinya berubah setiap refresh).
    """
    resp = send_from_directory(DATA_DIR, filename)
    resp.headers["Cache-Control"] = "no-store"  # paksa browser selalu fetch ulang
    return resp


@app.route("/api/status")
def api_status():
    """
    Kembalikan status pipeline sebagai JSON.

    Dashboard memanggil endpoint ini untuk menampilkan info
    "Data diperbarui X menit lalu" dan indikator mode live/fallback.
    """
    with _lock:
        # Gabungkan status data udara dan status berita dalam satu response
        return jsonify(dict(
            _status,
            refreshEveryMinutes=REFRESH_MINUTES,
            news=dict(_news_status, refreshEveryMinutes=NEWS_REFRESH_MINUTES),
        ))


@app.route("/api/refresh", methods=["GET", "POST"])
def api_refresh():
    """
    Trigger refresh data kualitas udara secara manual.

    Dipanggil dari tombol "Refresh" di dashboard.
    Mengembalikan HTTP 200 jika berhasil, 503 jika gagal.
    """
    result = do_refresh()
    with _lock:
        result["status"] = dict(_status)  # sertakan status terbaru dalam response
    return jsonify(result), (200 if result.get("ok") else 503)


@app.route("/api/refresh-news", methods=["GET", "POST"])
def api_refresh_news():
    """
    Trigger refresh berita + analisis LLM secara manual.

    Mengembalikan HTTP 200 jika berhasil, 503 jika gagal.
    """
    result = do_news_refresh()
    with _lock:
        result["status"] = dict(_news_status)
    return jsonify(result), (200 if result.get("ok") else 503)


# File statis yang boleh diakses publik dari root project
# File lain (model .joblib, CSV, notebook) TIDAK boleh diakses dari luar
ALLOWED_STATIC = {"app.js", "styles.css", "favicon.ico"}


@app.route("/<path:filename>")
def static_files(filename):
    """
    Sajikan file statis dashboard (JS, CSS, favicon).

    File di luar ALLOWED_STATIC dikembalikan 404 —
    melindungi model ML, dataset, dan source code dari akses publik.
    """
    if filename not in ALLOWED_STATIC:
        return ("Not found", 404)  # tolak akses ke file sensitif
    return send_from_directory(BASE, filename)


# ==============================================================================
# ENTRY POINT — Jalankan server
# ==============================================================================

if __name__ == "__main__":
    # Jalankan scheduler data udara di background thread (daemon=True: otomatis mati saat main thread mati)
    threading.Thread(target=scheduler, daemon=True).start()

    # Jalankan scheduler berita di background thread terpisah
    threading.Thread(target=news_scheduler, daemon=True).start()

    # Cetak info ke terminal
    print(
        f"[backend] serving on http://0.0.0.0:{PORT} "
        f"(data refresh {REFRESH_MINUTES}min, berita {NEWS_REFRESH_MINUTES}min)",
        flush=True,
    )

    # Jalankan Flask server — threaded=True agar bisa handle banyak request bersamaan
    app.run(host="0.0.0.0", port=PORT, threaded=True)
