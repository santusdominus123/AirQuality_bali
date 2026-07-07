const icons = {
  grid: '<svg viewBox="0 0 24 24"><rect x="4" y="4" width="6" height="6" rx="1"/><rect x="14" y="4" width="6" height="6" rx="1"/><rect x="4" y="14" width="6" height="6" rx="1"/><rect x="14" y="14" width="6" height="6" rx="1"/></svg>',
  flask: '<svg viewBox="0 0 24 24"><path d="M9 3h6M10 3v6l-5 8.5A2.3 2.3 0 0 0 7 21h10a2.3 2.3 0 0 0 2-3.5L14 9V3M8 15h8"/></svg>',
  history: '<svg viewBox="0 0 24 24"><path d="M4 12a8 8 0 1 0 2.3-5.7L4 8.5M4 4v4.5h4.5M12 7v5l3 2"/></svg>',
  chart: '<svg viewBox="0 0 24 24"><path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/></svg>',
  news: '<svg viewBox="0 0 24 24"><path d="M5 5h12v14H5zM17 8h2v10a1 1 0 0 1-1 1M8 9h6M8 13h6M8 16h4"/></svg>',
  alert: '<svg viewBox="0 0 24 24"><path d="M12 4 3 20h18L12 4ZM12 9v5M12 17h.01"/></svg>',
  spark: '<svg viewBox="0 0 24 24"><path d="M12 3 9.8 9.8 3 12l6.8 2.2L12 21l2.2-6.8L21 12l-6.8-2.2L12 3Z"/></svg>',
  expand: '<svg viewBox="0 0 24 24"><path d="M8 3H3v5M16 3h5v5M8 21H3v-5M16 21h5v-5"/></svg>',
  shield: '<svg viewBox="0 0 24 24"><path d="M12 3 5 6v5c0 4.5 2.8 8 7 10 4.2-2 7-5.5 7-10V6l-7-3ZM9 12l2 2 4-4"/></svg>',
  info: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></svg>',
  search: '<svg viewBox="0 0 24 24"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/></svg>',
  download: '<svg viewBox="0 0 24 24"><path d="M12 3v12M8 11l4 4 4-4M4 20h16"/></svg>',
  calendar: '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4M16 3v4M3 10h18"/></svg>',
  award: '<svg viewBox="0 0 24 24"><circle cx="12" cy="9" r="6"/><path d="m8 14-1 7 5-3 5 3-1-7"/></svg>',
  check: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="m8 12 2.5 2.5L16 9"/></svg>'
};

function injectIcons(root = document) {
  root.querySelectorAll('[data-icon]').forEach((el) => {
    el.innerHTML = icons[el.dataset.icon] || '';
  });
}
injectIcons();

// ============================================================
// NAVIGATION + SHELL
// ============================================================
const pages = document.querySelectorAll('.page');
const navItems = document.querySelectorAll('.nav-item');
const sidebar = document.getElementById('sidebar');
const sidebarBackdrop = document.getElementById('sidebarBackdrop');
let map;

function openPage(pageName) {
  pages.forEach((page) => page.classList.toggle('active', page.id === `page-${pageName}`));
  navItems.forEach((item) => item.classList.toggle('active', item.dataset.page === pageName));
  window.location.hash = pageName;
  sidebar.classList.remove('open');
  sidebarBackdrop.classList.remove('show');
  if (pageName === 'dashboard' && map) setTimeout(() => map.invalidateSize(), 80);
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

navItems.forEach((item) => item.addEventListener('click', () => openPage(item.dataset.page)));
document.getElementById('menuToggle').addEventListener('click', () => {
  sidebar.classList.toggle('open');
  sidebarBackdrop.classList.toggle('show');
});
sidebarBackdrop.addEventListener('click', () => {
  sidebar.classList.remove('open');
  sidebarBackdrop.classList.remove('show');
});

function showToast(message) {
  const toast = document.getElementById('toast');
  document.getElementById('toastMessage').textContent = message;
  toast.classList.add('show');
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.remove('show'), 2400);
}

