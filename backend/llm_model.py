"""
llm_model.py — Modul Analisis LLM Kualitas Udara Bali
======================================================

Modul ini mengirim data berita, cuaca, dan polutan ke model LLM (AI)
untuk menghasilkan analisis kualitas udara yang mudah dipahami masyarakat.

Mendukung 4 provider LLM:
  - Groq      : API cloud gratis dengan LLaMA (cepat, cocok untuk production)
  - OpenAI    : GPT-4o / GPT-4o-mini (berbayar, kualitas tinggi)
  - Anthropic : Claude (berbayar, kualitas tinggi)
  - Ollama    : LLM lokal di komputer sendiri (gratis, perlu GPU)

Cara pakai:
    from llm_model import AirQualityLLM
    llm = AirQualityLLM(provider="groq", api_key="gsk_...")
    result = llm.analyze(berita_list, lokasi, cuaca_data, pm25_pred, risk_level)

Output berupa objek AnalysisResult berisi:
  - ringkasan_berita    : paragraf ringkasan situasi udara
  - faktor_penyebab     : list faktor yang menyebabkan polusi
  - rekomendasi_mitigasi: list tindakan yang disarankan
  - tingkat_urgensi     : rendah / sedang / tinggi / kritis
  - kelompok_rentan     : kelompok yang perlu ekstra hati-hati
"""

from __future__ import annotations  # izinkan type hint forward reference

import json      # parse respons JSON dari LLM
import os        # baca environment variable untuk API key
import re        # regex untuk bersihkan markdown dari respons LLM
import textwrap  # dedent untuk format prompt multi-baris
import time      # time.sleep saat retry setelah rate limit
from dataclasses import dataclass, field  # struktur data sederhana
from typing import List, Optional         # type hints

# ─── Data classes ───────────────────────────────────────────────────────────

@dataclass
class BeritaItem:
    judul: str
    kategori: str
    lokasi: str
    ringkasan: str = ""
    konten: str = ""
    kata_kunci: str = ""
    tanggal: str = ""
    sumber: str = ""
    url: str = ""


@dataclass
class CuacaData:
    suhu_celsius: Optional[float] = None
    kelembaban_persen: Optional[float] = None
    kecepatan_angin_ms: Optional[float] = None
    arah_angin: Optional[str] = None
    kondisi: Optional[str] = None          # cerah, berawan, hujan, dll
    keterangan: str = ""


@dataclass
class AnalysisResult:
    ringkasan_berita: str = ""
    faktor_penyebab: List[str] = field(default_factory=list)
    rekomendasi_mitigasi: List[str] = field(default_factory=list)
    tingkat_urgensi: str = "rendah"        # rendah / sedang / tinggi / kritis
    kelompok_rentan: List[str] = field(default_factory=list)
    raw_response: str = ""
    provider_used: str = ""
    error: Optional[str] = None


# ─── Prompt builder ──────────────────────────────────────────────────────────

