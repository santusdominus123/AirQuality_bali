"""
============================================================
  NEWS SCRAPER INPUT MODEL — Berita Bali Hari Ini
  Untuk digunakan di Streamlit sebagai data input ke LLM

  Cara pakai di Streamlit:
    from news_scraper_input import get_berita_hari_ini, NewsInputModel

  Output:
    - List[dict] berisi artikel berita hari ini
    - Siap dikirim sebagai konteks ke LLM (Claude/GPT dll)
============================================================
"""

import os
import re
import json
import time
import hashlib
import logging
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime, date, timedelta
from threading import Lock
from typing import Optional
from urllib.parse import urljoin, quote_plus

import requests
from bs4 import BeautifulSoup

try:
    import feedparser
    HAS_FEEDPARSER = True
except ImportError:
    HAS_FEEDPARSER = False

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("NewsInputModel")


# ─────────────────────────────────────────────
# KONFIGURASI
# ─────────────────────────────────────────────

REQUEST_DELAY_MIN   = 0.3
REQUEST_DELAY_MAX   = 1.0
REQUEST_TIMEOUT     = 12
MAX_RETRIES         = 2
MAX_ARTICLE_WORKERS = 6
MAX_SOURCE_WORKERS  = 4
MAX_ARTICLES_PER_SOURCE = 30   # lebih kecil untuk kecepatan real-time

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

# ─────────────────────────────────────────────
# KATA KUNCI TOPIK
# ─────────────────────────────────────────────

KEYWORDS = {
    "kualitas_udara": [
        "kualitas udara", "polusi udara", "pencemaran udara", "polutan", "AQI", "ISPU",
        "PM2.5", "PM10", "kabut asap", "smog", "emisi kendaraan", "emisi", "udara tercemar", "debu",
    ],
    "cuaca": [
        "cuaca", "BMKG", "hujan", "angin kencang", "kemarau", "kabut", "suhu udara",
    ],
    "kemacetan": [
        "kemacetan", "macet", "lalu lintas", "lalin",
        "kepadatan kendaraan", "volume kendaraan", "rekayasa lalu lintas",
    ],
    "kebakaran": [
        "kebakaran lahan", "kebakaran hutan", "karhutla", "titik api",
        "asap kebakaran", "kebakaran",
    ],
    "sampah_lingkungan": [
        "pembakaran sampah", "bakar sampah", "pembakaran lahan", "asap pembakaran",
    ],
}

LOCATIONS = [
    "bali", "denpasar", "badung", "gianyar", "tabanan",
    "bangli", "karangasem", "buleleng", "jembrana", "klungkung",
    "kuta", "ubud", "sanur", "canggu", "seminyak", "legian",
    "uluwatu", "jimbaran", "nusa dua", "kerobokan", "singaraja",
]

NEGATIVE_KEYWORDS = [
    "pemilu", "pilkada", "drakor", "k-pop", "sinetron",
    "sepak bola", "premier league", "saham", "bitcoin",
    "vaksin", "ujian nasional", "beasiswa",
    # Off-topic yang sering nyasar (bukan kualitas udara):
    "santunan", "disdukcapil", "bpjs", "pelantikan", "lowongan",
    "mangrove", "terumbu karang", "perbaikan jalan", "perbaiki jalan",
    "anggaran", "umkm", "pariwisata",
]

# Topik inti = HANYA yang berkaitan dengan kualitas udara (polusi, asap,
# pembakaran, kebakaran, emisi kendaraan/kemacetan, cuaca yang memengaruhi
# dispersi). Sengaja sempit agar berita tak relevan tersaring.
CORE_TOPIC_KEYWORDS = [
    "udara", "polusi", "polutan", "asap", "kabut", "emisi", "ispu", "aqi",
    "pm2.5", "pm10", "smog", "debu", "pencemaran udara",
    "kebakaran", "terbakar", "karhutla", "titik api", "pembakaran",
    "kemacetan", "macet", "lalu lintas", "kendaraan",
    "cuaca", "bmkg", "hujan", "angin", "kemarau",
]

BALI_LOCATION_KEYWORDS = [
    "bali", "denpasar", "badung", "gianyar", "tabanan", "bangli",
    "karangasem", "buleleng", "jembrana", "klungkung",
    "kuta", "ubud", "sanur", "canggu", "seminyak", "legian",
    "nusa dua", "jimbaran", "uluwatu", "kerobokan", "singaraja",
    "ngurah rai", "tukad",
]


# ─────────────────────────────────────────────
# DATA MODEL
# ─────────────────────────────────────────────

