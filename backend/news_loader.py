"""
news_loader.py
==============
Utilitas untuk memuat, memfilter, dan menyiapkan data berita CSV
agar siap dianalisis oleh AirQualityLLM.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple
import pandas as pd

from llm_model import BeritaItem

# Kategori yang relevan dengan kualitas udara
KATEGORI_UDARA = {
    "kemacetan",
    "kualitas_udara",
    "kebakaran",
    "sampah_lingkungan",
    "cuaca",
    "kebisingan_polusi_lain",
}

# Peta kabupaten/kota → sub-lokasi yang tercakup
KABUPATEN_COVERAGE: dict[str, list[str]] = {
    "Denpasar":    ["denpasar", "renon", "sanur", "pemogan", "sesetan", "kesiman", "sunset road", "gatot subroto", "imam bonjol"],
    "Badung":      ["badung", "kuta", "kuta selatan", "kuta utara", "legian", "seminyak", "canggu", "berawa", "tibubeneng",
                    "kerobokan", "dalung", "mengwi", "abiansemal", "jimbaran", "nusa dua", "pecatu", "uluwatu", "munggu"],
    "Gianyar":     ["gianyar", "ubud", "sukawati", "blahbatuh", "tampaksiring", "tegallalang", "monkey forest", "campuhan"],
    "Tabanan":     ["tabanan", "bedugul"],
    "Klungkung":   ["klungkung", "nusa penida", "padangbai"],
    "Bangli":      ["bangli", "kintamani"],
    "Karangasem":  ["karangasem", "amed"],
    "Buleleng":    ["buleleng", "singaraja"],
    "Jembrana":    ["jembrana", "negara"],
}

# Semua kabupaten
ALL_KABUPATEN = sorted(KABUPATEN_COVERAGE.keys())


def load_berita(csv_path: str | Path) -> pd.DataFrame:
    """Muat CSV berita dan normalisasi kolom."""
    df = pd.read_csv(str(csv_path))
    # Normalisasi kolom teks
    for col in ["judul", "ringkasan", "konten", "kata_kunci", "lokasi", "kategori", "sumber", "tag"]:
        if col in df.columns:
            df[col] = df[col].fillna("").astype(str)
    if "tanggal" in df.columns:
        df["tanggal"] = df["tanggal"].fillna("").astype(str)
    return df


def filter_berita_lokasi(
    df: pd.DataFrame,
    kabupaten: str,
    max_berita: int = 10,
    hanya_relevan_udara: bool = True,
) -> Tuple[List[BeritaItem], dict]:
    """
    Filter berita berdasarkan kabupaten/kota dan relevansi kualitas udara.

    Returns
    -------
    (list of BeritaItem, stats_dict)
    """
    sub_lokasi = KABUPATEN_COVERAGE.get(kabupaten, [kabupaten.lower()])
    # word-boundary agar 'badung' tidak cocok dengan 'Bandung', dll.
    pattern = r"\b(?:" + "|".join(re.escape(s) for s in sub_lokasi) + r")\b"

    # Filter berdasarkan kolom lokasi
    mask_lokasi = df["lokasi"].str.lower().str.contains(pattern, na=False, regex=True)
    df_filtered = df[mask_lokasi].copy()

    # Fallback: cari di judul / konten jika tidak ada hasil dari kolom lokasi
    if len(df_filtered) < 3:
        mask_judul = (
            df["judul"].str.lower().str.contains(pattern, na=False, regex=True) |
            df["konten"].str.lower().str.contains(pattern, na=False, regex=True)
        )
        df_extra = df[mask_judul & ~mask_lokasi].head(5)
        df_filtered = pd.concat([df_filtered, df_extra], ignore_index=True)

    # Filter kategori relevan dengan kualitas udara (TANPA fallback ke semua
    # kategori — supaya berita off-topic tidak masuk).
    if hanya_relevan_udara:
        mask_kategori = df_filtered["kategori"].isin(KATEGORI_UDARA)
        df_udara = df_filtered[mask_kategori].copy()
    else:
        df_udara = df_filtered.copy()

    # Urutkan: prioritaskan kualitas_udara dan kebakaran
    priority_order = {
        "kualitas_udara": 0,
        "kebakaran": 1,
        "kemacetan": 2,
        "sampah_lingkungan": 3,
        "cuaca": 4,
        "kebisingan_polusi_lain": 5,
    }
    if "kategori" in df_udara.columns:
        df_udara["_priority"] = df_udara["kategori"].map(priority_order).fillna(99)
        df_udara = df_udara.sort_values("_priority")

    df_top = df_udara.head(max_berita)

    # Konversi ke BeritaItem
    items: List[BeritaItem] = []
    for _, row in df_top.iterrows():
        items.append(BeritaItem(
            judul=row.get("judul", ""),
            kategori=row.get("kategori", ""),
            lokasi=row.get("lokasi", ""),
            ringkasan=row.get("ringkasan", ""),
            konten=row.get("konten", "")[:500],   # potong agar tidak terlalu panjang
            kata_kunci=row.get("kata_kunci", ""),
            tanggal=row.get("tanggal", ""),
            sumber=row.get("sumber", ""),
            url=row.get("url", ""),
        ))

    stats = {
        "total_berita_bali": len(df),
        "berita_di_lokasi": len(df_filtered),
        "berita_relevan_udara": len(df_udara),
        "berita_digunakan": len(items),
        "distribusi_kategori": df_udara["kategori"].value_counts().to_dict() if len(df_udara) > 0 else {},
    }

    return items, stats


def get_distribusi_kabupaten(df: pd.DataFrame) -> dict[str, int]:
    """Hitung jumlah berita per kabupaten (untuk preview di UI)."""
    hasil: dict[str, int] = {}
    for kab, sub_loks in KABUPATEN_COVERAGE.items():
        pattern = "|".join(re.escape(s) for s in sub_loks)
        count = df["lokasi"].str.lower().str.contains(pattern, na=False, regex=True).sum()
        hasil[kab] = int(count)
    return hasil


def preview_berita(items: List[BeritaItem]) -> pd.DataFrame:
    """Buat DataFrame ringkas untuk ditampilkan di UI."""
    rows = []
    for b in items:
        rows.append({
            "Tanggal": b.tanggal,
            "Judul": b.judul[:80] + ("..." if len(b.judul) > 80 else ""),
            "Kategori": b.kategori,
            "Sumber": b.sumber,
        })
    return pd.DataFrame(rows)