def _build_system_prompt() -> str:
    return textwrap.dedent("""
        Kamu adalah sistem analisis kualitas udara cerdas untuk wilayah Bali, Indonesia.
        Tugasmu adalah menganalisis berita lokal, data cuaca, dan prediksi PM2.5 untuk
        memberikan penjelasan yang mudah dipahami oleh masyarakat umum dan pengambil kebijakan.

        Selalu berikan respons dalam format JSON yang valid dengan struktur berikut:
        {
            "ringkasan_berita": "<Paragraf ringkas (2-3 kalimat) menggambarkan situasi terkini berdasarkan berita>",
            "faktor_penyebab": [
                "<Faktor 1 yang berkontribusi pada kualitas udara>",
                "<Faktor 2>",
                ...
            ],
            "rekomendasi_mitigasi": [
                "<Rekomendasi konkret 1 untuk pemerintah/masyarakat>",
                "<Rekomendasi 2>",
                ...
            ],
            "tingkat_urgensi": "<rendah|sedang|tinggi|kritis>",
            "kelompok_rentan": [
                "<Kelompok yang paling terdampak, misal: anak-anak, lansia, penderita asma>"
            ]
        }

        Panduan penentuan tingkat_urgensi berdasarkan PM2.5:
        - rendah  : PM2.5 < 12 µg/m³ (Baik)
        - sedang  : PM2.5 12–35 µg/m³ (Sedang)
        - tinggi  : PM2.5 35–55 µg/m³ (Tidak Sehat untuk Kelompok Sensitif)
        - kritis  : PM2.5 > 55 µg/m³ (Tidak Sehat / Berbahaya)

        ATURAN RELEVANSI (penting):
        - GUNAKAN HANYA berita yang benar-benar berkaitan dengan kualitas udara:
          polusi/pencemaran udara, asap, kabut asap, emisi/kemacetan kendaraan,
          kebakaran lahan/hutan, pembakaran sampah/lahan, debu, atau cuaca yang
          memengaruhi penyebaran polutan.
        - ABAIKAN total berita yang tidak relevan (politik, anggaran, santunan,
          pariwisata, pembangunan jalan, olahraga, dll). JANGAN sebut/kutip berita
          tak relevan di ringkasan maupun faktor.
        - Jika TIDAK ADA berita relevan, nyatakan di ringkasan_berita bahwa tidak ada
          berita kualitas udara yang menonjol hari ini, lalu dasarkan faktor_penyebab
          pada DATA POLUTAN TERUKUR dan cuaca (bukan mengarang berita).
        - Jangan pernah mengarang berita yang tidak ada di input.

        Berikan respons HANYA dalam format JSON yang valid. Jangan tambahkan teks di luar JSON.
    """).strip()


def _build_user_prompt(
    lokasi: str,
    berita_list: List[BeritaItem],
    cuaca: Optional[CuacaData],
    pm25_pred: Optional[float],
    risk_level: Optional[str],
    waktu_prediksi: str = "",
    polutan: Optional[dict] = None,
) -> str:
    # Format berita
    berita_text = ""
    for i, b in enumerate(berita_list[:8], 1):      # maks 8 berita
        berita_text += f"\n{i}. [{b.kategori.upper()}] {b.judul}"
        if b.tanggal:
            berita_text += f" ({b.tanggal})"
        if b.ringkasan and len(b.ringkasan) > 30:
            snippet = b.ringkasan[:300].rsplit(" ", 1)[0] + "..."
            berita_text += f"\n   {snippet}"
        if b.kata_kunci:
            berita_text += f"\n   Kata kunci: {b.kata_kunci}"

    # Format cuaca
    cuaca_text = "Tidak tersedia"
    if cuaca:
        parts = []
        if cuaca.suhu_celsius is not None:
            parts.append(f"Suhu: {cuaca.suhu_celsius:.1f}°C")
        if cuaca.kelembaban_persen is not None:
            parts.append(f"Kelembaban: {cuaca.kelembaban_persen:.0f}%")
        if cuaca.kecepatan_angin_ms is not None:
            parts.append(f"Angin: {cuaca.kecepatan_angin_ms:.1f} m/s")
        if cuaca.arah_angin:
            parts.append(f"Arah angin: {cuaca.arah_angin}")
        if cuaca.kondisi:
            parts.append(f"Kondisi: {cuaca.kondisi}")
        if cuaca.keterangan:
            parts.append(f"Keterangan: {cuaca.keterangan}")
        cuaca_text = " | ".join(parts) if parts else "Tidak tersedia"

    # Format PM2.5
    pm25_text = f"{pm25_pred:.2f} µg/m³" if pm25_pred is not None else "Tidak tersedia"
    risk_text = risk_level if risk_level else "Tidak diketahui"

    # Format polutan terukur (konsentrasi rata-rata kabupaten saat ini)
    polutan_text = "Tidak tersedia"
    if polutan:
        labels = {"co": "CO", "no2": "NO₂", "o3": "O₃", "so2": "SO₂",
                  "pm2_5": "PM2.5", "pm10": "PM10", "nh3": "NH₃"}
        parts = [f"{labels.get(k, k)}: {v} µg/m³" for k, v in polutan.items() if v is not None]
        dom = polutan.get("dominant")
        if dom:
            parts.append(f"Polutan dominan: {labels.get(dom, dom.upper())}")
        if parts:
            polutan_text = " | ".join(parts)

    prompt = textwrap.dedent(f"""
        === ANALISIS KUALITAS UDARA ===
        Lokasi        : {lokasi}
        {f'Waktu prediksi: {waktu_prediksi}' if waktu_prediksi else ''}

        --- DATA PREDIKSI PM2.5 ---
        Prediksi PM2.5: {pm25_text}
        Risk Level     : {risk_text}

        --- DATA POLUTAN TERUKUR SAAT INI ---
        {polutan_text}

        --- DATA CUACA ---
        {cuaca_text}

        --- BERITA / LAPORAN PUBLIK TERKINI ({len(berita_list)} berita) ---
        {berita_text if berita_text else 'Tidak ada berita terkait'}

        Berdasarkan semua data di atas, berikan analisis komprehensif dalam format JSON
        yang mencakup: ringkasan situasi, faktor penyebab pencemaran udara, rekomendasi
        mitigasi yang actionable, tingkat urgensi, dan kelompok rentan yang perlu diperhatikan.

        Faktor penyebab WAJIB didasarkan pada kombinasi berita terkini DAN data polutan
        terukur di atas (mis. polutan dominan, tingkat PM2.5/PM10/O₃) — kaitkan keduanya.
        Fokus pada relevansi berita terhadap kualitas udara (kemacetan, kebakaran,
        pembakaran sampah, polusi industri, cuaca ekstrem, aktivitas transportasi).
        Jika berita tidak relevan/kosong, gunakan data polutan terukur, cuaca, dan PM2.5
        sebagai dasar analisis.
    """).strip()

    return prompt