// ============================================================
// AQI CATEGORIES + NARRATIVE CONTENT
// ============================================================
function getAqiCategory(aqi) {
  if (aqi <= 50) return { label: 'Baik', color: '#00e400' };
  if (aqi <= 100) return { label: 'Sedang', color: '#ffff00' };
  if (aqi <= 150) return { label: 'Tidak Sehat (Populasi Sensitif)', color: '#ff7e00' };
  if (aqi <= 200) return { label: 'Tidak Sehat', color: '#ff0000' };
  if (aqi <= 300) return { label: 'Sangat Tidak Sehat', color: '#8f3f97' };
  return { label: 'Berbahaya', color: '#7e0023' };
}
function catKey(aqi) {
  if (aqi <= 50) return 'baik';
  if (aqi <= 100) return 'sedang';
  if (aqi <= 150) return 'sensitif';
  if (aqi <= 200) return 'tidakSehat';
  if (aqi <= 300) return 'sangat';
  return 'berbahaya';
}
// Warna AQI standar (mis. Sedang = #ffff00) terlalu menyilaukan untuk bar UI di
// atas latar putih. Versi lembut ini hanya dipakai untuk meter, bukan marker peta.
const meterColors = {
  baik: '#3cc26b', sedang: '#f4b740', sensitif: '#f5832b',
  tidakSehat: '#ec5b4d', sangat: '#9b59b6', berbahaya: '#9c2742'
};
const statusDesc = {
  baik: 'Udara bersih dan ideal untuk segala aktivitas luar ruangan.',
  sedang: 'Kualitas udara masih dapat diterima, kelompok sensitif tetap perlu waspada.',
  sensitif: 'Kelompok sensitif mulai merasakan dampak, batasi aktivitas berat di luar.',
  tidakSehat: 'Sebagian orang dapat mengalami gangguan kesehatan, kurangi aktivitas luar ruangan.',
  sangat: 'Peringatan kesehatan, hindari aktivitas di luar ruangan.',
  berbahaya: 'Kondisi darurat, seluruh warga berisiko terdampak masalah kesehatan.'
};
const causes = {
  baik: ['Sirkulasi udara baik dan polutan tersebar merata.', 'Aktivitas kendaraan relatif rendah.', 'Tidak ada sumber pembakaran yang signifikan.'],
  sedang: ['Tidak ada hujan, sehingga polutan tidak terbawa turun.', 'Aktivitas kendaraan meningkat pada jam sibuk.', 'Terdapat indikasi asap dari pembakaran sampah lokal.'],
  sensitif: ['Lalu lintas padat meningkatkan emisi kendaraan.', 'Angin lemah menahan polutan di permukaan.', 'Pembakaran sampah terpantau di beberapa titik.'],
  tidakSehat: ['Akumulasi polutan akibat cuaca kering berkepanjangan.', 'Kepadatan kendaraan tinggi hampir sepanjang hari.', 'Asap pembakaran lahan dan sampah meningkat.'],
  sangat: ['Inversi suhu menjebak polutan di lapisan bawah.', 'Emisi kendaraan dan aktivitas industri terakumulasi.', 'Terindikasi pembakaran skala besar di sekitar wilayah.'],
  berbahaya: ['Polusi ekstrem akibat kombinasi pembakaran dan cuaca stagnan.', 'Tidak ada hujan untuk meluruhkan partikel di udara.', 'Sejumlah sumber emisi aktif dalam waktu bersamaan.']
};
const recommendations = {
  baik: ['Manfaatkan udara bersih untuk aktivitas luar ruangan.', 'Tetap pantau kondisi kualitas udara secara berkala.'],
  sedang: ['Kurangi aktivitas luar ruangan saat kualitas udara memburuk.', 'Gunakan masker saat beraktivitas di luar ruangan.'],
  sensitif: ['Kelompok sensitif sebaiknya membatasi aktivitas di luar.', 'Gunakan masker ketika berada di luar ruangan.'],
  tidakSehat: ['Batasi aktivitas luar ruangan, terutama olahraga berat.', 'Gunakan masker dan nyalakan pemurni udara bila tersedia.'],
  sangat: ['Hindari aktivitas luar ruangan.', 'Tutup ventilasi dan gunakan pemurni udara di dalam ruangan.'],
  berbahaya: ['Tetap berada di dalam ruangan sepanjang hari.', 'Gunakan masker N95 dan pemurni udara, ikuti arahan otoritas setempat.']
};
const news = {
  baik: 'Kondisi udara terpantau kondusif dengan lalu lintas relatif lancar di sebagian besar wilayah.',
  sedang: 'Terpantau peningkatan kemacetan dan keluhan asap pembakaran sampah di beberapa wilayah.',
  sensitif: 'Kepadatan lalu lintas dan munculnya kabut tipis terpantau terutama pada pagi hari.',
  tidakSehat: 'Warga mengeluhkan kabut asap dan menurunnya jarak pandang di sejumlah ruas.',
  sangat: 'Muncul imbauan pembatasan aktivitas luar akibat memburuknya kualitas udara.',
  berbahaya: 'Kondisi darurat udara dengan imbauan agar warga tetap berada di dalam ruangan.'
};

