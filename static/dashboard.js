"use strict";
const $ = (id) => document.getElementById(id);
const zone = "America/Lima";
const dateFormat = new Intl.DateTimeFormat("es-PE", {
  timeZone: zone,
  day: "2-digit",
  month: "short",
  year: "numeric",
});
const timeFormat = new Intl.DateTimeFormat("es-PE", {
  timeZone: zone,
  hour: "2-digit",
  minute: "2-digit",
});
const speciesNames = {
  tortugas: "Tortuga marina",
  gaviotines: "Gaviotín peruano",
  invasores: "Amenaza / intrusión",
};
const stateNames = {
  nuevo: "Nuevo",
  en_revision: "En revisión",
  confirmado: "Confirmado",
  resuelto: "Resuelto",
  descartado: "Falso positivo",
};
const severityNames = {
  informativa: "Informativa",
  advertencia: "Advertencia",
  alta: "Alta",
  critica: "Crítica",
};
const speciesIcons = { tortugas: "🐢", gaviotines: "🐦", invasores: "⚠" };
let key = sessionStorage.getItem("nawi-control-key") || "";
let theme =
  new URLSearchParams(location.search).get("theme") ||
  localStorage.getItem("nawi-theme") ||
  "pacifico";
let demo = new URLSearchParams(location.search).get("demo") === "1";
let page = 1;
let pages = 1;
let totalRows = 0;
let order = "fecha";
let direction = "desc";
let range = null;
let pendingAction = null;
let focusedEvent = null;
let summaryData = null;
let tableEvents = [];
let timelineChart = null;
let map = null;
let markerLayer = null;
let draftMarker = null;
let placing = false;
let centeredPrototype = "";
let busy = false;
let refreshAgain = false;
let stateFilter = "todos";
let lastUpdated = null;
let currentMode = null;
let mapSignature = "";
let pendingMapData = null;
let toastTimer = null;
const visibleSeries = new Set(["tortugas", "gaviotines", "invasores"]);
const sparks = {};
const demoEvents = createDemoEvents();
const demoLocations = [
  {
    id: "prototipo",
    nombre: "Estación demostrativa · Chorrillos",
    tipo: "prototipo",
    latitud: -12.164,
    longitud: -77.031,
  },
  {
    id: "demo-nido1",
    nombre: "Nido demostrativo A",
    tipo: "nido",
    latitud: -12.149,
    longitud: -77.032,
  },
  {
    id: "demo-nido2",
    nombre: "Nido demostrativo B",
    tipo: "nido",
    latitud: -12.097,
    longitud: -77.058,
  },
];

function createDemoEvents() {
  const places = [
    { nombre: "Playa Agua Dulce · demo", latitud: -12.164, longitud: -77.031 },
    { nombre: "Costa Verde · demo", latitud: -12.097, longitud: -77.059 },
    { nombre: "La Punta · demo", latitud: -12.071, longitud: -77.162 },
    { nombre: "Playa Venecia · demo", latitud: -12.202, longitud: -77.018 },
  ];
  return Array.from({ length: 24 }, (_, i) => {
    const especie = ["tortugas", "gaviotines", "gaviotines", "invasores"][
      i % 4
    ];
    const place = places[i % places.length];
    return {
      id: "demostracion-" + i,
      fecha: new Date(Date.now() - (i * 0.72 + 0.12) * 3600000).toISOString(),
      especie,
      cantidad: especie === "invasores" ? 1 : 1 + (i % 5),
      confianza: 0.82 + (i % 7) * 0.023,
      dispositivo: "Prototipo demostrativo",
      ubicacion: place.nombre,
      latitud: place.latitud,
      longitud: place.longitud,
      tipo_evento:
        especie === "invasores"
          ? ["Intrusión: perro", "Residuos plásticos", "Intrusión: persona"][
              Math.floor(i / 4) % 3
            ]
          : "Avistamiento",
      severidad:
        especie === "invasores"
          ? i % 3 === 0
            ? "critica"
            : "advertencia"
          : "informativa",
      estado: ["nuevo", "confirmado", "nuevo", "confirmado", "descartado", "nuevo"][i % 6],
      tiene_imagen: false,
      imagen_url: null,
    };
  });
}

function applyTheme(value) {
  theme = ["pacifico", "abisal", "reserva"].includes(value)
    ? value
    : "pacifico";
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("nawi-theme", theme);
  for (const input of document.querySelectorAll('input[name="theme"]'))
    input.checked = input.value === theme;
  if (summaryData) renderCharts(summaryData);
}
applyTheme(theme);

function notify(message, error = false) {
  $("toast").textContent = message;
  $("toast").className = "toast" + (error ? " error" : "");
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => ($("toast").hidden = true), 6000);
}
function notice(message) {
  $("notice").textContent = message;
  $("notice").hidden = !message;
}
async function api(path, options = {}) {
  // Con la clave guardada, el servidor devuelve coordenadas exactas; sin ella, aproximadas.
  const headers = { ...(options.headers || {}) };
  if (key && !headers["X-ALERTA-KEY"]) headers["X-ALERTA-KEY"] = key;
  const response = await fetch(path, { cache: "no-store", ...options, headers });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(
      result.error || "El servidor respondió con HTTP " + response.status + ".",
    );
    error.status = response.status;
    throw error;
  }
  return result;
}
async function mutate(action) {
  if (demo) {
    notify(
      "Estás en una demostración. Vuelve a los datos reales para guardar cambios.",
    );
    return;
  }
  try {
    await action();
  } catch (error) {
    if (error.status === 401) {
      pendingAction = action;
      showSettings();
      notify("Introduce la clave de control para completar esta acción.");
    } else notify(error.message, true);
  }
}
function post(path, payload, method = "POST") {
  return api(path, {
    method,
    headers: { "Content-Type": "application/json", "X-ALERTA-KEY": key },
    body: JSON.stringify(payload),
  });
}
// Período: afecta a todo el panel (KPI, mapa, gráfico).
function periodParams() {
  const values = new URLSearchParams({
    horas:
      $("period-filter").value === "custom" ? "24" : $("period-filter").value,
  });
  if (range) {
    values.set("desde", range.start);
    values.set("hasta", range.end);
  }
  return values;
}
// Filtros de la tabla: solo afectan a la tabla y a la exportación.
function params() {
  const values = periodParams();
  values.set("especie", $("species-filter").value);
  values.set("estado", stateFilter);
  return values;
}
const STATE_GROUPS = {
  pendiente: ["nuevo", "en_revision"],
  validado: ["confirmado", "resuelto"],
};
function matchesState(event, state) {
  if (state === "todos") return event.estado !== "descartado";
  return (STATE_GROUPS[state] || [state]).includes(event.estado);
}
function demoFiltered(table = true) {
  const values = table ? params() : periodParams();
  const end = range ? Date.parse(range.end) : Date.now();
  const start = range
    ? Date.parse(range.start)
    : end - Number(values.get("horas")) * 3600000;
  return demoEvents
    .filter(
      (event) =>
        Date.parse(event.fecha) >= start && Date.parse(event.fecha) <= end,
    )
    .filter(
      (event) =>
        !table ||
        values.get("especie") === "todas" ||
        event.especie === values.get("especie"),
    )
    .filter((event) =>
      matchesState(event, table ? values.get("estado") : "todos"),
    );
}
function demoSummary(events) {
  const end = new Date();
  const hours = range
    ? Math.max(1, (Date.parse(range.end) - Date.parse(range.start)) / 3600000)
    : Number(periodParams().get("horas"));
  const start = new Date(end - hours * 3600000);
  const bucket = Math.max(1, Math.ceil(hours / 32));
  const serie = Array.from(
    { length: Math.ceil(hours / bucket) + 1 },
    (_, index) => ({
      hora: new Date(+start + index * bucket * 3600000).toISOString(),
      eventos: 0,
      tortugas: 0,
      gaviotines: 0,
      invasores: 0,
    }),
  );
  for (const event of events) {
    const index = Math.min(
      serie.length - 1,
      Math.max(
        0,
        Math.floor((Date.parse(event.fecha) - start) / 3600000 / bucket),
      ),
    );
    serie[index][event.especie] += 1;
    serie[index].eventos += 1;
  }
  const kpis = {};
  for (const name of ["total", "tortugas", "gaviotines", "invasores"]) {
    const filtered =
      name === "total"
        ? events
        : events.filter((event) => event.especie === name);
    const value = ["total", "invasores"].includes(name)
      ? filtered.length
      : filtered.reduce((sum, event) => sum + event.cantidad, 0);
    const old = Math.floor(value * 0.84);
    kpis[name] = {
      valor: value,
      anterior: old,
      variacion: old ? Math.round(((value - old) / old) * 1000) / 10 : null,
    };
  }
  return {
    kpis,
    serie,
    eventos: events,
    amenazas: events.filter((event) => event.especie === "invasores"),
    puntos: events,
    ubicaciones: demoLocations,
    actualizado: end.toISOString(),
    dispositivo: {
      conectado: true,
      estado: "monitoreando",
      nombre: "Demostración",
    },
    almacenamiento_persistente: true,
    coordenadas_exactas: true,
    pendientes: demoEvents.filter((event) =>
      STATE_GROUPS.pendiente.includes(event.estado),
    ).length,
    desde: start.toISOString(),
    hasta: end.toISOString(),
  };
}