# ─── Provider adapters ───────────────────────────────────────────────────────

def _call_groq(
    api_keys,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    max_tokens: int,
) -> str:
    """
    Panggil Groq API dengan ROTASI banyak API key: kalau satu key kena rate
    limit / kuota habis (429), otomatis pindah ke key berikutnya. Kalau semua
    key kena 429 (limit per-menit), tunggu sebentar lalu ulangi sekali.
    """
    try:
        from groq import Groq
    except ImportError:
        raise ImportError("Install groq: pip install groq")

    keys = [k for k in (api_keys if isinstance(api_keys, (list, tuple)) else [api_keys]) if k]
    if not keys:
        raise ValueError("Tidak ada GROQ_API_KEY")

    last_exc = None
    for round_i in range(2):
        for key in keys:
            try:
                client = Groq(api_key=key)
                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format={"type": "json_object"},
                )
                return response.choices[0].message.content
            except Exception as exc:  # noqa: BLE001
                msg = str(exc)
                if "429" in msg or "rate limit" in msg.lower():
                    last_exc = exc
                    continue  # key ini limit/habis -> coba key berikutnya
                raise
        # Semua key kena 429 di ronde ini -> tunggu lalu ulang sekali.
        m = re.search(r"try again in ([\d.]+)s", str(last_exc) if last_exc else "")
        time.sleep(min((float(m.group(1)) + 0.5) if m else 8, 30))
    raise last_exc if last_exc else RuntimeError("Groq gagal setelah rotasi key")


def _call_openai(
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    max_tokens: int,
) -> str:
    """Panggil OpenAI API."""
    try:
        from openai import OpenAI
    except ImportError:
        raise ImportError("Install openai: pip install openai")

    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content


def _call_anthropic(
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    max_tokens: int,
) -> str:
    """Panggil Anthropic API."""
    try:
        import anthropic
    except ImportError:
        raise ImportError("Install anthropic: pip install anthropic")

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
        temperature=temperature,
    )
    return message.content[0].text


def _call_ollama(
    base_url: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    max_tokens: int,
) -> str:
    """Panggil Ollama lokal."""
    import urllib.request

    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "options": {"temperature": temperature, "num_predict": max_tokens},
    }).encode()

    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode())
    return data["message"]["content"]


# ─── Main class ──────────────────────────────────────────────────────────────

# Model default per provider
_DEFAULT_MODELS = {
    "groq":      "llama-3.3-70b-versatile",
    "openai":    "gpt-4o-mini",
    "anthropic": "claude-sonnet-4-6",
    "ollama":    "llama3.2",
}