const POLLUTANTS = [
  { key: 'co', label: 'CO', color: '#36b9a1' },
  { key: 'no', label: 'NO', color: '#2ec9a7' },
  { key: 'no2', label: 'NO₂', color: '#17c9a3' },
  { key: 'o3', label: 'O₃', color: '#54b8e8' },
  { key: 'so2', label: 'SO₂', color: '#8f78d8' },
  { key: 'pm2_5', label: 'PM2.5', color: '#f4b740' },
  { key: 'pm10', label: 'PM10', color: '#ef886f' },
  { key: 'nh3', label: 'NH₃', color: '#e0846a' }
];
const MONTHS = ['Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni', 'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember'];
// Nama polutan versi mudah dimengerti — untuk menjelaskan "kenapa AQI segini".
const POLLUTANT_NAMES = {
  o3: 'Ozon (O₃)', pm2_5: 'Partikel halus (PM2.5)', pm10: 'Partikel kasar (PM10)',
  co: 'Karbon monoksida (CO)', no2: 'Nitrogen dioksida (NO₂)',
  so2: 'Sulfur dioksida (SO₂)', nh3: 'Amonia (NH₃)', no: 'Nitrogen monoksida (NO)'
};


// ============================================================
// CHART RENDERER
// ============================================================
function niceMax(peak) {
  if (!isFinite(peak) || peak <= 0) return 1;
  peak *= 1.08;
  const mag = Math.pow(10, Math.floor(Math.log10(peak)));
  const n = peak / mag;
  const nice = n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10;
  return nice * mag;
}
function fmtAxis(v) {
  if (v >= 100) return String(Math.round(v));
  if (v >= 10) return v.toFixed(0);
  if (v >= 1) return v.toFixed(1);
  return v.toFixed(2);
}