async function refresh() {
  if (busy) {
    refreshAgain = true;
    return;
  }
  busy = true;
  try {
    let data, records, config;
    if (demo) {
      const events = demoFiltered(true);
      data = demoSummary(demoFiltered(false));
      const sorted = events.slice().sort((a, b) => {
        const first = a[order] ?? "",
          second = b[order] ?? "";
        const comparison =
          typeof first === "number"
            ? first - second
            : String(first).localeCompare(String(second), "es");
        return direction === "asc" ? comparison : -comparison;
      });
      const size = Number($("rows-per-page").value);
      const count = sorted.length;
      page = Math.min(page, Math.max(1, Math.ceil(count / size)));
      records = {
        eventos: sorted.slice((page - 1) * size, page * size),
        total: count,
        pagina: page,
        paginas: Math.max(1, Math.ceil(count / size)),
      };
      config = { mode: "tortugas" };
    } else {
      const query = params();
      const tableQuery = new URLSearchParams(query);
      tableQuery.set("pagina", page);
      tableQuery.set("limite", $("rows-per-page").value);
      tableQuery.set("orden", order);
      tableQuery.set("direccion", direction);
      [data, records, config] = await Promise.all([
        api("/api/resumen?" + periodParams()),
        api("/api/detecciones?" + tableQuery),
        api("/config"),
      ]);
    }
    summaryData = data;
    tableEvents = records.eventos;
    page = records.pagina;
    pages = records.paginas;
    totalRows = records.total;
    renderKpis(data.kpis);
    renderStatus(data, config);
    renderLatest(data.eventos);
    renderCharts(data);
    renderMap(data);
    renderThreats(data.amenazas);
    renderTable(records);
    $("demo-banner").hidden = !demo;
    $("demo-button").innerHTML = demo
      ? '<i class="bi bi-stop-circle" aria-hidden="true"></i>Salir de demostración'
      : '<i class="bi bi-play-circle" aria-hidden="true"></i>Ver demostración';
    $("server-dot").className = "status-dot online";
    $("server-status").textContent = demo
      ? "Vista demostrativa"
      : "Servidor conectado";
    $("storage-status").textContent = data.almacenamiento_persistente
      ? "ÑAWI APU · Conservación en tiempo real"
      : "Historial temporal · Conecta un volumen en Railway";
    notice(
      demo || data.almacenamiento_persistente
        ? ""
        : "Conecta un volumen al servicio Railway para conservar el historial entre despliegues.",
    );
    // Sin clave, las coordenadas se ven aproximadas: se indica en el mapa, sin alarmar.
    $("precision-label").hidden = demo || data.coordenadas_exactas !== false;
    $("precision-label").title =
      "Para proteger los nidos, sin la clave de control las ubicaciones se redondean (~1 km). Introdúcela en Configuración para ver las exactas.";
    if (!demo) spotlight(data.eventos);
  } catch (error) {
    $("server-dot").className = "status-dot error";
    $("server-status").textContent = "Sin conexión al servidor";
    $("monitor-status").textContent = "Conexión interrumpida";
    $("status-pill").className = "status-pill warning";
    notice(
      "No se pudieron actualizar los datos. " +
        error.message +
        " La vista se reintentará automáticamente.",
    );
  } finally {
    busy = false;
    if (refreshAgain) {
      refreshAgain = false;
      refresh();
    }
  }
}
// Texto del período elegido, para que las comparaciones se entiendan solas.
function periodLabel() {
  if (range) return { current: "el rango", before: "el rango anterior" };
  return (
    {
      1: { current: "la última hora", before: "la hora anterior" },
      24: { current: "las últimas 24 h", before: "las 24 h anteriores" },
      168: { current: "los últimos 7 días", before: "los 7 días anteriores" },
      720: { current: "los últimos 30 días", before: "los 30 días anteriores" },
    }[$("period-filter").value] || { current: "el período", before: "el período anterior" }
  );
}
function renderKpis(kpis) {
  for (const [name, item] of Object.entries(kpis)) {
    const value = $("kpi-" + name),
      change = $("change-" + name);
    if (!value) continue;
    value.textContent = Number(item.valor).toLocaleString("es-PE");
    value.classList.remove("loading-value");
    change.className = "";
    const previous = periodLabel();
    if (item.variacion === null)
      change.textContent =
        item.valor === 0
          ? "Sin actividad en " + previous.current
          : "Ninguno en " + previous.before;
    else {
      change.textContent =
        (item.variacion >= 0 ? "↑ +" : "↓ ") +
        item.variacion +
        "% vs. " +
        previous.before;
      change.className =
        name === "invasores"
          ? item.variacion > 0
            ? "negative"
            : "positive"
          : item.variacion >= 0
            ? "positive"
            : "negative";
    }
    if ($("species-" + name)) {
      $("species-" + name).textContent = Number(item.valor).toLocaleString(
        "es-PE",
      );
      $("species-change-" + name).textContent =
        item.variacion === null
          ? "Individuos avistados"
          : (item.variacion >= 0 ? "+" : "") +
            item.variacion +
            "% vs. período anterior";
    }
  }
  $("nav-threats").textContent = kpis.invasores.valor;
  $("nav-threats").hidden = kpis.invasores.valor === 0;
}
function renderStatus(data, config) {
  const device = data.dispositivo;
  const online = Boolean(device?.conectado);
  let label = "Agente desconectado";
  let style = "";
  if (demo) {
    label = "Demostración";
    style = "warning";
  } else if (online) {
    if (device.estado === "monitoreando") {
      label = "Monitoreo activo";
      style = "active";
    } else if (device.estado === "error") {
      label = "Revisar dispositivo";
      style = "warning";
    } else label = "Cámara conectada · en pausa";
  }
  $("monitor-status").textContent = label;
  $("status-pill").className = "status-pill " + style;
  $("status-pill").querySelector(".status-dot").className =
    "status-dot " + (online && !demo ? "online" : "");
  lastUpdated = new Date(data.actualizado);
  tickUpdated();
  $("peru-clock").textContent = timeFormat.format(new Date());
  // Solo informa qué vigila la cámara: se controla desde WhatsApp, no desde aquí.
  currentMode = config.mode;
  $("mode-indicator").textContent = {
    tortugas: "🐢 Vigilando tortugas",
    gaviotines: "🐦 Vigilando gaviotines",
    invasores: "⚠ Vigilando amenazas",
    detenido: "⏸ Vigilancia en pausa",
  }[config.mode] || "—";
  $("mode-indicator").className = "mode-indicator " + config.mode;
  $("chart-period").textContent = range
    ? "Rango personalizado"
    : { 1: "1 hora", 24: "24 horas", 168: "7 días", 720: "30 días" }[
        $("period-filter").value
      ] || "Período";
}
function colors() {
  const css = getComputedStyle(document.documentElement);
  return {
    tortugas: css.getPropertyValue("--accent").trim(),
    gaviotines: css.getPropertyValue("--blue").trim(),
    invasores: css.getPropertyValue("--coral").trim(),
    text: css.getPropertyValue("--muted").trim(),
  };
}
function renderCharts(data) {
  if (!window.Chart) {
    notice("La librería de gráficos no se pudo cargar. Actualiza la página.");
    return;
  }
  const palette = colors();
  const labels = data.serie.map((item) => item.hora);
  if (data.serie.length > 1) {
    // Cada punto agrupa un intervalo de tiempo: se indica para leer bien el gráfico.
    const minutes = Math.round(
      (new Date(data.serie[1].hora) - new Date(data.serie[0].hora)) / 60000,
    );
    $("chart-subtitle").textContent =
      "Eventos por intervalo de " +
      (minutes >= 60 && minutes % 60 === 0
        ? minutes / 60 + (minutes === 60 ? " hora" : " horas")
        : minutes + " min");
  }
  const datasets = ["tortugas", "gaviotines", "invasores"].map(
    (name, index) => ({
      label: {
        tortugas: "Tortugas",
        gaviotines: "Gaviotines",
        invasores: "Amenazas",
      }[name],
      data: data.serie.map((item) => item[name] || 0),
      borderColor: palette[name],
      backgroundColor: palette[name] + "18",
      fill: index === 0,
      borderWidth: 2,
      pointRadius: 0,
      pointHoverRadius: 4,
      // "monotone" no inventa valores entre puntos ni baja de cero.
      cubicInterpolationMode: "monotone",
      tension: 0.2,
      borderDash: [[], [6, 3], [2, 3]][index],
      hidden: !visibleSeries.has(name),
    }),
  );
  if (!timelineChart) {
    timelineChart = new Chart($("timeline-chart"), {
      type: "line",
      data: { labels, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: "#102e42",
            borderColor: "#3c7586",
            borderWidth: 1,
            padding: 10,
            displayColors: true,
            callbacks: {
              title: (items) =>
                dateFormat.format(new Date(items[0].label)) +
                " · " +
                timeFormat.format(new Date(items[0].label)),
              label: (item) =>
                item.dataset.label + ": " + item.raw + " eventos",
            },
          },
        },
        scales: {
          x: {
            grid: { display: false },
            border: { color: "#315366" },
            ticks: {
              color: palette.text,
              font: { family: "Inter", size: 10 },
              maxTicksLimit: 7,
              maxRotation: 0,
              callback: function (value) {
                const date = new Date(this.getLabelForValue(value));
                return Number($("period-filter").value) >= 168
                  ? date.toLocaleDateString("es-PE", {
                      timeZone: zone,
                      day: "2-digit",
                      month: "short",
                    })
                  : timeFormat.format(date);
              },
            },
          },
          y: {
            beginAtZero: true,
            border: { display: false },
            grid: { color: "#5f8fa31d", tickLength: 0 },
            ticks: {
              color: palette.text,
              font: { family: "Inter", size: 10 },
              precision: 0,
              maxTicksLimit: 4,
              padding: 7,
            },
          },
        },
      },
    });
  } else {
    timelineChart.data = { labels, datasets };
    timelineChart.options.scales.x.ticks.color = palette.text;
    timelineChart.options.scales.y.ticks.color = palette.text;
    timelineChart.update("none");
  }
  $("chart-empty").hidden = data.kpis.total.valor > 0 && visibleSeries.size > 0;
  $("chart-empty").textContent = visibleSeries.size
    ? "Las detecciones aparecerán aquí al iniciar el monitoreo."
    : "Selecciona una serie para visualizarla.";
  $("timeline-chart").setAttribute(
    "aria-label",
    data.kpis.total.valor +
      " eventos en el período. Tortugas, gaviotines y amenazas se distinguen por líneas continuas, discontinuas y punteadas.",
  );
  for (const name of ["total", "tortugas", "gaviotines", "invasores"]) {
    const color = name === "total" ? palette.tortugas : palette[name];
    const values = data.serie.map(
      (item) => item[name === "total" ? "eventos" : name] || 0,
    );
    if (sparks[name]) {
      sparks[name].data.datasets[0].data = values;
      sparks[name].data.datasets[0].borderColor = color;
      sparks[name].update("none");
    } else
      sparks[name] = new Chart($("spark-" + name), {
        type: "line",
        data: {
          labels: values.map((_, i) => i),
          datasets: [
            {
              data: values,
              borderColor: color,
              borderWidth: 1.7,
              pointRadius: 0,
              cubicInterpolationMode: "monotone",
              fill: false,
            },
          ],
        },
        options: {
          animation: false,
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false }, tooltip: { enabled: false } },
          scales: {
            x: { display: false },
            y: { display: false, beginAtZero: true },
          },
          events: [],
        },
      });
  }
}