# Model yang tersedia per provider (untuk UI dropdown)
AVAILABLE_MODELS = {
    "groq": [
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "mixtral-8x7b-32768",
        "gemma2-9b-it",
        "llama3-70b-8192",
        "llama3-8b-8192",
    ],
    "openai": [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4-turbo",
        "gpt-3.5-turbo",
    ],
    "anthropic": [
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
        "claude-opus-4-6",
    ],
    "ollama": [
        "llama3.2",
        "llama3.1",
        "mistral",
        "gemma2",
        "qwen2.5",
    ],
}


class AirQualityLLM:
    """
    Kelas utama untuk analisis LLM kualitas udara.

    Parameters
    ----------
    provider : str
        Provider LLM: 'groq', 'openai', 'anthropic', atau 'ollama'
    api_key : str, optional
        API key untuk provider berbasis cloud (wajib untuk groq/openai/anthropic)
    model : str, optional
        Nama model. Jika None, gunakan default untuk provider.
    ollama_url : str, optional
        Base URL untuk Ollama lokal (default: http://localhost:11434)
    temperature : float, optional
        Kreativitas model (0.0–1.0, default 0.2 untuk konsistensi analisis)
    max_tokens : int, optional
        Panjang respons maksimum (default 1500)
    """

    def __init__(
        self,
        provider: str = "groq",
        api_key: str = "",
        model: Optional[str] = None,
        ollama_url: str = "http://localhost:11434",
        temperature: float = 0.2,
        max_tokens: int = 1500,
        api_keys: Optional[list] = None,
    ):
        self.provider = provider.lower()
        self.api_key = api_key or os.getenv(self._env_key()) or ""
        # Daftar API key untuk rotasi otomatis (kalau satu kena limit/habis ->
        # tukar ke berikutnya). Bisa dari api_keys, atau api_key dipisah koma.
        raw = api_keys if api_keys else [k for k in self.api_key.split(",")]
        self.api_keys = [k.strip() for k in raw if k and k.strip()]
        self.model = model or _DEFAULT_MODELS.get(self.provider, "llama-3.3-70b-versatile")
        self.ollama_url = ollama_url
        self.temperature = temperature
        self.max_tokens = max_tokens

    def _env_key(self) -> str:
        return {
            "groq":      "GROQ_API_KEY",
            "openai":    "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
        }.get(self.provider, "")

    def analyze(
        self,
        berita_list: List[BeritaItem],
        lokasi: str,
        cuaca: Optional[CuacaData] = None,
        pm25_pred: Optional[float] = None,
        risk_level: Optional[str] = None,
        waktu_prediksi: str = "",
        polutan: Optional[dict] = None,
    ) -> AnalysisResult:
        """
        Analisis utama — menggabungkan berita, polutan terukur, cuaca, dan prediksi PM2.5.

        Returns
        -------
        AnalysisResult
        """
        system_prompt = _build_system_prompt()
        user_prompt = _build_user_prompt(
            lokasi=lokasi,
            berita_list=berita_list,
            cuaca=cuaca,
            pm25_pred=pm25_pred,
            risk_level=risk_level,
            waktu_prediksi=waktu_prediksi,
            polutan=polutan,
        )

        raw = ""
        try:
            raw = self._call_provider(system_prompt, user_prompt)
            result = self._parse_response(raw)
            result.raw_response = raw
            result.provider_used = f"{self.provider}/{self.model}"
            return result

        except Exception as exc:
            return AnalysisResult(
                error=str(exc),
                raw_response=raw,
                provider_used=f"{self.provider}/{self.model}",
                ringkasan_berita="Analisis tidak tersedia (terjadi kesalahan).",
                faktor_penyebab=["Data tidak dapat diproses oleh LLM."],
                rekomendasi_mitigasi=["Periksa koneksi dan API key, lalu coba lagi."],
            )

    def _call_provider(self, system_prompt: str, user_prompt: str) -> str:
        """Dispatch ke provider yang sesuai."""
        if self.provider == "groq":
            return _call_groq(
                self.api_keys or [self.api_key], self.model,
                system_prompt, user_prompt,
                self.temperature, self.max_tokens,
            )
        elif self.provider == "openai":
            return _call_openai(
                self.api_key, self.model,
                system_prompt, user_prompt,
                self.temperature, self.max_tokens,
            )
        elif self.provider == "anthropic":
            return _call_anthropic(
                self.api_key, self.model,
                system_prompt, user_prompt,
                self.temperature, self.max_tokens,
            )
        elif self.provider == "ollama":
            return _call_ollama(
                self.ollama_url, self.model,
                system_prompt, user_prompt,
                self.temperature, self.max_tokens,
            )
        else:
            raise ValueError(f"Provider tidak dikenal: '{self.provider}'. "
                             "Gunakan: groq, openai, anthropic, ollama")

    def _parse_response(self, raw: str) -> AnalysisResult:
        """Parse JSON dari respons LLM, dengan fallback ke regex."""
        # Coba langsung parse
        text = raw.strip()

        # Hapus markdown code fence jika ada
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)

        data = json.loads(text)

        return AnalysisResult(
            ringkasan_berita=data.get("ringkasan_berita", ""),
            faktor_penyebab=data.get("faktor_penyebab", []),
            rekomendasi_mitigasi=data.get("rekomendasi_mitigasi", []),
            tingkat_urgensi=data.get("tingkat_urgensi", "sedang"),
            kelompok_rentan=data.get("kelompok_rentan", []),
        )

    def test_connection(self) -> tuple[bool, str]:
        """
        Uji koneksi ke provider.
        Returns (success: bool, message: str)
        """
        try:
            system = "Kamu adalah asisten. Balas HANYA dengan JSON."
            user = 'Balas dengan: {"status": "ok", "pesan": "Koneksi berhasil"}'
            raw = self._call_provider(system, user)
            data = json.loads(raw.strip())
            if data.get("status") == "ok":
                return True, f"✅ Koneksi ke {self.provider}/{self.model} berhasil!"
            return True, f"✅ Provider merespons (model: {self.model})"
        except Exception as e:
            return False, f"❌ Koneksi gagal: {e}"