function renderChart(element, series, opts = {}) {
  const width = 900;
  const height = 200;
  const pad = { x: 34, y: 16, bottom: 24 };
  const usableW = width - pad.x * 2;
  const usableH = height - pad.y - pad.bottom;
  const max = niceMax(Math.max(...series.flatMap((line) => line.values), 0));
  const step = max / 4;
  const count = series[0].values.length;
  const point = (value, index) => `${pad.x + (index / (count - 1)) * usableW},${pad.y + usableH - (value / max) * usableH}`;
  const grid = [0, step, step * 2, step * 3, max].map((value) => {
    const y = pad.y + usableH - (value / max) * usableH;
    return `<line x1="${pad.x}" x2="${width - pad.x}" y1="${y}" y2="${y}" stroke="#edf0f2" stroke-width="1"/><text x="2" y="${y + 3}" fill="#a4adb5" font-size="8">${fmtAxis(value)}</text>`;
  }).join('');
  const labelSource = opts.labels && opts.labels.length === count ? opts.labels : null;
  const labels = Array.from({ length: 7 }, (_, i) => {
    const idx = Math.round((i / 6) * (count - 1));
    const text = labelSource ? labelSource[idx] : `${String(Math.round((i / 6) * 24)).padStart(2, '0')}:00`;
    return `<text x="${pad.x + (idx / (count - 1)) * usableW}" y="${height - 4}" text-anchor="middle" fill="#a4adb5" font-size="8">${text}</text>`;
  }).join('');
  const paths = series.map((line, lineIndex) => {
    const points = line.values.map((value, index) => point(value, index)).join(' ');
    const area = `${pad.x},${pad.y + usableH} ${points} ${width - pad.x},${pad.y + usableH}`;
    return `<defs><linearGradient id="fill-${element.dataset.chart}-${lineIndex}" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="${line.color}" stop-opacity=".18"/><stop offset="100%" stop-color="${line.color}" stop-opacity=".01"/></linearGradient></defs><polygon points="${area}" fill="url(#fill-${element.dataset.chart}-${lineIndex})"/><polyline points="${points}" fill="none" stroke="${line.color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
  }).join('');
  element.innerHTML = `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="Grafik kualitas udara">${grid}${labels}${paths}<line class="hover-line" x1="0" x2="0" y1="${pad.y}" y2="${pad.y + usableH}" stroke="#8c98a2" stroke-dasharray="3 3" opacity="0"/>${series.map((line, index) => `<circle class="hover-dot hover-dot-${index}" r="4" fill="${line.color}" stroke="white" stroke-width="2" opacity="0"/>`).join('')}</svg><div class="chart-tooltip"></div>`;
  const svg = element.querySelector('svg');
  const tooltip = element.querySelector('.chart-tooltip');
  svg.addEventListener('pointermove', (event) => {
    const rect = svg.getBoundingClientRect();
    const x = Math.max(pad.x, Math.min(width - pad.x, ((event.clientX - rect.left) / rect.width) * width));
    const idx = Math.round(((x - pad.x) / usableW) * (count - 1));
    const head = labelSource ? labelSource[idx] : `${String(idx).padStart(2, '0')}:00`;
    const snapX = pad.x + (idx / (count - 1)) * usableW;
    svg.querySelector('.hover-line').setAttribute('x1', snapX);
    svg.querySelector('.hover-line').setAttribute('x2', snapX);
    svg.querySelector('.hover-line').setAttribute('opacity', '1');
    series.forEach((line, lineIndex) => {
      const y = pad.y + usableH - (line.values[idx] / max) * usableH;
      const dot = svg.querySelector(`.hover-dot-${lineIndex}`);
      dot.setAttribute('cx', snapX);
      dot.setAttribute('cy', y);
      dot.setAttribute('opacity', '1');
    });
    tooltip.innerHTML = `<b>${head}</b>${series.map((line) => `<span><i style="background:${line.color}"></i>${line.name}<strong>${line.values[idx]}${line.unit}</strong></span>`).join('')}`;
    const tooltipX = (snapX / width) * rect.width;
    tooltip.style.left = `${Math.min(Math.max(tooltipX, 70), rect.width - 70)}px`;
    tooltip.style.top = `${Math.max(8, event.clientY - rect.top - 78)}px`;
    tooltip.classList.add('show');
  });
  svg.addEventListener('pointerleave', () => {
    svg.querySelectorAll('.hover-line,.hover-dot').forEach((node) => node.setAttribute('opacity', '0'));
    tooltip.classList.remove('show');
  });
}

// ============================================================
// STATE (filled after data loads)
// ============================================================
const DATA = {};
let locations = [];
const markers = {};
let activeLocation = null;
let historyData = [];
let filteredHistory = [];
let currentPage = 1;
const rowsPerPage = 20;
let categoryFilter = 'all';
const pollutantSel = { pollutant: 'pm2_5', forecast: 'pm2_5' };
const clampMeter = (value) => `${Math.max(6, Math.min(100, Math.round(value)))}%`;

// ============================================================
// DASHBOARD: SELECT A LOCATION (drives all map-linked widgets)
// ============================================================
function selectLocation(loc) {
  activeLocation = loc;
  const cat = catKey(loc.aqi);
  const meterColor = meterColors[cat];
  const tr = DATA.trend[loc.name];

  // Max PM2.5 = tertinggi antar titik; Min PM2.5 = terendah antar titik
  // (sebelum dirata-rata jadi nilai kabupaten).
  const pm25Max = loc.pm25Max != null ? loc.pm25Max : loc.pm25;
  const pm25Min = loc.pm25Min != null ? loc.pm25Min : loc.pm25;

  // Kartu metrik atas
  document.getElementById('metricLocation').textContent = loc.name;
  document.getElementById('aqiValue').textContent = loc.aqi;
  const aqiMeter = document.getElementById('aqiMeter');
  aqiMeter.style.width = clampMeter(loc.aqi / 3);
  aqiMeter.style.background = meterColor;
  document.getElementById('statusValue').textContent = loc.level;
  const statusValue = document.getElementById('statusValue');

  if (loc.aqi <= 50) {
      statusValue.style.color = "#00e400";
  } else if (loc.aqi <= 100) {
      statusValue.style.color = "#ffff00";
  } else if (loc.aqi <= 150) {
      statusValue.style.color = "#ff7e00";
  } else if (loc.aqi <= 200) {
      statusValue.style.color = "#ff0000";
  } else if (loc.aqi <= 300) {
      statusValue.style.color = "#8f3f97";
  } else {
      statusValue.style.color = "#7e0023";
  }
  // Alasan mudah dimengerti: AQI ditentukan oleh polutan dominan.
  const domName = POLLUTANT_NAMES[loc.dominant] || (loc.dominant ? loc.dominant.toUpperCase() : '');
  const reason = domName ? ` AQI ditentukan terutama oleh ${domName}.` : '';
  document.getElementById('statusDesc').textContent = statusDesc[cat] + reason;
  document.getElementById('pm25Value').textContent = Math.round(pm25Max * 10) / 10;
  const pm25Meter = document.getElementById('pm25Meter');
  pm25Meter.style.width = clampMeter(pm25Max / 2.5);
  pm25Meter.style.background = meterColor;
  document.getElementById('pm25MinValue').textContent = Math.round(pm25Min * 10) / 10;
  const pm25MinMeter = document.getElementById('pm25MinMeter');
  pm25MinMeter.style.width = clampMeter(pm25Min / 2.5);
  pm25MinMeter.style.background = meterColor;

  // Kartu di sekeliling peta
  document.getElementById('weatherTemp').textContent = `${loc.temp}°`;
  const dry = loc.precipitation < 0.1;
  document.getElementById('weatherText').textContent =
    `Suhu ${loc.temp}°C, kelembapan ${loc.humidity}%, dan angin ${loc.windSpeed} km/jam${dry ? ' tanpa hujan' : ' dengan hujan ringan'}. ` +
    (loc.aqi <= 50
      ? 'Kondisi ini cukup mendukung penyebaran polutan sehingga kualitas udara cenderung baik.'
      : 'Sirkulasi udara yang terbatas membuat PM2.5/PM10 lebih mudah bertahan di udara.');
  // Berita / Faktor / Rekomendasi: pakai hasil LLM bila ada, jika tidak pakai template.
  const llm = DATA.news && DATA.news.regencies && DATA.news.regencies[loc.name];
  const useLlm = llm && !llm.error && llm.ringkasan;
  document.getElementById('newsText').textContent = useLlm ? llm.ringkasan : news[cat];
  const causeItems = useLlm && llm.faktor && llm.faktor.length ? llm.faktor : causes[cat];
  const recoItems = useLlm && llm.rekomendasi && llm.rekomendasi.length ? llm.rekomendasi : recommendations[cat];
  document.getElementById('causesList').innerHTML = causeItems.map((item) => `<li>${item}</li>`).join('');
  document.getElementById('recoList').innerHTML = recoItems.map((item) => `<li>${item}</li>`).join('');
  // Sumber berita (link) bila pakai hasil LLM.
  const sumberEl = document.getElementById('newsSources');
  const sumberList = useLlm && llm.sumber ? llm.sumber : [];
  sumberEl.innerHTML = sumberList.length
    ? '<span>Sumber:</span> ' + sumberList.slice(0, 4).map((s) => {
        const outlet = s.sumber && s.sumber !== 'Google News RSS' ? s.sumber : (s.judul || 'berita').slice(0, 46);
        const safeTitle = (s.judul || '').replace(/"/g, '');
        return s.url
          ? `<a href="${s.url}" target="_blank" rel="noopener" title="${safeTitle}">${outlet}</a>`
          : `<span>${outlet}</span>`;
      }).join(' · ')
    : '';

  // Tren AQI 24 jam terakhir (data nyata)
  const trendChart = document.querySelector('.chart[data-chart="multi"]');
  if (trendChart && tr) {
    renderChart(trendChart, [
      { name: 'AQI', unit: '', color: '#17c9a3', values: tr.aqi }
    ], { labels: tr.labels });
  }
  document.getElementById('trendLocation').textContent = loc.name;

  // Prediksi AQI 24 jam ke depan
  const predChart = document.querySelector('.chart[data-chart="prediction"]');
  const pred = DATA.prediction[loc.name];
  if (predChart && pred) {
    renderChart(predChart, [
      { name: 'AQI', unit: '', color: '#17c9a3', values: pred.aqi }
    ], { labels: pred.labels });
    const peak = Math.max(...pred.aqi);
    const risk = peak <= 50 ? 'Rendah' : peak <= 100 ? 'Sedang' : peak <= 150 ? 'Cukup Tinggi' : 'Tinggi';
    const riskEl = document.querySelector('#page-dashboard .risk-score strong');
    if (riskEl) riskEl.textContent = risk;
  }

  // Info Polutan mengikuti lokasi
  renderPollutants();
  renderPollutantChart('pollutant');
  renderPollutantChart('forecast');
  document.getElementById('pollutantLocation').textContent = loc.name;

  // Sorot marker yang dipilih
  Object.entries(markers).forEach(([name, marker]) => {
    const el = marker.getElement();
    if (el) el.querySelector('.aq-marker').classList.toggle('selected', name === loc.name);
  });
}

// ============================================================
// INFO POLUTAN
// ============================================================
function renderPollutants() {
  if (!activeLocation) return;
  document.getElementById('pollutantRows').innerHTML = POLLUTANTS.map((p, i) => {
    const value = activeLocation.poll[p.key];
    return `<div class="pollutant-row"><span class="pollutant-name"><i style="background:${p.color}"></i>${p.label}</span><span class="pollutant-value">${value}<small>µg/m³</small></span></div>`;
  }).join('');
}

function renderPollutantChart(chartType) {
  if (!activeLocation || !DATA.pollutantSeries) return;
  const series = DATA.pollutantSeries[activeLocation.name];
  if (!series) return;
  const key = pollutantSel[chartType];
  const meta = POLLUTANTS.find((p) => p.key === key);
  const values = chartType === 'forecast' ? series.pollutants[key].forecast : series.pollutants[key].current;
  const labels = chartType === 'forecast' ? series.labelsForecast : series.labelsCurrent;
  const chart = document.querySelector(`.chart[data-chart="${chartType}"]`);
  if (chart) renderChart(chart, [{ name: meta.label, unit: ' µg/m³', color: meta.color, values }], { labels });
}

function setupPollutantFilters() {
  document.querySelectorAll('[data-chart-filter]').forEach((dropdown) => {
    const chartType = dropdown.dataset.chartFilter;
    dropdown.querySelector('.filter-menu').innerHTML = POLLUTANTS.map((p) =>
      `<button class="filter-option${p.key === pollutantSel[chartType] ? ' selected' : ''}" type="button" data-pollutant="${p.key}"><i style="background:${p.color}"></i><span>${p.label}</span></button>`
    ).join('');
    const selected = POLLUTANTS.find((p) => p.key === pollutantSel[chartType]);
    dropdown.querySelector('.filter-trigger span').textContent = selected.label;
  });
  document.querySelectorAll('[data-chart-filter] .filter-option').forEach((option) => option.addEventListener('click', () => {
    const dropdown = option.closest('.filter-dropdown');
    const chartType = dropdown.dataset.chartFilter;
    pollutantSel[chartType] = option.dataset.pollutant;
    dropdown.querySelectorAll('.filter-option').forEach((item) => item.classList.toggle('selected', item === option));
    dropdown.querySelector('.filter-trigger span').textContent = option.querySelector('span').textContent;
    dropdown.classList.remove('open');
    dropdown.querySelector('.filter-trigger').setAttribute('aria-expanded', 'false');
    renderPollutantChart(chartType);
  }));
}

// ============================================================
// MAP
// ============================================================
function buildLocations() {
  locations = DATA.current.locations.map((c) => {
    const cat = getAqiCategory(c.aqi);
    return {
      name: c.name, regency: c.regency, station: c.regency,
      lat: c.lat, lng: c.lng, aqi: c.aqi, level: cat.label, color: cat.color,
      pm25: c.pm25, pm10: c.pm10, pm25Max: c.pm25Max, pm25Min: c.pm25Min,
      temp: c.temp, humidity: c.humidity,
      precipitation: c.precipitation, windSpeed: c.windSpeed, pressure: c.pressure,
      dominant: c.dominant, update: c.update,
      poll: { co: c.co, no: c.no, no2: c.no2, o3: c.o3, so2: c.so2, pm2_5: c.pm25, pm10: c.pm10, nh3: c.nh3 }
    };
  });
}

function initMap() {
  if (!window.L) {
    document.getElementById('map').innerHTML = '<div style="display:grid;place-items:center;height:100%;color:#65727e">Peta memerlukan koneksi internet</div>';
    return;
  }
  map = L.map('map', { zoomControl: true, scrollWheelZoom: false }).setView([-8.4, 115.15], 9);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap',
    maxZoom: 19
  }).addTo(map);

  const coords = [];
  locations.forEach((loc) => {
    if (loc.lat == null || loc.lng == null) return;
    coords.push([loc.lat, loc.lng]);
    const icon = L.divIcon({
      className: '',
      html: `<div class="aq-marker" style="background:${loc.color}"><span>${loc.aqi}</span></div>`,
      iconSize: [28, 28],
      iconAnchor: [14, 28]
    });
    const marker = L.marker([loc.lat, loc.lng], { icon }).addTo(map)
      marker.bindTooltip(`
        <div class="sensor-popup">
          <div class="sensor-popup__head">
            <div>
              <b>${loc.name}</b>
              <small>${loc.station}</small>
            </div>
            <strong style="color:${loc.color}">${loc.aqi}</strong>
          </div>

          <span class="sensor-popup__status"
            style="color:${loc.color};background:${loc.color}18">
            ${loc.level}
          </span>

          <div class="sensor-popup__grid">
            <span>PM2.5 <b>${loc.pm25} µg/m³</b></span>
            <span>PM10 <b>${loc.pm10} µg/m³</b></span>
            <span>Suhu <b>${loc.temp}°C</b></span>
            <span>Kelembapan <b>${loc.humidity}%</b></span>
          </div>

          <p>Polutan dominan: ${loc.dominant.toUpperCase()}.</p>

          <small class="sensor-popup__time">
            Diperbarui ${loc.update}
          </small>
        </div>
      `, {
        sticky: true,
        direction: 'top',
        opacity: 1,
        className: 'aqi-tooltip'
      });
    marker.on('mouseover', () => {
      marker.openTooltip();
    });

    marker.on('mouseout', () => {
      marker.closeTooltip();
    });
    marker.on('click', () => selectLocation(loc));
    markers[loc.name] = marker;
  });

  if (coords.length) map.fitBounds(L.latLngBounds(coords).pad(0.15));
}

document.getElementById('fitMap').addEventListener('click', () => {
  if (map) {
    const coords = locations.filter((l) => l.lat != null).map((l) => [l.lat, l.lng]);
    if (coords.length) map.flyToBounds(L.latLngBounds(coords).pad(0.15), { duration: .8 });
    showToast('Tampilan peta disesuaikan');
  }
});

// ============================================================
// DATA HISTORI
// ============================================================
function sparkline(aqi, seed) {
  const color = getAqiCategory(aqi).color;
  const points = [8, 10 + seed % 3, 6, 9, 4 + seed % 4, 7].map((y, i) => `${i * 9},${y}`).join(' ');
  return `<svg class="sparkline" viewBox="0 0 48 15"><polyline points="${points}" fill="none" stroke="${color}" stroke-width="1.5"/></svg>`;
}

function renderHistory() {

  const query = document.getElementById('historySearch').value.toLowerCase();

  filteredHistory = historyData.filter(row =>
    (categoryFilter === 'all' || row.category === categoryFilter) &&
    row.location.toLowerCase().includes(query)
  );

  // Reset ke halaman pertama jika halaman sekarang melebihi total halaman
  const totalPages = Math.ceil(filteredHistory.length / rowsPerPage);

  if (currentPage > totalPages && totalPages > 0) {
    currentPage = totalPages;
  }

  const start = (currentPage - 1) * rowsPerPage;
  const end = start + rowsPerPage;

  const pageData = filteredHistory.slice(start, end);

  document.getElementById('historyBody').innerHTML = pageData.map((row, i) =>
    `<tr>
      <td><span class="location-cell"><i></i>${row.location}</span></td>
      <td>${row.time}</td>
      <td><b style="color:${getAqiCategory(row.aqi).color}">${row.aqi}</b></td>
      <td>${row.pm25} <small style="color:#a0a9b1">µg/m³</small></td>
      <td>
        <span class="category"
          style="color:${getAqiCategory(row.aqi).color};
          background:${getAqiCategory(row.aqi).color}18">
          ${row.category}
        </span>
      </td>
    </tr>`
  ).join('');

  document.getElementById('rowCount').textContent =
    `Menampilkan ${filteredHistory.length === 0 ? 0 : start + 1}-${Math.min(end, filteredHistory.length)} dari ${filteredHistory.length} data`;

  renderPagination();
}

function renderPagination() {
  const totalPages = Math.ceil(filteredHistory.length / rowsPerPage);
  const container = document.querySelector('.pagination div');

  let html = '';

  // Tombol sebelumnya
  html += `<button ${currentPage === 1 ? 'disabled' : ''} onclick="changePage(${currentPage - 1})">‹</button>`;

  if (totalPages <= 3) {
    // Jika halaman <=3 tampilkan semua
    for (let i = 1; i <= totalPages; i++) {
      html += `<button class="${i === currentPage ? 'active' : ''}"
                onclick="changePage(${i})">${i}</button>`;
    }
  } else {

    // Halaman pertama
    html += `<button class="${currentPage === 1 ? 'active' : ''}"
              onclick="changePage(1)">1</button>`;

    if (currentPage <= 2) {

      html += `
      <button class="${currentPage===2?'active':''}"
              onclick="changePage(2)">2</button>

      <button class="${currentPage===3?'active':''}"
              onclick="changePage(3)">3</button>

      <span>...</span>

      <button onclick="changePage(${totalPages})">${totalPages}</button>`;

    } else if (currentPage >= totalPages-1) {

      html += `<span>...</span>`;

      html += `
      <button class="${currentPage===totalPages-1?'active':''}"
              onclick="changePage(${totalPages-1})">${totalPages-1}</button>

      <button class="${currentPage===totalPages?'active':''}"
              onclick="changePage(${totalPages})">${totalPages}</button>`;

    } else {

      html += `<span>...</span>`;

      html += `
      <button class="active"
              onclick="changePage(${currentPage})">${currentPage}</button>

      <button onclick="changePage(${currentPage+1})">
        ${currentPage+1}
      </button>

      <span>...</span>

      <button onclick="changePage(${totalPages})">${totalPages}</button>`;
    }
  }

  // Tombol selanjutnya
  html += `<button ${currentPage === totalPages ? 'disabled' : ''}
            onclick="changePage(${currentPage + 1})">›</button>`;

  container.innerHTML = html;
}

function changePage(page){

    currentPage = page;

    renderHistory();

}

document.getElementById('historySearch').addEventListener('input', () => {

    currentPage = 1;

    renderHistory();

});
document.querySelectorAll('#historyCategoryFilter .filter-option').forEach((option) => option.addEventListener('click', () => {
  categoryFilter = option.dataset.category;
  const dropdown = option.closest('.filter-dropdown');
  dropdown.querySelectorAll('.filter-option').forEach((item) => item.classList.toggle('selected', item === option));
  dropdown.querySelector('.filter-trigger span').textContent = option.querySelector('span').textContent;
  dropdown.classList.remove('open');
  dropdown.querySelector('.filter-trigger').setAttribute('aria-expanded', 'false');
  currentPage = 1;
  renderHistory();
}));

document.getElementById('downloadCsv').addEventListener('click', () => {
  const header = 'Lokasi,Waktu,Status,AQI,PM2.5,Kategori\n';
  const csv = header + historyData.map((row) => `${row.location},${row.time},Aktif,${row.aqi},${row.pm25},${row.category}`).join('\n');
  const link = document.createElement('a');
  link.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
  link.download = 'ecomonitor-histori.csv';
  link.click();
  URL.revokeObjectURL(link.href);
  showToast('Data histori berhasil diunduh');
});

// ============================================================
// GLOBAL DROPDOWN CLOSE + TRIGGERS
// ============================================================
document.querySelectorAll('.filter-trigger').forEach((trigger) => trigger.addEventListener('click', (event) => {
  event.stopPropagation();
  const dropdown = trigger.closest('.filter-dropdown');
  const willOpen = !dropdown.classList.contains('open');
  document.querySelectorAll('.filter-dropdown.open').forEach((item) => {
    item.classList.remove('open');
    item.querySelector('.filter-trigger').setAttribute('aria-expanded', 'false');
  });
  dropdown.classList.toggle('open', willOpen);
  trigger.setAttribute('aria-expanded', String(willOpen));
}));
document.addEventListener('click', () => {
  document.querySelectorAll('.filter-dropdown.open').forEach((dropdown) => {
    dropdown.classList.remove('open');
    dropdown.querySelector('.filter-trigger').setAttribute('aria-expanded', 'false');
  });
});

// ============================================================
// PAGE ROUTING
// ============================================================
function applyHash() {
  const target = window.location.hash.slice(1);
  if (['dashboard', 'pollutants', 'history', 'accuracy'].includes(target)) openPage(target);
}
window.addEventListener('hashchange', applyHash);

// ============================================================
// BOOTSTRAP
// ============================================================
function formatDate(ts) {
  // ts: "YYYY-MM-DD HH:MM:SS"
  const [date, time] = ts.split(' ');
  const [y, m, d] = date.split('-').map(Number);
  const [hh, mm] = time.split(':');
  return `${String(d).padStart(2, '0')} ${MONTHS[m - 1]} ${y}, ${hh}.${mm}`;
}
function shortDate(ts) {
  const [y, m, d] = ts.split(' ')[0].split('-').map(Number);
  return `${String(d).padStart(2, '0')} ${MONTHS[m - 1].slice(0, 3)} ${y}`;
}

async function loadData() {
  const files = ['current', 'trend', 'prediction', 'pollutant_series', 'history'];
  const results = await Promise.all(files.map((f) => fetch(`data/${f}.json`).then((r) => {
    if (!r.ok) throw new Error(`${f}.json ${r.status}`);
    return r.json();
  })));
  DATA.current = results[0];
  DATA.trend = results[1];
  DATA.prediction = results[2];
  DATA.pollutantSeries = results[3];
  DATA.history = results[4];

  // news.json opsional (mungkin belum dibuat / LLM belum jalan).
  try {
    const r = await fetch('data/news.json', { cache: 'no-store' });
    DATA.news = r.ok ? await r.json() : null;
  } catch (_e) {
    DATA.news = null;
  }
}

async function init() {
  try {
    await loadData();
  } catch (err) {
    console.error('Gagal memuat data:', err);
    showToast('Gagal memuat data dashboard');
    return;
  }

  // Topbar date = latest record in the dataset.
  document.getElementById('currentDate').textContent = formatDate(DATA.current.meta.dataLatest);

  // Data Histori
  historyData = DATA.history.rows.map((r) => ({
    location: r.location, time: r.time, aqi: r.aqi, pm25: r.pm25,
    category: getAqiCategory(r.aqi).label
  }));
  if (historyData.length) {
    const times = historyData.map((r) => r.time).sort();
    const rangeEl = document.querySelector('#page-history .date-filter span:last-child');
    if (rangeEl) rangeEl.textContent = `${shortDate(times[0])} - ${shortDate(times[times.length - 1])}`;
  }
  renderHistory();
  setupPollutantFilters();

  buildLocations();
  initMap();
  selectLocation(locations.find((l) => l.name === 'Denpasar') || locations[0]);

  applyHash();
}

init();