@dataclass
class NewsArticle:
    """Model artikel berita."""
    id:          str = ""
    judul:       str = ""
    url:         str = ""
    tanggal:     str = ""       # format YYYY-MM-DD
    sumber:      str = ""
    kategori:    str = ""
    lokasi:      str = ""
    ringkasan:   str = ""
    konten:      str = ""
    kata_kunci:  list = field(default_factory=list)
    scrape_time: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["kata_kunci"] = ", ".join(d["kata_kunci"])
        return d

    def to_llm_context(self) -> str:
        """Format teks siap kirim ke LLM."""
        return (
            f"[{self.sumber}] {self.judul}\n"
            f"Tanggal: {self.tanggal} | Kategori: {self.kategori} | Lokasi: {self.lokasi}\n"
            f"Ringkasan: {self.ringkasan}\n"
            f"URL: {self.url}\n"
        )


# ─────────────────────────────────────────────
# INPUT MODEL UTAMA
# ─────────────────────────────────────────────

class NewsInputModel:
    """
    Input Model untuk scraping berita Bali hari ini.

    Cara pakai:
        model = NewsInputModel()
        articles = model.fetch()            # scrape semua sumber
        context  = model.to_llm_context()   # format untuk LLM

    Parameter:
        target_date  : tanggal target (default: hari ini)
        max_articles : batas total artikel
        sumber_aktif : list sumber yang diaktifkan (None = semua)
        progress_cb  : callback(persen, label) untuk Streamlit progress bar
    """

    def __init__(
        self,
        target_date: Optional[date] = None,
        max_articles: int = 100,
        sumber_aktif: Optional[list] = None,
        progress_cb=None,
    ):
        self.target_date  = target_date or date.today()
        self.max_articles = max_articles
        self.progress_cb  = progress_cb
        self.articles: list[NewsArticle] = []
        self._session     = self._make_session()

        # Daftar sumber tersedia
        self._all_sources = {
            "Google News RSS": self._scrape_google_news_rss,
            "Antara Bali":     self._scrape_antara_bali,
            "Nusa Bali":       self._scrape_nusa_bali,
            "Tribun Bali":     self._scrape_tribun_bali,
            "Detik Bali":      self._scrape_detik_bali,
            "Bali Post":       self._scrape_bali_post,
            "Radar Bali":      self._scrape_radar_bali,
        }

        self.sumber_aktif = (
            sumber_aktif if sumber_aktif else list(self._all_sources.keys())
        )

    # ── Daftar sumber yang tersedia ──────────────────────────────

    @property
    def available_sources(self) -> list:
        return list(self._all_sources.keys())

    # ── HTTP Session ─────────────────────────────────────────────

    def _make_session(self) -> requests.Session:
        s = requests.Session()
        s.headers.update({
            "Accept":          "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT":             "1",
            "Connection":      "keep-alive",
        })
        return s

    def _fetch(self, url: str) -> Optional[requests.Response]:
        if not url or not url.startswith(("http://", "https://")):
            return None
        self._session.headers["User-Agent"] = random.choice(USER_AGENTS)
        for attempt in range(MAX_RETRIES):
            try:
                r = self._session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
                if r.status_code == 200:
                    r.encoding = r.apparent_encoding or "utf-8"
                    return r
                if r.status_code in (403, 404):
                    return None
            except Exception:
                if attempt < MAX_RETRIES - 1:
                    time.sleep(1)
        return None

    def _safe_parse(self, content: bytes, encoding: str = "utf-8") -> BeautifulSoup:
        try:
            return BeautifulSoup(content, "lxml")
        except Exception:
            return BeautifulSoup(content, "html.parser")

    # ── Utilitas ─────────────────────────────────────────────────

    def _gen_id(self, url: str) -> str:
        return hashlib.md5(url.encode()).hexdigest()[:12]

    def _is_today(self, date_str: Optional[str]) -> bool:
        """Cek apakah tanggal = target_date. None dianggap lolos (tidak tahu tanggal)."""
        if not date_str:
            return True
        try:
            return datetime.strptime(date_str, "%Y-%m-%d").date() == self.target_date
        except ValueError:
            return True

    def _parse_date(self, raw: str) -> Optional[str]:
        """Parse berbagai format tanggal ke YYYY-MM-DD."""
        if not raw:
            return None
        raw = raw.strip()
        today = date.today()

        # ISO / RFC
        m = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
        if m:
            return m.group(1)

        # Relative
        if re.search(r"baru saja|just now|\d+ menit lalu|\d+ min", raw, re.I):
            return today.strftime("%Y-%m-%d")
        m2 = re.search(r"(\d+)\s*jam\s*lalu|(\d+)\s*hours?\s*ago", raw, re.I)
        if m2:
            n = int(m2.group(1) or m2.group(2))
            return (datetime.now() - timedelta(hours=n)).strftime("%Y-%m-%d")
        if re.search(r"kemarin|yesterday", raw, re.I):
            return (today - timedelta(days=1)).strftime("%Y-%m-%d")
        m3 = re.search(r"(\d+)\s*hari\s*lalu|(\d+)\s*days?\s*ago", raw, re.I)
        if m3:
            n = int(m3.group(1) or m3.group(2))
            return (today - timedelta(days=n)).strftime("%Y-%m-%d")

        # Nama bulan Indonesia
        BULAN = {
            "januari": "January", "februari": "February", "maret": "March",
            "april": "April", "mei": "May", "juni": "June",
            "juli": "July", "agustus": "August", "september": "September",
            "oktober": "October", "november": "November", "desember": "December",
        }
        raw_lower = raw.lower()
        for id_name, en_name in BULAN.items():
            raw_lower = raw_lower.replace(id_name, en_name.lower())

        for fmt in [
            "%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y",
            "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
            "%d/%m/%Y", "%d-%m-%Y",
        ]:
            try:
                return datetime.strptime(raw_lower.strip(), fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue

        # URL tanggal
        m4 = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", raw)
        if m4:
            y, mo, d2 = m4.groups()
            try:
                return f"{y}-{mo}-{d2}"
            except Exception:
                pass
        return None

    def _extract_date_from_soup(self, soup: BeautifulSoup, url: str = "") -> Optional[str]:
        """Ekstraksi tanggal dengan prioritas: JSON-LD → meta → time tag → URL."""
        # JSON-LD
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                items = data if isinstance(data, list) else [data]
                for item in items:
                    for field_name in ("datePublished", "dateModified", "uploadDate"):
                        val = item.get(field_name, "")
                        if val:
                            result = self._parse_date(str(val))
                            if result:
                                return result
            except Exception:
                pass

        # Meta tags
        for sel in [
            "meta[property='article:published_time']",
            "meta[name='article:published_time']",
            "meta[property='og:published_time']",
            "meta[name='publishdate']",
            "meta[itemprop='datePublished']",
        ]:
            tag = soup.select_one(sel)
            if tag:
                val = tag.get("content", "")
                if val:
                    result = self._parse_date(val)
                    if result:
                        return result

        # time tag
        for sel in ["time[datetime]", "[itemprop='datePublished']", "time", ".post-date", ".date"]:
            tag = soup.select_one(sel)
            if not tag:
                continue
            val = tag.get("datetime", "") or tag.get("content", "") or tag.get_text(strip=True)
            if val:
                result = self._parse_date(val)
                if result:
                    return result

        # URL fallback
        if url:
            result = self._parse_date(url)
            if result:
                return result
        return None

    def _is_relevant(self, title: str, content: str = "") -> bool:
        """Filter relevansi: lokasi Bali + topik kualitas udara, pakai word-boundary
        agar mis. 'Bandung' tidak cocok dengan 'Badung'."""
        title_lower = title.lower()
        text_lower  = (title + " " + content).lower()

        for neg in NEGATIVE_KEYWORDS:
            if neg in title_lower:
                return False

        def has_any(words):
            return any(re.search(r"\b" + re.escape(w) + r"\b", text_lower) for w in words)

        if not has_any(BALI_LOCATION_KEYWORDS):
            return False
        if not has_any(CORE_TOPIC_KEYWORDS):
            return False
        return True

    def _detect_category(self, text: str) -> str:
        text_lower = text.lower()
        scores = {cat: 0 for cat in KEYWORDS}
        for cat, kws in KEYWORDS.items():
            for kw in kws:
                if kw.lower() in text_lower:
                    scores[cat] += 1
        best = max(scores, key=scores.get)
        return best if scores[best] > 0 else "umum"

    def _detect_location(self, text: str) -> str:
        text_lower = text.lower()
        found = [loc for loc in LOCATIONS if loc in text_lower]
        return ", ".join(found) if found else "bali"

    def _extract_keywords(self, text: str) -> list:
        text_lower = text.lower()
        found = []
        for kws in KEYWORDS.values():
            for kw in kws:
                if kw.lower() in text_lower and kw not in found:
                    found.append(kw)
        return found[:10]

    def _build_article_from_url(
        self, url: str, sumber: str,
        title_sel: str, content_sel: str,
    ) -> Optional[NewsArticle]:
        """Fetch URL artikel dan buat NewsArticle."""
        if not url.startswith(("http://", "https://")):
            return None
        r = self._fetch(url)
        if not r:
            return None

        soup = self._safe_parse(r.content, r.encoding or "utf-8")
        title_tag = soup.select_one(title_sel)
        if not title_tag:
            return None
        title = title_tag.get_text(strip=True)
        if not title:
            return None

        date_str = self._extract_date_from_soup(soup, url)
        if not self._is_today(date_str):
            return None

        content_div = soup.select_one(content_sel)
        paragraphs  = content_div.select("p") if content_div else soup.select("article p")
        content     = " ".join(p.get_text(strip=True) for p in paragraphs)[:2000]

        desc_tag = soup.select_one("meta[name='description'], meta[property='og:description']")
        ringkasan = desc_tag.get("content", "") if desc_tag else content[:300]

        if not self._is_relevant(title, content):
            return None

        combined = title + " " + content
        return NewsArticle(
            id          = self._gen_id(url),
            judul       = title,
            url         = url,
            tanggal     = date_str or date.today().strftime("%Y-%m-%d"),
            sumber      = sumber,
            kategori    = self._detect_category(combined),
            lokasi      = self._detect_location(combined),
            ringkasan   = ringkasan[:500],
            konten      = content,
            kata_kunci  = self._extract_keywords(combined),
            scrape_time = datetime.now().isoformat(),
        )

    # ─────────────────────────────────────────────
    # SCRAPER PER SUMBER
    # ─────────────────────────────────────────────

    def _scrape_google_news_rss(self) -> list:
        """Sumber utama: Google News RSS — cepat & luas jangkauan."""
        # Query umum se-Bali + query per kabupaten/kota agar tiap daerah dapat berita
        # (tidak ada yang miss).
        _kabupaten = [
            "denpasar", "badung", "gianyar", "tabanan", "jembrana",
            "bangli", "klungkung", "karangasem", "buleleng",
        ]
        queries = [
            "kualitas udara bali",
            "polusi udara bali",
            "pencemaran udara bali",
            "kabut asap bali",
            "kebakaran lahan bali",
            "pembakaran sampah bali",
            "kemacetan bali",
            "emisi kendaraan bali",
            "cuaca ekstrem bali",
            "BMKG cuaca bali",
        ] + [
            f"polusi udara OR kualitas udara OR asap OR pembakaran sampah {kab}"
            for kab in _kabupaten
        ]
        articles = []
        seen_urls: set = set()
        today_str = self.target_date.strftime("%Y-%m-%d")

        for query in queries:
            # when:1d -> hanya berita 24 jam terakhir (current date).
            url = (
                f"https://news.google.com/rss/search"
                f"?q={quote_plus(query)}+when:1d"
                f"&hl=id&gl=ID&ceid=ID:id"
            )
            try:
                if HAS_FEEDPARSER:
                    feed = feedparser.parse(url)
                    entries = feed.entries
                else:
                    r = self._fetch(url)
                    if not r:
                        continue
                    soup_rss = BeautifulSoup(r.content, "xml")
                    entries  = []
                    for item in soup_rss.find_all("item"):
                        t = item.find("title")
                        lnk = item.find("link")
                        d   = item.find("description")
                        pub = item.find("pubDate")
                        entries.append({
                            "title":     t.get_text(strip=True) if t else "",
                            "link":      lnk.next_sibling.strip() if lnk and lnk.next_sibling else "",
                            "summary":   d.get_text(separator=" ", strip=True) if d else "",
                            "published": pub.get_text(strip=True) if pub else "",
                        })

                for entry in entries:
                    if hasattr(entry, "get"):
                        title   = entry.get("title", "")
                        lnk     = entry.get("link", "")
                        summary = entry.get("summary", "")
                        pub     = entry.get("published", "")
                    else:
                        title   = entry["title"]
                        lnk     = entry["link"]
                        summary = entry["summary"]
                        pub     = entry["published"]

                    if "<" in str(summary):
                        summary = BeautifulSoup(str(summary), "html.parser").get_text(" ", strip=True)

                    title, lnk, summary = str(title), str(lnk), str(summary)
                    if not title or not lnk or lnk in seen_urls:
                        continue

                    date_str = self._parse_date(str(pub)) or today_str
                    if not self._is_today(date_str):
                        continue

                    combined = f"{title} {summary} bali"
                    if not self._is_relevant(title, summary + " bali"):
                        continue

                    seen_urls.add(lnk)
                    articles.append(NewsArticle(
                        id          = self._gen_id(lnk),
                        judul       = title,
                        url         = lnk,
                        tanggal     = date_str,
                        sumber      = "Google News RSS",
                        kategori    = self._detect_category(combined),
                        lokasi      = self._detect_location(combined),
                        ringkasan   = summary[:500],
                        konten      = summary[:2000],
                        kata_kunci  = self._extract_keywords(combined),
                        scrape_time = datetime.now().isoformat(),
                    ))
            except Exception as e:
                logger.debug(f"[GoogleNewsRSS] Error query '{query}': {e}")

            time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

        return articles[:MAX_ARTICLES_PER_SOURCE]

    def _scrape_antara_bali(self) -> list:
        """Antara Bali — scraping halaman pencarian."""
        articles = []
        seen_urls: set = set()
        url_lock = Lock()
        queries = [
            "kualitas udara bali", "banjir bali", "macet bali",
            "kebakaran bali", "sampah bali", "cuaca bali",
        ]

        def collect_urls(query):
            local = []
            r = self._fetch(f"https://bali.antaranews.com/search?q={quote_plus(query)}")
            if not r:
                return local
            soup = self._safe_parse(r.content)
            for a in soup.select("a.simple-post-list__link, h2 a, h3 a"):
                href = a.get("href", "")
                full = urljoin("https://bali.antaranews.com", href)
                if not full.startswith("http"):
                    continue
                with url_lock:
                    if full in seen_urls:
                        continue
                    seen_urls.add(full)
                local.append(full)
            return local

        all_urls = []
        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = {ex.submit(collect_urls, q): q for q in queries}
            for future in as_completed(futures):
                all_urls.extend(future.result())

        def fetch_article(url):
            return self._build_article_from_url(
                url, "Antara Bali",
                "h1.post-title, h1.article__title, h1",
                ".post-content, .article__body, div.post-body",
            )

        with ThreadPoolExecutor(max_workers=MAX_ARTICLE_WORKERS) as ex:
            futures = [ex.submit(fetch_article, u) for u in all_urls[:60]]
            for future in as_completed(futures):
                art = future.result()
                if art:
                    articles.append(art)

        return articles[:MAX_ARTICLES_PER_SOURCE]

    def _scrape_nusa_bali(self) -> list:
        articles = []
        seen_urls: set = set()
        url_lock = Lock()

        def collect_page(section, page):
            urls = []
            r = self._fetch(f"https://www.nusabali.com{section}?page={page}")
            if not r:
                return urls
            soup = self._safe_parse(r.content)
            for a in soup.select("h2 a, h3 a, article a"):
                href = a.get("href", "")
                full = urljoin("https://www.nusabali.com", href)
                if not full.startswith("https://www.nusabali.com"):
                    continue
                with url_lock:
                    if full in seen_urls:
                        continue
                    seen_urls.add(full)
                urls.append(full)
            return urls

        sections = ["/berita/lingkungan", "/berita/lalu-lintas", "/berita/bali", "/berita/denpasar"]
        all_urls = []
        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = {ex.submit(collect_page, s, 1): s for s in sections}
            for future in as_completed(futures):
                all_urls.extend(future.result())

        def fetch_article(url):
            return self._build_article_from_url(
                url, "Nusa Bali",
                "h1", ".post-content, .article-content, .content-detail",
            )

        with ThreadPoolExecutor(max_workers=MAX_ARTICLE_WORKERS) as ex:
            futures = [ex.submit(fetch_article, u) for u in all_urls[:50]]
            for future in as_completed(futures):
                art = future.result()
                if art:
                    articles.append(art)

        return articles[:MAX_ARTICLES_PER_SOURCE]

    def _scrape_tribun_bali(self) -> list:
        articles = []
        seen_urls: set = set()
        url_lock = Lock()

        def collect_page(section):
            urls = []
            r = self._fetch(f"https://bali.tribunnews.com{section}?page=1")
            if not r:
                return urls
            soup = self._safe_parse(r.content)
            for a in soup.select("h2 a, h3 a, .title a"):
                href = a.get("href", "")
                full = href if href.startswith("http") else urljoin("https://bali.tribunnews.com", href)
                if "tribunnews.com" not in full:
                    continue
                with url_lock:
                    if full in seen_urls:
                        continue
                    seen_urls.add(full)
                urls.append(full)
            return urls

        sections = ["/bali", "/lalu-lintas", "/lingkungan", "/denpasar"]
        all_urls = []
        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = [ex.submit(collect_page, s) for s in sections]
            for future in as_completed(futures):
                all_urls.extend(future.result())

        def fetch_article(url):
            return self._build_article_from_url(
                url, "Tribun Bali",
                "h1.title, h1",
                ".detail-content, .article-content, article",
            )

        with ThreadPoolExecutor(max_workers=MAX_ARTICLE_WORKERS) as ex:
            futures = [ex.submit(fetch_article, u) for u in all_urls[:50]]
            for future in as_completed(futures):
                art = future.result()
                if art:
                    articles.append(art)

        return articles[:MAX_ARTICLES_PER_SOURCE]

    def _scrape_detik_bali(self) -> list:
        articles = []
        seen_urls: set = set()
        url_lock = Lock()
        queries  = ["kualitas udara bali", "banjir bali", "macet bali", "kebakaran bali"]

        def collect_query(query):
            urls = []
            r = self._fetch(
                f"https://www.detik.com/search/searchall"
                f"?query={quote_plus(query)}&siteid=61&sortby=time&page=1"
            )
            if not r:
                return urls
            soup = self._safe_parse(r.content)
            for a in soup.select("article a, .list-content__item a, h2 a"):
                href = a.get("href", "")
                full = href if href.startswith("http") else urljoin("https://www.detik.com", href)
                if "detik.com" not in full:
                    continue
                with url_lock:
                    if full in seen_urls:
                        continue
                    seen_urls.add(full)
                urls.append(full)
            return urls

        all_urls = []
        with ThreadPoolExecutor(max_workers=3) as ex:
            futures = [ex.submit(collect_query, q) for q in queries]
            for future in as_completed(futures):
                all_urls.extend(future.result())

        def fetch_article(url):
            return self._build_article_from_url(
                url, "Detik Bali",
                "h1.detail__title, h1.title, h1",
                "div.detail__body, div.detail-body, article",
            )

        with ThreadPoolExecutor(max_workers=MAX_ARTICLE_WORKERS) as ex:
            futures = [ex.submit(fetch_article, u) for u in all_urls[:50]]
            for future in as_completed(futures):
                art = future.result()
                if art:
                    articles.append(art)

        return articles[:MAX_ARTICLES_PER_SOURCE]

    def _scrape_bali_post(self) -> list:
        articles = []
        seen_urls: set = set()
        url_lock = Lock()

        def collect_page(cat, page):
            urls = []
            r = self._fetch(f"https://www.balipost.com{cat}/page/{page}")
            if not r:
                return urls
            soup = self._safe_parse(r.content)
            for a in soup.select("h2 a, h3 a, .entry-title a, article a"):
                href = a.get("href", "")
                full = href if href.startswith("http") else urljoin("https://www.balipost.com", href)
                if "balipost.com" not in full:
                    continue
                with url_lock:
                    if full in seen_urls:
                        continue
                    seen_urls.add(full)
                urls.append(full)
            return urls

        categories = ["/kategori/bali", "/kategori/lingkungan", "/kategori/metropolitan"]
        all_urls = []
        with ThreadPoolExecutor(max_workers=3) as ex:
            futures = {ex.submit(collect_page, c, 1): c for c in categories}
            for future in as_completed(futures):
                all_urls.extend(future.result())

        def fetch_article(url):
            return self._build_article_from_url(
                url, "Bali Post",
                "h1.entry-title, h1.post-title, h1",
                ".entry-content, .post-content, article",
            )

        with ThreadPoolExecutor(max_workers=MAX_ARTICLE_WORKERS) as ex:
            futures = [ex.submit(fetch_article, u) for u in all_urls[:40]]
            for future in as_completed(futures):
                art = future.result()
                if art:
                    articles.append(art)

        return articles[:MAX_ARTICLES_PER_SOURCE]

    def _scrape_radar_bali(self) -> list:
        articles = []
        seen_urls: set = set()
        url_lock = Lock()

        def collect_section(section):
            urls = []
            r = self._fetch(f"https://radarbali.jawapos.com{section}")
            if not r:
                return urls
            soup = self._safe_parse(r.content)
            for a in soup.select("article a, .card-body a, h2 a, h3 a"):
                href = a.get("href", "")
                full = href if href.startswith("http") else urljoin("https://radarbali.jawapos.com", href)
                with url_lock:
                    if full in seen_urls:
                        continue
                    seen_urls.add(full)
                urls.append(full)
            return urls

        sections = ["/denpasar", "/badung", "/lingkungan", "/bali"]
        all_urls = []
        with ThreadPoolExecutor(max_workers=3) as ex:
            futures = [ex.submit(collect_section, s) for s in sections]
            for future in as_completed(futures):
                all_urls.extend(future.result())

        def fetch_article(url):
            return self._build_article_from_url(
                url, "Radar Bali",
                "h1",
                ".post-content, .detail-body, .article-body, article",
            )

        with ThreadPoolExecutor(max_workers=MAX_ARTICLE_WORKERS) as ex:
            futures = [ex.submit(fetch_article, u) for u in all_urls[:40]]
            for future in as_completed(futures):
                art = future.result()
                if art:
                    articles.append(art)

        return articles[:MAX_ARTICLES_PER_SOURCE]

    # ─────────────────────────────────────────────
    # FETCH UTAMA
    # ─────────────────────────────────────────────

    def fetch(self) -> list[NewsArticle]:
        """
        Jalankan scraping dari semua sumber aktif.
        Kembalikan list NewsArticle hari ini.
        """
        all_articles: list[NewsArticle] = []
        seen_ids: set = set()
        results_lock = Lock()

        active_scrapers = {
            name: fn
            for name, fn in self._all_sources.items()
            if name in self.sumber_aktif
        }

        total = len(active_scrapers)

        def run_scraper(name: str, fn):
            try:
                arts = fn()
                logger.debug(f"[{name}] {len(arts)} artikel hari ini")
                return arts
            except Exception as e:
                logger.warning(f"[{name}] Error: {e}")
                return []

        completed = 0
        with ThreadPoolExecutor(max_workers=MAX_SOURCE_WORKERS) as ex:
            futures = {ex.submit(run_scraper, name, fn): name for name, fn in active_scrapers.items()}
            for future in as_completed(futures):
                arts = future.result()
                completed += 1
                with results_lock:
                    for art in arts:
                        if art.id not in seen_ids:
                            seen_ids.add(art.id)
                            all_articles.append(art)

                if self.progress_cb:
                    persen = int((completed / total) * 100)
                    label  = futures[future]
                    self.progress_cb(persen, label)

        # Urutkan terbaru dulu
        all_articles.sort(key=lambda a: a.tanggal or "0000-00-00", reverse=True)
        self.articles = all_articles[: self.max_articles]
        return self.articles

    # ─────────────────────────────────────────────
    # OUTPUT UNTUK LLM
    # ─────────────────────────────────────────────

    def to_llm_context(
        self,
        max_articles: Optional[int] = None,
        kategori: Optional[str] = None,
    ) -> str:
        """
        Format artikel jadi satu string konteks untuk LLM.

        Parameter:
            max_articles : batas artikel yang dimasukkan ke konteks
            kategori     : filter berdasarkan kategori ("kualitas_udara", "cuaca", dll)
        """
        articles = self.articles
        if kategori:
            articles = [a for a in articles if a.kategori == kategori]
        if max_articles:
            articles = articles[:max_articles]

        if not articles:
            return "Tidak ada berita yang ditemukan hari ini."

        today_str = self.target_date.strftime("%d %B %Y")
        lines = [
            f"=== BERITA BALI HARI INI ({today_str}) ===",
            f"Total artikel: {len(articles)}",
            "",
        ]
        for i, art in enumerate(articles, 1):
            lines.append(f"--- Artikel {i} ---")
            lines.append(art.to_llm_context())

        return "\n".join(lines)

    def to_list_dict(self) -> list[dict]:
        """Kembalikan list of dict untuk ditampilkan di Streamlit DataFrame."""
        return [a.to_dict() for a in self.articles]

    def summary(self) -> dict:
        """Ringkasan statistik scraping."""
        from collections import Counter
        return {
            "total_artikel": len(self.articles),
            "tanggal":       self.target_date.strftime("%Y-%m-%d"),
            "per_sumber":    dict(Counter(a.sumber for a in self.articles)),
            "per_kategori":  dict(Counter(a.kategori for a in self.articles)),
            "per_lokasi":    dict(Counter(
                loc for a in self.articles for loc in a.lokasi.split(", ")
            )),
        }


# ─────────────────────────────────────────────
# FUNGSI SHORTCUT (untuk Streamlit)
# ─────────────────────────────────────────────

def get_berita_hari_ini(
    max_articles: int = 100,
    sumber_aktif: Optional[list] = None,
    progress_cb=None,
) -> tuple[list[NewsArticle], str]:
    """
    Shortcut: Ambil berita hari ini dan kembalikan (articles, llm_context).

    Contoh di Streamlit:
        articles, context = get_berita_hari_ini(max_articles=50)
        st.dataframe([a.to_dict() for a in articles])
        # kirim context ke LLM:
        response = claude.messages.create(..., content=context + "\\n\\n" + user_question)
    """
    model = NewsInputModel(
        max_articles=max_articles,
        sumber_aktif=sumber_aktif,
        progress_cb=progress_cb,
    )
    articles = model.fetch()
    context  = model.to_llm_context()
    return articles, context


# ─────────────────────────────────────────────
# CONTOH PENGGUNAAN DI STREAMLIT
# ─────────────────────────────────────────────

STREAMLIT_EXAMPLE = '''
"""
Contoh integrasi di app Streamlit Anda:
"""
import streamlit as st
import anthropic
from news_scraper_input import NewsInputModel

st.title("🌴 Bali News AI — Berita Hari Ini")

# ── Sidebar: pilih sumber ──
model_tmp   = NewsInputModel()
all_sources = model_tmp.available_sources

with st.sidebar:
    st.header("⚙️ Pengaturan")
    sumber_dipilih = st.multiselect(
        "Pilih sumber berita:",
        options=all_sources,
        default=all_sources,
    )
    max_art = st.slider("Maks. artikel:", 20, 200, 80)
    kategori_filter = st.selectbox(
        "Filter kategori:",
        ["semua", "kualitas_udara", "cuaca", "kemacetan", "kebakaran", "sampah_lingkungan"],
    )

# ── Tombol scrape ──
if st.button("🔄 Ambil Berita Hari Ini", type="primary"):
    progress_bar = st.progress(0)
    status_text  = st.empty()

    def update_progress(persen, label):
        progress_bar.progress(persen)
        status_text.text(f"Sedang scraping: {label}...")

    with st.spinner("Mengambil berita..."):
        model = NewsInputModel(
            max_articles=max_art,
            sumber_aktif=sumber_dipilih,
            progress_cb=update_progress,
        )
        articles = model.fetch()

    progress_bar.progress(100)
    status_text.empty()

    if not articles:
        st.warning("Tidak ada berita ditemukan hari ini.")
        st.stop()

    st.session_state["articles"] = articles
    st.session_state["model"]    = model

    # Tampilkan ringkasan
    summary = model.summary()
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Artikel", summary["total_artikel"])
    col2.metric("Sumber Aktif", len(summary["per_sumber"]))
    col3.metric("Tanggal", summary["tanggal"])

    # Tabel artikel
    import pandas as pd
    df = pd.DataFrame(model.to_list_dict())
    if kategori_filter != "semua":
        df = df[df["kategori"] == kategori_filter]
    st.dataframe(df[["judul", "sumber", "kategori", "lokasi", "tanggal", "url"]])

# ── Chat dengan LLM ──
if "model" in st.session_state:
    st.divider()
    st.subheader("💬 Tanya tentang berita hari ini")

    user_q = st.text_input("Pertanyaan Anda:", placeholder="Bagaimana kondisi udara Bali hari ini?")

    if user_q:
        model: NewsInputModel = st.session_state["model"]
        kat = None if kategori_filter == "semua" else kategori_filter
        context = model.to_llm_context(max_articles=30, kategori=kat)

        client = anthropic.Anthropic()
        with st.spinner("AI sedang menjawab..."):
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                system=(
                    "Kamu adalah asisten berita Bali yang membantu. "
                    "Jawab berdasarkan data berita di bawah ini saja. "
                    "Jika informasi tidak ada di data, katakan tidak tersedia."
                ),
                messages=[{
                    "role": "user",
                    "content": f"{context}\\n\\nPertanyaan: {user_q}",
                }],
            )
        st.write(response.content[0].text)
'''


# ─────────────────────────────────────────────
# MAIN (test standalone)
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("=== TEST NewsInputModel ===")
    print(f"Target date: {date.today()}")
    print("Memulai scraping (Google News RSS saja untuk test cepat)...\n")

    model = NewsInputModel(
        max_articles=20,
        sumber_aktif=["Google News RSS"],
    )
    articles = model.fetch()

    print(f"\nTotal artikel: {len(articles)}")
    for i, art in enumerate(articles[:5], 1):
        print(f"\n[{i}] {art.judul}")
        print(f"    Sumber   : {art.sumber}")
        print(f"    Tanggal  : {art.tanggal}")
        print(f"    Kategori : {art.kategori}")
        print(f"    Lokasi   : {art.lokasi}")

    print("\n--- CONTOH KONTEKS LLM (5 artikel pertama) ---")
    print(model.to_llm_context(max_articles=5))

    print("\n--- RINGKASAN ---")
    import json as _json
    print(_json.dumps(model.summary(), ensure_ascii=False, indent=2))