# ─── Quick test (jalankan langsung) ──────────────────────────────────────────
if __name__ == "__main__":
    import os

    # Contoh penggunaan dengan Groq
    llm = AirQualityLLM(
        provider="groq",
        api_key=os.getenv("GROQ_API_KEY", ""),
        model="llama-3.3-70b-versatile",
    )

    berita = [
        BeritaItem(
            judul="Kemacetan Parah di Jalan Sunset Road Denpasar",
            kategori="kemacetan",
            lokasi="denpasar",
            ringkasan="Kemacetan panjang terjadi akibat pelebaran jalan dan volume kendaraan tinggi pada jam sibuk sore hari.",
            kata_kunci="kemacetan, kendaraan, emisi, polusi",
        ),
        BeritaItem(
            judul="Kebakaran Lahan di Kawasan Bukit Badung",
            kategori="kebakaran",
            lokasi="badung",
            ringkasan="Kebakaran lahan seluas 5 hektar terjadi di kawasan perbukitan Badung, asap tebal menyebar ke permukiman.",
            kata_kunci="kebakaran, asap, lahan, polutan",
        ),
    ]

    cuaca = CuacaData(
        suhu_celsius=32.5,
        kelembaban_persen=78,
        kecepatan_angin_ms=1.2,
        arah_angin="Barat Daya",
        kondisi="Cerah berawan",
    )

    hasil = llm.analyze(
        berita_list=berita,
        lokasi="Denpasar",
        cuaca=cuaca,
        pm25_pred=45.8,
        risk_level="Tidak Sehat untuk Kelompok Sensitif",
        waktu_prediksi="17:00 WITA",
    )

    print("=== HASIL ANALISIS ===")
    print(f"Provider: {hasil.provider_used}")
    print(f"\nRingkasan:\n{hasil.ringkasan_berita}")
    print(f"\nFaktor Penyebab:")
    for f in hasil.faktor_penyebab:
        print(f"  • {f}")
    print(f"\nRekomendasi:")
    for r in hasil.rekomendasi_mitigasi:
        print(f"  → {r}")
    print(f"\nUrgensi   : {hasil.tingkat_urgensi}")
    print(f"Rentan    : {', '.join(hasil.kelompok_rentan)}")
    if hasil.error:
        print(f"\n⚠️  Error: {hasil.error}")