function initMap() {
  if (!window.L) {
    $("map").textContent =
      "Mapa no disponible. Puedes introducir las coordenadas al fijar la ubicación.";
    return;
  }
  map = L.map("map", {
    zoomControl: true,
    scrollWheelZoom: false,
    attributionControl: true,
  }).setView([-12.1, -77.1], 11);
  // Capas base gratuitas y sin API key (CARTO ahora exige clave y mostraba "API KEY REQUIRED").
  const satellite = L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    {
      maxZoom: 19,
      attribution:
        "Imágenes &copy; Esri, Maxar, Earthstar Geographics",
    },
  );
  const streets = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    className: "map-tiles-dark",
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  });
  satellite.addTo(map);
  L.control
    .layers({ Satélite: satellite, Mapa: streets }, null, {
      position: "topright",
      collapsed: false,
    })
    .addTo(map);
  let tileErrors = 0;
  satellite.on("tileerror", () => {
    // Si el satélite no carga (red del evento, bloqueo), se cambia solo al mapa.
    if (++tileErrors === 4 && map.hasLayer(satellite)) {
      map.removeLayer(satellite);
      streets.addTo(map);
    }
  });
  streets.on("tileerror", () => {
    $("location-label").title =
      "El mapa base requiere conexión a internet. Las ubicaciones guardadas siguen disponibles.";
  });
  markerLayer = L.layerGroup().addTo(map);
  map.on("popupclose", () => {
    if (pendingMapData) setTimeout(() => pendingMapData && renderMap(pendingMapData), 0);
  });
  L.control.scale({ imperial: false, position: "bottomright" }).addTo(map);
  map.on("click", (event) => {
    if (placing) showLocation(event.latlng.lat, event.latlng.lng);
  });
}
function markerIcon(kind, label = "") {
  const content =
    kind === "prototipo"
      ? '<i class="bi bi-broadcast"></i>'
      : kind === "nido"
        ? "N"
        : label;
  return L.divIcon({
    className: "marker-host",
    html: '<div class="map-marker ' + kind + '">' + content + "</div>",
    iconSize: [22, 22],
    iconAnchor: [11, 11],
  });
}
function popup(title, detail) {
  const element = document.createElement("div");
  const strong = document.createElement("strong");
  strong.textContent = title;
  const small = document.createElement("div");
  small.textContent = detail;
  element.append(strong, small);
  return element;
}
function eventImage(event) {
  return event.tiene_imagen
    ? "/api/detecciones/" + encodeURIComponent(event.id) + "/imagen"
    : event.imagen_url || null;
}
function countLabel(event) {
  const n = event.cantidad;
  if (event.especie === "invasores")
    return n + (n === 1 ? " detectado" : " detectados");
  return n + (n === 1 ? " individuo" : " individuos");
}
function eventSummary(event) {
  const what =
    event.especie === "invasores" && event.tipo_evento
      ? String(event.tipo_evento).replace(/^Intrusión:\s*/i, "Intrusión: ")
      : speciesNames[event.especie] || event.especie;
  return {
    title: (speciesIcons[event.especie] || "") + " " + what,
    detail:
      countLabel(event) +
      (typeof event.confianza === "number"
        ? " · " + Math.round(event.confianza * 100) + "% confianza"
        : "") +
      " · " +
      dateFormat.format(new Date(event.fecha)) +
      " " +
      timeFormat.format(new Date(event.fecha)),
  };
}
// Ficha de una detección para el mapa: foto + qué + cuántos + cuándo.
function detectionCard(event, compact = false) {
  const card = document.createElement("div");
  card.className = "map-card" + (compact ? " compact" : "");
  const image = eventImage(event);
  if (image) {
    const img = document.createElement("img");
    img.src = image;
    img.alt = "Foto de la detección";
    img.loading = "lazy";
    card.append(img);
  }
  const info = eventSummary(event);
  const title = document.createElement("strong");
  title.textContent = info.title;
  const detail = document.createElement("span");
  detail.textContent = info.detail;
  card.append(title, detail);
  if (event.ia_descripcion && !compact) {
    const ia = document.createElement("em");
    ia.className = "ia-note";
    ia.textContent = "🤖 " + event.ia_descripcion;
    card.append(ia);
  }
  return card;
}
// Estación (rombo) con el número de detecciones; borde coral si hay amenazas sin atender.
function stationIcon(count, threat) {
  return L.divIcon({
    className: "marker-host",
    html:
      '<div class="station-marker' +
      (threat ? " threat" : "") +
      '"><div class="map-marker prototipo"><i class="bi bi-broadcast"></i></div>' +
      '<span class="station-count">' +
      count +
      "</span></div>",
    iconSize: [26, 26],
    iconAnchor: [13, 13],
  });
}
function renderMap(data) {
  const prototype = data.ubicaciones.find((item) => item.tipo === "prototipo");
  $("location-label").replaceChildren();
  const icon = document.createElement("i");
  icon.className = "bi bi-geo-alt";
  $("location-label").append(
    icon,
    document.createTextNode(
      prototype
        ? prototype.nombre +
            " · " +
            prototype.latitud.toFixed(5) +
            ", " +
            prototype.longitud.toFixed(5)
        : "Estación sin ubicar",
    ),
  );
  $("location-label").title = prototype
    ? "Coordenadas de la estación"
    : "Pulsa “Ubicar estación” y usa el GPS del celular junto a la cámara.";
  if (!map) return;
  // El panel se refresca cada 5 s: redibujar los marcadores cerraba la ventana
  // que el guardián tenía abierta. Solo se redibuja si cambiaron los datos,
  // y se espera a que cierre la ventana abierta.
  const signature = JSON.stringify([
    data.ubicaciones.map((p) => [p.id, p.latitud, p.longitud, p.nombre]),
    data.puntos.map((p) => [p.id, p.estado, p.latitud, p.longitud]),
  ]);
  if (signature === mapSignature) return;
  if (map._popup && map.hasLayer(map._popup)) {
    pendingMapData = data;
    return;
  }
  mapSignature = signature;
  pendingMapData = null;
  markerLayer.clearLayers();
  const stationKey = prototype
    ? prototype.latitud.toFixed(5) + "," + prototype.longitud.toFixed(5)
    : null;
  const pointKeys = new Set(
    data.puntos
      .filter((point) => point.latitud != null)
      .map((point) => point.latitud.toFixed(5) + "," + point.longitud.toFixed(5)),
  );
  for (const place of data.ubicaciones) {
    // Si hay detecciones en la estación, se muestran en su mismo marcador (abajo).
    if (place.tipo === "prototipo" && pointKeys.has(stationKey)) continue;
    const info = document.createElement("div");
    info.className = "map-card compact";
    const title = document.createElement("strong");
    title.textContent = place.nombre;
    const detail = document.createElement("span");
    detail.textContent =
      (place.tipo === "prototipo" ? "Estación de la cámara" : "Nido observado") +
      " · " +
      place.latitud.toFixed(5) +
      ", " +
      place.longitud.toFixed(5);
    info.append(title, detail);
    L.marker([place.latitud, place.longitud], {
      icon: markerIcon(place.tipo),
      title: place.nombre,
      zIndexOffset: place.tipo === "prototipo" ? -100 : 0,
    })
      .addTo(markerLayer)
      .bindPopup(info);
  }
  // Agrupa las detecciones que comparten coordenadas (con la estación fija, casi todas).
  const groups = new Map();
  for (const point of data.puntos) {
    if (point.latitud == null || point.longitud == null) continue;
    const id = point.latitud.toFixed(5) + "," + point.longitud.toFixed(5);
    if (!groups.has(id)) groups.set(id, []);
    groups.get(id).push(point);
  }
  for (const [key, events] of groups) {
    events.sort((a, b) => Date.parse(b.fecha) - Date.parse(a.fecha));
    const latest = events[0];
    const atStation = key === stationKey;
    const threat = events.some(
      (event) =>
        event.especie === "invasores" && STATE_GROUPS.pendiente.includes(event.estado),
    );
    const marker = L.marker([latest.latitud, latest.longitud], {
      icon: atStation
        ? stationIcon(events.length, threat)
        : markerIcon(
            threat ? "invasores" : latest.especie,
            events.length > 1
              ? String(events.length)
              : { invasores: "!", tortugas: "T", gaviotines: "G" }[latest.especie],
          ),
      title: (atStation ? prototype.nombre + " · " : "") + events.length + " detecciones",
      zIndexOffset: 500,
    }).addTo(markerLayer);
    // Al pasar el cursor: la foto y el resumen de la más reciente.
    const hover = detectionCard(latest);
    if (events.length > 1) {
      const more = document.createElement("small");
      more.textContent =
        "+" + (events.length - 1) + " más en este punto · haz clic para verlas";
      hover.append(more);
    }
    marker.bindTooltip(hover, {
      // Al costado: hacia arriba la ficha con foto no cabe y se cortaba.
      direction: "right",
      offset: [18, 0],
      className: "map-tooltip",
      opacity: 1,
    });
    // Al hacer clic: lista de las últimas detecciones del punto.
    const list = document.createElement("div");
    list.className = "map-popup-list";
    const heading = document.createElement("strong");
    heading.className = "map-popup-heading";
    heading.textContent =
      (atStation ? prototype.nombre + " · " : "") +
      events.length +
      (events.length === 1 ? " detección" : " detecciones");
    list.append(heading);
    for (const event of events.slice(0, 6)) {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "map-popup-row";
      row.append(detectionCard(event, true));
      row.addEventListener("click", () => openEvent(event));
      list.append(row);
    }
    marker.bindPopup(list, { maxWidth: 300, minWidth: 250, maxHeight: 290 });
    marker.on("popupopen", () => marker.closeTooltip());
  }
  const identity = prototype
    ? prototype.latitud + "," + prototype.longitud
    : "";
  if (identity && identity !== centeredPrototype) {
    map.setView([prototype.latitud, prototype.longitud], 16, {
      animate: false,
    });
    centeredPrototype = identity;
  }
}
function showLocation(lat, lon) {
  const prototype = summaryData?.ubicaciones.find(
    (item) => item.tipo === "prototipo",
  );
  $("latitude").value = lat ?? prototype?.latitud ?? "";
  $("longitude").value = lon ?? prototype?.longitud ?? "";
  $("location-name").value = prototype?.nombre || "";
  $("location-type").value = "prototipo";
  $("gps-help").textContent =
    "Abre esta página en el celular junto a la cámara y pulsa el botón: se usarán sus coordenadas GPS.";
  if (map && lat != null) {
    placing = false;
    $("map-help").hidden = true;
    $("map").style.cursor = "";
    if (draftMarker) map.removeLayer(draftMarker);
    draftMarker = L.marker([lat, lon], {
      icon: markerIcon("prototipo"),
      opacity: 0.7,
    }).addTo(map);
  }
  $("location-dialog").showModal();
}
function timeAgo(date) {
  const minutes = Math.round((Date.now() - Date.parse(date)) / 60000);
  if (minutes < 1) return "hace instantes";
  if (minutes < 60) return "hace " + minutes + " min";
  if (minutes < 24 * 60) return "hace " + Math.round(minutes / 60) + " h";
  return dateFormat.format(new Date(date));
}
// Muestra las amenazas más recientes; el resto se ve en la tabla (que tiene páginas).
const THREATS_SHOWN = 3;
function renderThreats(threats) {
  const root = $("threat-list");
  root.replaceChildren();
  if (!threats.length) {
    const p = document.createElement("p");
    p.className = "panel-empty";
    const icon = document.createElement("i");
    icon.className = "bi bi-shield-check";
    p.append(icon, document.createTextNode("Sin amenazas en " + periodLabel().current + "."));
    root.append(p);
    return;
  }
  for (const threat of threats.slice(0, THREATS_SHOWN)) {
    const row = document.createElement("div");
    row.className = "threat-row";
    const button = document.createElement("button");
    button.type = "button";
    const icon = document.createElement("i");
    icon.className = "bi bi-exclamation-triangle";
    const title = document.createElement("span");
    title.textContent = String(threat.tipo_evento || "Amenaza").replace(/^Intrusión:\s*/i, "Intrusión: ");
    title.title = threat.ia_descripcion || threat.ubicacion || "";
    const when = document.createElement("small");
    when.className = "threat-when";
    when.textContent = timeAgo(threat.fecha);
    button.append(icon, title, when);
    button.addEventListener("click", () => openEvent(threat));
    const severity = document.createElement("span");
    severity.className = "severity " + threat.severidad;
    severity.textContent = severityNames[threat.severidad] || "Advertencia";
    row.append(button, severity);
    root.append(row);
  }
  if (threats.length > THREATS_SHOWN) {
    const more = document.createElement("button");
    more.type = "button";
    more.className = "text-button threat-more";
    more.textContent =
      "Ver las " + (threats.length >= 50 ? "50+" : threats.length) + " amenazas en el registro →";
    more.addEventListener("click", () => {
      $("species-filter").value = "invasores";
      setStateFilter("todos");
      $("registros").scrollIntoView({ behavior: "smooth", block: "start" });
    });
    root.append(more);
  }
}
function cell(text, className = "") {
  const td = document.createElement("td");
  td.textContent = text ?? "—";
  td.className = className;
  return td;
}
const isOpenThreat = (event) =>
  event.especie === "invasores" && STATE_GROUPS.pendiente.includes(event.estado);
function stateBadge(estado, event = null) {
  if (estado === "descartado" && event?.descartado_por === "guardian") {
    // Revisado en terreno: es el veredicto más confiable.
    const badge = document.createElement("span");
    badge.className = "badge descartado ia";
    badge.title = "El guardián revisó la zona y respondió NO a la alerta.";
    badge.innerHTML = '<i class="bi bi-person-check" aria-hidden="true"></i>';
    badge.append(document.createTextNode("Descartada por el guardián"));
    return badge;
  }
  if (estado === "descartado" && event?.ia_resultado === "no_coincide") {
    const badge = document.createElement("span");
    badge.className = "badge descartado ia";
    badge.title = event.ia_descripcion || "";
    badge.innerHTML = '<i class="bi bi-robot" aria-hidden="true"></i>';
    badge.append(document.createTextNode("Descartado por Gemini"));
    return badge;
  }
  const badge = document.createElement("span");
  badge.className = "badge " + estado;
  const symbol = document.createElement("i");
  symbol.className =
    "bi " +
    ({
      nuevo: "bi-check-circle",
      en_revision: "bi-check-circle",
      confirmado: "bi-check-circle-fill",
      resuelto: "bi-check2-all",
      descartado: "bi-x-circle",
    }[estado] || "bi-circle");
  badge.append(
    symbol,
    document.createTextNode(
      {
        nuevo: "Registrada",
        en_revision: "Registrada",
        confirmado: "Confirmada",
        resuelto: "Atendida",
        descartado: "Falso positivo",
      }[estado] || estado,
    ),
  );
  return badge;
}
function renderTable(records) {
  const body = $("events-body");
  body.replaceChildren();
  for (const event of records.eventos) {
    const row = document.createElement("tr");
    if (isOpenThreat(event)) row.className = "pending-row";
    // Foto
    const photoCell = cell("", "photo-cell");
    const image = eventImage(event);
    const photo = document.createElement("button");
    photo.type = "button";
    photo.className = "thumb";
    photo.setAttribute("aria-label", "Ver detalle y foto");
    if (image) {
      const img = document.createElement("img");
      img.src = image;
      img.alt = "";
      img.loading = "lazy";
      photo.append(img);
    } else {
      photo.classList.add("empty");
      photo.innerHTML = '<i class="bi bi-image" aria-hidden="true"></i>';
    }
    photo.addEventListener("click", () => openEvent(event));
    photoCell.append(photo);
    row.append(photoCell);
    // Qué y cuántos
    const species = cell("");
    const wrap = document.createElement("div");
    wrap.className = "species-label";
    const emoji = document.createElement("span");
    emoji.className = "species-emoji";
    emoji.textContent = speciesIcons[event.especie];
    const label = document.createElement("span");
    label.textContent = eventSummary(event).title.replace(/^\S+\s/, "");
    const count = document.createElement("small");
    count.textContent = countLabel(event);
    label.append(count);
    if (event.ia_resultado === "coincide") {
      const ia = document.createElement("small");
      ia.className = "ia-ok";
      ia.title = event.ia_descripcion || "";
      ia.textContent = "✓ Verificado por Gemini";
      label.append(ia);
    }
    wrap.append(emoji, label);
    species.append(wrap);
    row.append(species);
    // Cuándo
    const date = cell(dateFormat.format(new Date(event.fecha)), "date-cell");
    const time = document.createElement("small");
    time.textContent = timeFormat.format(new Date(event.fecha));
    date.append(time);
    row.append(date);
    // Confianza
    const confidence = cell("");
    if (event.confianza == null) confidence.textContent = "Manual";
    else {
      const container = document.createElement("span");
      container.className = "confidence";
      const track = document.createElement("span");
      track.className = "confidence-track";
      const fill = document.createElement("span");
      fill.style.width = Math.round(event.confianza * 100) + "%";
      track.append(fill);
      container.append(
        document.createTextNode(Math.round(event.confianza * 100) + "%"),
        track,
      );
      confidence.append(container);
    }
    row.append(confidence);
    // Revisión
    const status = cell("");
    status.append(stateBadge(event.estado, event));
    row.append(status);
    // Acciones rápidas: validar sin abrir nada.
    const actionCell = cell("", "actions-cell");
    const more = document.createElement("button");
    more.type = "button";
    more.className = "icon-button";
    more.title = "Ver foto y detalle";
    more.setAttribute(
      "aria-label",
      "Ver detalle del evento del " + dateFormat.format(new Date(event.fecha)),
    );
    more.innerHTML = '<i class="bi bi-eye" aria-hidden="true"></i>';
    more.addEventListener("click", () => openEvent(event));
    actionCell.append(more);
    row.append(actionCell);
    body.append(row);
  }
  $("records-empty").hidden = records.total > 0;
  $("record-count").textContent =
    records.total +
    (records.total === 1 ? " evento" : " eventos") +
    (stateFilter === "descartado"
      ? " marcados como falso positivo"
      : " en el período");
  const size = Number($("rows-per-page").value);
  $("pagination-label").textContent = records.total
    ? (page - 1) * size +
      1 +
      "–" +
      Math.min(page * size, records.total) +
      " de " +
      records.total +
      " registros"
    : "0 registros";
  $("page-number").textContent = page + " / " + pages;
  $("previous-page").disabled = page <= 1;
  $("next-page").disabled = page >= pages;
  for (const button of document.querySelectorAll("[data-sort]")) {
    button
      .closest("th")
      .setAttribute(
        "aria-sort",
        button.dataset.sort === order
          ? direction === "asc"
            ? "ascending"
            : "descending"
          : "none",
      );
  }
}
function openEvent(event) {
  focusedEvent = event;
  $("event-title").textContent = eventSummary(event).title;
  const root = $("event-detail");
  root.replaceChildren();
  const details = {
    Cantidad: countLabel(event),
    Confianza:
      typeof event.confianza === "number"
        ? Math.round(event.confianza * 100) + "% (modelo de IA)"
        : "Registro manual",
    Fecha:
      dateFormat.format(new Date(event.fecha)) +
      " " +
      timeFormat.format(new Date(event.fecha)),
    Lugar: event.ubicacion || "Sin ubicación",
    Estado:
      {
        resuelto: "Amenaza atendida",
        descartado: "Falso positivo (no cuenta)",
      }[event.estado] || "Registrada",
    Dispositivo: event.dispositivo,
  };
  if (event.ia_resultado)
    details["Verificación Gemini"] =
      {
        coincide: "✓ Confirmado",
        no_coincide: "✗ No coincide (falso positivo)",
        error: "No disponible",
      }[event.ia_resultado] + (event.ia_descripcion ? " · " + event.ia_descripcion : "");
  for (const [name, value] of Object.entries(details)) {
    const item = document.createElement("div");
    const label = document.createElement("span");
    label.textContent = name;
    item.append(label, document.createTextNode(value ?? "—"));
    root.append(item);
  }
  const image = eventImage(event);
  $("event-image").hidden = !image;
  if (image) $("event-image").src = image;
  else $("event-image").removeAttribute("src");
  $("event-question").hidden = event.estado !== "descartado";
  $("event-question").textContent =
    event.descartado_por === "guardian"
      ? "El guardián revisó la zona y la marcó como falsa alarma: no cuenta en los registros."
      : event.ia_resultado === "no_coincide"
      ? "La IA verificó la foto y no encontró lo detectado: no cuenta en los registros."
      : "Marcada como falsa alarma: no cuenta en los registros.";
  $("event-dialog").showModal();
}
// ---- Última detección: siempre visible, con foto y validación ----
function renderLatest(events) {
  const body = $("latest-body");
  const latest = events && events[0];
  if (!latest) {
    $("latest-time").textContent = "Sin detecciones en el período";
    $("latest-state").hidden = true;
    return;
  }
  // Si la última es una tortuga/gaviotín pero hubo un intruso reciente (10 min),
  // se muestra también: una amenaza no debe quedar tapada por una detección de fauna.
  const recentThreat =
    latest.especie === "invasores"
      ? null
      : events.find(
          (event) =>
            event.especie === "invasores" &&
            event.estado !== "descartado" &&
            Date.now() - Date.parse(event.fecha) < 10 * 60000,
        );
  const signature =
    latest.id + ":" + latest.estado + ":" + (recentThreat ? recentThreat.id : "");
  if (body.dataset.signature === signature) return;
  body.dataset.signature = signature;
  body.replaceChildren();
  $("latest-time").textContent =
    dateFormat.format(new Date(latest.fecha)) +
    " · " +
    timeFormat.format(new Date(latest.fecha));
  $("latest-state").hidden = false;
  $("latest-state").replaceChildren(stateBadge(latest.estado, latest));
  const image = eventImage(latest);
  const figure = document.createElement("button");
  figure.type = "button";
  figure.className = "latest-photo" + (image ? "" : " empty");
  figure.setAttribute("aria-label", "Ver detalle de la última detección");
  if (image) {
    const img = document.createElement("img");
    img.src = image;
    img.alt = "Foto de la última detección";
    figure.append(img);
  } else figure.innerHTML = '<i class="bi bi-image" aria-hidden="true"></i>';
  figure.addEventListener("click", () => openEvent(latest));
  const info = document.createElement("div");
  info.className = "latest-info";
  const summary = eventSummary(latest);
  const title = document.createElement("strong");
  title.textContent = summary.title;
  const count = document.createElement("span");
  count.className = "latest-count";
  count.textContent = countLabel(latest);
  const meta = document.createElement("span");
  meta.className = "latest-meta";
  meta.textContent =
    (typeof latest.confianza === "number"
      ? Math.round(latest.confianza * 100) + "% de confianza"
      : "Registro manual") +
    (latest.ubicacion ? " · " + latest.ubicacion : "");
  info.append(title, count, meta);
  if (latest.ia_descripcion) {
    const ia = document.createElement("em");
    ia.className = "ia-note";
    ia.textContent = "🤖 Gemini: " + latest.ia_descripcion;
    info.append(ia);
  }
  body.append(figure, info);
  if (recentThreat) {
    const strip = document.createElement("button");
    strip.type = "button";
    strip.className = "threat-strip";
    const minutes = Math.max(
      0,
      Math.round((Date.now() - Date.parse(recentThreat.fecha)) / 60000),
    );
    strip.innerHTML = '<i class="bi bi-exclamation-triangle-fill" aria-hidden="true"></i>';
    strip.append(
      document.createTextNode(
        "Intruso detectado " +
          (minutes < 1 ? "hace instantes" : "hace " + minutes + " min") +
          " · " +
          String(recentThreat.ia_clase || recentThreat.tipo_evento || "amenaza").replace(
            /^Intrusión:\s*/i,
            "",
          ),
      ),
    );
    strip.addEventListener("click", () => openEvent(recentThreat));
    body.append(strip);
  }
}
function tickUpdated() {
  if (!lastUpdated) return;
  const seconds = Math.max(0, Math.round((Date.now() - lastUpdated) / 1000));
  $("last-update").textContent =
    seconds < 5
      ? "Actualizado ahora"
      : seconds < 60
        ? "Actualizado hace " + seconds + " s"
        : "Actualizado " + timeFormat.format(lastUpdated);
}
// ---- Detección en vivo: tarjeta emergente cuando llega un evento nuevo ----
let lastSeenEvent;
let spotlightTimer;
function spotlight(events) {
  const newest = events && events[0];
  if (lastSeenEvent === undefined) {
    // Primera carga: no se anuncia lo que ya estaba registrado.
    lastSeenEvent = newest ? newest.id : null;
    return;
  }
  if (!newest || newest.id === lastSeenEvent) return;
  lastSeenEvent = newest.id;
  let card = $("spotlight");
  if (!card) {
    card = document.createElement("aside");
    card.id = "spotlight";
    card.className = "spotlight";
    card.setAttribute("role", "status");
    card.setAttribute("aria-live", "polite");
    document.body.append(card);
  }
  card.replaceChildren();
  card.classList.toggle("threat", newest.especie === "invasores");
  const header = document.createElement("div");
  header.className = "spotlight-header";
  const live = document.createElement("span");
  live.className = "spotlight-live";
  live.textContent = newest.especie === "invasores" ? "Alerta en vivo" : "Detección en vivo";
  const close = document.createElement("button");
  close.type = "button";
  close.className = "spotlight-close";
  close.setAttribute("aria-label", "Cerrar");
  close.textContent = "×";
  close.addEventListener("click", (event) => {
    event.stopPropagation();
    card.classList.remove("visible");
  });
  header.append(live, close);
  card.append(header);
  if (newest.tiene_imagen) {
    const image = document.createElement("img");
    image.alt = "Captura de la detección";
    image.src = "/api/detecciones/" + encodeURIComponent(newest.id) + "/imagen";
    card.append(image);
  }
  const title = document.createElement("strong");
  title.textContent =
    (speciesIcons[newest.especie] || "") + " " + (speciesNames[newest.especie] || newest.especie);
  const detail = document.createElement("p");
  const parts = [
    newest.cantidad + (newest.cantidad === 1 ? " individuo" : " individuos"),
  ];
  if (typeof newest.confianza === "number")
    parts.push(Math.round(newest.confianza * 100) + "% de confianza");
  parts.push(timeFormat.format(new Date(newest.fecha)));
  detail.textContent = parts.join(" · ");
  card.append(title, detail);
  if (newest.ia_descripcion) {
    const ia = document.createElement("p");
    ia.className = "ia-note";
    ia.textContent = "🤖 " + newest.ia_descripcion;
    card.append(ia);
  }
  if (newest.especie === "invasores" && newest.tipo_evento) {
    const type = document.createElement("p");
    type.className = "spotlight-type";
    type.textContent = newest.tipo_evento;
    card.append(type);
  }
  card.onclick = () => openEvent(newest);
  requestAnimationFrame(() => card.classList.add("visible"));
  clearTimeout(spotlightTimer);
  spotlightTimer = setTimeout(() => card.classList.remove("visible"), 15000);
}
function showSettings() {
  $("control-key").value = "";
  $("control-key").placeholder = key
    ? "Clave guardada en esta pestaña"
    : "El mismo valor de ALERTA_KEY del servidor";
  $("settings-dialog").showModal();
}
function exportData() {
  if (!demo) {
    // El CSV incluye coordenadas exactas, por eso el servidor exige la clave.
    mutate(async () => {
      const response = await fetch("/api/exportar?" + params(), {
        cache: "no-store",
        headers: { "X-ALERTA-KEY": key },
      });
      if (!response.ok) {
        const result = await response.json().catch(() => ({}));
        const error = new Error(
          result.error || "El servidor respondió con HTTP " + response.status + ".",
        );
        error.status = response.status;
        throw error;
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      // Nombre con el rango de fechas que envía el servidor.
      const disposition = response.headers.get("Content-Disposition") || "";
      link.download =
        (disposition.match(/filename="?([^";]+)"?/) || [])[1] || "nawi-apu-reporte.csv";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
    return;
  }
  const rows = [
    ["DEMOSTRACIÓN", "Fecha", "Especie", "Cantidad", "Ubicación", "Estado"],
    ...demoFiltered().map((event) => [
      "DEMO",
      event.fecha,
      event.especie,
      event.cantidad,
      event.ubicacion,
      event.estado,
    ]),
  ];
  const content =
    "\ufeff" +
    rows
      .map((row) =>
        row
          .map((value) => '"' + String(value).replaceAll('"', '""') + '"')
          .join(","),
      )
      .join("\r\n");
  const url = URL.createObjectURL(
    new Blob([content], { type: "text/csv;charset=utf-8" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = "nawi-apu-demostracion.csv";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function clearLocationDraft() {
  placing = false;
  $("map-help").hidden = true;
  if (draftMarker && map) {
    map.removeLayer(draftMarker);
    draftMarker = null;
  }
}
function resetAndRefresh() {
  page = 1;
  refresh();
}
for (const button of document.querySelectorAll("[data-settings]"))
  button.addEventListener("click", showSettings);
for (const button of document.querySelectorAll("[data-export]"))
  button.addEventListener("click", exportData);
for (const button of document.querySelectorAll("[data-close]"))
  button.addEventListener("click", () => button.closest("dialog").close());
$("location-dialog").addEventListener("close", clearLocationDraft);
$("settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const value = $("control-key").value;
  if (value) {
    key = value;
    sessionStorage.setItem("nawi-control-key", key);
  }
  $("settings-dialog").close();
  if (pendingAction) {
    const action = pendingAction;
    pendingAction = null;
    await mutate(action);
  } else notify("Configuración guardada.");
  refresh();
});
for (const input of document.querySelectorAll('input[name="theme"]'))
  input.addEventListener("change", () => applyTheme(input.value));

// "Ubicar estación" abre el diálogo con el GPS como opción principal.
$("pin-button").addEventListener("click", () => showLocation());
// Alternativa: tocar el punto en el mapa.
$("map-pick-button").addEventListener("click", () => {
  $("location-dialog").close();
  if (!map) return;
  placing = true;
  $("map-help").hidden = false;
  $("map").style.cursor = "crosshair";
  notify("Toca en el mapa el punto donde está la cámara.");
});
$("location-form").addEventListener("submit", (event) => {
  event.preventDefault();
  mutate(async () => {
    await post("/api/ubicaciones", {
      nombre:
        $("location-name").value.trim() ||
        ($("location-type").value === "nido" ? "Nido observado" : "Estación ÑAWI APU"),
      tipo: $("location-type").value,
      latitud: Number($("latitude").value),
      longitud: Number($("longitude").value),
    });
    $("location-dialog").close();
    notify("Ubicación guardada. Se asociará a los próximos eventos.");
    await refresh();
  });
});
$("expand-map").addEventListener("click", async () => {
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else await $("mapa").requestFullscreen();
    setTimeout(() => map?.invalidateSize(), 100);
  } catch {
    notify("Tu navegador no permite ampliar el mapa en pantalla completa.");
  }
});
document.addEventListener("fullscreenchange", () =>
  setTimeout(() => map?.invalidateSize(), 100),
);
$("period-filter").addEventListener("change", () => {
  if ($("period-filter").value === "custom") $("dates-dialog").showModal();
  else {
    range = null;
    resetAndRefresh();
  }
});
$("dates-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const start = new Date($("date-start").value),
    end = new Date($("date-end").value);
  if (!(start < end)) {
    notify("La fecha inicial debe ser anterior a la final.", true);
    return;
  }
  range = { start: start.toISOString(), end: end.toISOString() };
  $("dates-dialog").close();
  resetAndRefresh();
});
for (const id of ["species-filter", "rows-per-page"])
  $(id).addEventListener("change", resetAndRefresh);
function setStateFilter(state) {
  stateFilter = state;
  for (const chip of document.querySelectorAll("[data-state]"))
    chip.setAttribute("aria-pressed", chip.dataset.state === state);
  resetAndRefresh();
}
for (const chip of document.querySelectorAll("[data-state]"))
  chip.addEventListener("click", () => setStateFilter(chip.dataset.state));

for (const button of document.querySelectorAll("[data-sort]"))
  button.addEventListener("click", () => {
    direction =
      button.dataset.sort === order && direction === "desc" ? "asc" : "desc";
    order = button.dataset.sort;
    resetAndRefresh();
  });
for (const button of document.querySelectorAll("[data-series]"))
  button.addEventListener("click", () => {
    const name = button.dataset.series;
    if (visibleSeries.has(name)) visibleSeries.delete(name);
    else visibleSeries.add(name);
    button.setAttribute("aria-pressed", visibleSeries.has(name));
    if (summaryData) renderCharts(summaryData);
  });
$("previous-page").addEventListener("click", () => {
  if (page > 1) {
    page--;
    refresh();
  }
});
$("next-page").addEventListener("click", () => {
  if (page < pages) {
    page++;
    refresh();
  }
});
// Ubicar la estación con el GPS del dispositivo (pensado para el celular junto a la cámara).
$("gps-button").addEventListener("click", () => {
  if (!("geolocation" in navigator)) {
    notify("Este navegador no permite usar el GPS.", true);
    return;
  }
  $("gps-button").disabled = true;
  $("gps-help").textContent = "Buscando señal GPS…";
  navigator.geolocation.getCurrentPosition(
    (position) => {
      const { latitude, longitude, accuracy } = position.coords;
      $("latitude").value = latitude.toFixed(6);
      $("longitude").value = longitude.toFixed(6);
      $("gps-help").textContent =
        "Ubicación obtenida con una precisión de ±" +
        Math.round(accuracy) +
        " m. Revisa el nombre y guarda.";
      if (map) {
        if (draftMarker) map.removeLayer(draftMarker);
        draftMarker = L.marker([latitude, longitude], {
          icon: markerIcon($("location-type").value),
          opacity: 0.8,
        }).addTo(map);
        map.setView([latitude, longitude], 17);
      }
      $("gps-button").disabled = false;
    },
    (error) => {
      $("gps-help").textContent =
        error.code === error.PERMISSION_DENIED
          ? "Permiso de ubicación denegado. Actívalo en el navegador o escribe las coordenadas."
          : "No se pudo obtener la ubicación. Intenta al aire libre o escribe las coordenadas.";
      $("gps-button").disabled = false;
    },
    { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 },
  );
});
function toggleDemo() {
  demo = !demo;
  page = 1;
  centeredPrototype = "";
  refresh();
}
$("demo-button").addEventListener("click", toggleDemo);
$("exit-demo").addEventListener("click", () => {
  demo = false;
  page = 1;
  centeredPrototype = "";
  refresh();
});
$("menu-button").addEventListener("click", () =>
  $("sidebar").classList.toggle("open"),
);
const navLinks = [...document.querySelectorAll(".sidebar nav a[href^='#']")];
function selectNav(id) {
  for (const link of navLinks)
    link.classList.toggle("selected", link.getAttribute("href") === "#" + id);
}
let navLockUntil = 0;
for (const anchor of navLinks)
  anchor.addEventListener("click", () => {
    const id = anchor.getAttribute("href").slice(1);
    selectNav(id);
    // Durante el desplazamiento suave no se recalcula la sección visible.
    navLockUntil = Date.now() + 900;
    $("sidebar").classList.remove("open");
    const target = $(id);
    if (target && id !== "resumen") {
      target.classList.remove("section-focus");
      void target.offsetWidth; // reinicia la animación si se pulsa dos veces
      target.classList.add("section-focus");
      setTimeout(() => target.classList.remove("section-focus"), 1700);
    }
  });
// El menú sigue al desplazamiento: marca la última sección cuyo inicio ya pasó
// por la franja superior de la pantalla (o la última, al llegar al final).
let spyQueued = false;
function updateNavFromScroll() {
  spyQueued = false;
  if (Date.now() < navLockUntil) return;
  const sections = navLinks
    .map((link) => $(link.getAttribute("href").slice(1)))
    .filter(Boolean)
    .map((section) => ({ id: section.id, top: section.getBoundingClientRect().top }))
    .sort((a, b) => a.top - b.top);
  if (!sections.length) return;
  const atBottom =
    window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4;
  let current = sections[0].id;
  for (const section of sections)
    if (section.top <= window.innerHeight * 0.3) current = section.id;
  if (atBottom) current = sections[sections.length - 1].id;
  if (window.scrollY < 40) current = "resumen";
  selectNav(current);
}
window.addEventListener(
  "scroll",
  () => {
    if (!spyQueued) {
      spyQueued = true;
      requestAnimationFrame(updateNavFromScroll);
    }
  },
  { passive: true },
);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    $("sidebar").classList.remove("open");
    clearLocationDraft();
  }
});
// Link directo desde la alerta de WhatsApp: ?evento=<id> abre esa detección.
async function openLinkedEvent() {
  const id = new URLSearchParams(location.search).get("evento");
  if (!id || demo) return;
  try {
    openEvent(await api("/api/detecciones/" + encodeURIComponent(id)));
  } catch {
    notify("No se encontró la detección del enlace.", true);
  }
}
initMap();
refresh().then(openLinkedEvent);
setInterval(refresh, 5000);
setInterval(tickUpdated, 1000);
