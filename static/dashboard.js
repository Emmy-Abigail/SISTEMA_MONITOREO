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
let searchTimer = null;
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
      estado: ["confirmado", "nuevo", "confirmado", "en_revision"][i % 4],
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
function params() {
  const values = new URLSearchParams({
    horas:
      $("period-filter").value === "custom" ? "24" : $("period-filter").value,
    especie: $("species-filter").value,
    estado: $("state-filter").value,
    buscar: $("global-search").value.trim(),
  });
  if (range) {
    values.set("desde", range.start);
    values.set("hasta", range.end);
  }
  return values;
}
function demoFiltered() {
  const values = params();
  const end = range ? Date.parse(range.end) : Date.now();
  const start = range
    ? Date.parse(range.start)
    : end - Number(values.get("horas")) * 3600000;
  const search = values.get("buscar").toLocaleLowerCase("es");
  return demoEvents
    .filter(
      (event) =>
        Date.parse(event.fecha) >= start && Date.parse(event.fecha) <= end,
    )
    .filter(
      (event) =>
        values.get("especie") === "todas" ||
        event.especie === values.get("especie"),
    )
    .filter(
      (event) =>
        values.get("estado") === "todos" ||
        event.estado === values.get("estado"),
    )
    .filter(
      (event) =>
        !search ||
        [event.especie, event.ubicacion, event.tipo_evento, event.dispositivo]
          .join(" ")
          .toLocaleLowerCase("es")
          .includes(search),
    );
}
function demoSummary(events) {
  const end = new Date();
  const hours = range
    ? Math.max(1, (Date.parse(range.end) - Date.parse(range.start)) / 3600000)
    : Number(params().get("horas"));
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
  $("refresh-button").classList.add("refreshing");
  try {
    let data, records, config;
    if (demo) {
      const events = demoFiltered();
      data = demoSummary(events);
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
        api("/api/resumen?" + query),
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
      demo
        ? ""
        : !data.almacenamiento_persistente
          ? "Conecta un volumen al servicio Railway para conservar el historial entre despliegues."
          : data.coordenadas_exactas === false
            ? "Ubicaciones aproximadas (~1 km) para proteger los nidos. Introduce la clave de control en Configuración para ver las exactas."
            : "",
    );
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
    $("refresh-button").classList.remove("refreshing");
    if (refreshAgain) {
      refreshAgain = false;
      refresh();
    }
  }
}
function renderKpis(kpis) {
  for (const [name, item] of Object.entries(kpis)) {
    const value = $("kpi-" + name),
      change = $("change-" + name);
    if (!value) continue;
    value.textContent = Number(item.valor).toLocaleString("es-PE");
    value.classList.remove("loading-value");
    change.className = "";
    if (item.variacion === null) change.textContent = "Sin período comparable";
    else {
      change.textContent =
        (item.variacion >= 0 ? "↑ +" : "↓ ") +
        item.variacion +
        "% vs. período anterior";
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
          ? "Objetos reportados"
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
    } else label = "En espera de WhatsApp";
  }
  $("monitor-status").textContent = label;
  $("status-pill").className = "status-pill " + style;
  $("status-pill").querySelector(".status-dot").className =
    "status-dot " + (online && !demo ? "online" : "");
  $("last-update").textContent =
    "Actualizado " + timeFormat.format(new Date(data.actualizado));
  $("peru-clock").textContent = timeFormat.format(new Date());
  if (document.activeElement !== $("control-mode"))
    $("control-mode").value = config.mode;
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
  const datasets = ["tortugas", "gaviotines", "invasores"].map(
    (name, index) => ({
      label: {
        tortugas: "Tortugas",
        gaviotines: "Gaviotines",
        invasores: "Amenazas",
      }[name],
      data: data.serie.map((item) => item[name] || 0),
      borderColor: palette[name],
      backgroundColor: palette[name] + "12",
      fill: index === 0,
      borderWidth: 2,
      pointRadius: 0,
      pointHoverRadius: 4,
      tension: 0.35,
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
              font: { family: "Inter", size: 8 },
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
              font: { family: "Inter", size: 8 },
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
              tension: 0.4,
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
  const tiles = L.tileLayer(
    "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
    {
      maxZoom: 19,
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
    },
  ).addTo(map);
  tiles.on("tileerror", () => {
    $("location-label").title =
      "El mapa base requiere conexión a internet. Las ubicaciones guardadas siguen disponibles.";
  });
  markerLayer = L.layerGroup().addTo(map);
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
function renderMap(data) {
  const prototype = data.ubicaciones.find((item) => item.tipo === "prototipo");
  $("location-label").replaceChildren();
  const icon = document.createElement("i");
  icon.className = "bi bi-geo-alt";
  $("location-label").append(
    icon,
    document.createTextNode(
      prototype ? prototype.nombre : "Ubicación del prototipo pendiente",
    ),
  );
  $("location-label").title = prototype
    ? prototype.latitud.toFixed(6) + ", " + prototype.longitud.toFixed(6)
    : "Fija la ubicación donde dejarás el prototipo.";
  if (!map) return;
  markerLayer.clearLayers();
  for (const place of data.ubicaciones) {
    L.marker([place.latitud, place.longitud], {
      icon: markerIcon(place.tipo),
      title: place.nombre,
    })
      .addTo(markerLayer)
      .bindPopup(
        popup(
          place.nombre,
          place.tipo === "prototipo"
            ? "Ubicación del prototipo"
            : "Nido observado",
        ),
      );
  }
  for (const point of data.puntos) {
    if (point.latitud == null || point.longitud == null) continue;
    const label =
      point.especie === "invasores"
        ? "!"
        : point.especie === "tortugas"
          ? "T"
          : "G";
    L.marker([point.latitud, point.longitud], {
      icon: markerIcon(point.especie, label),
      title: speciesNames[point.especie],
    })
      .addTo(markerLayer)
      .bindPopup(
        popup(
          speciesNames[point.especie] + " · " + point.cantidad,
          point.ubicacion + " · " + timeFormat.format(new Date(point.fecha)),
        ),
      );
  }
  const identity = prototype
    ? prototype.latitud + "," + prototype.longitud
    : "";
  if (identity && identity !== centeredPrototype) {
    map.setView([prototype.latitud, prototype.longitud], 12, {
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
  if (map && lat != null) {
    if (draftMarker) map.removeLayer(draftMarker);
    draftMarker = L.marker([lat, lon], {
      icon: markerIcon("prototipo"),
      opacity: 0.7,
    }).addTo(map);
  }
  $("location-dialog").showModal();
}
function renderThreats(threats) {
  const root = $("threat-list");
  root.replaceChildren();
  if (!threats.length) {
    const p = document.createElement("p");
    p.className = "panel-empty";
    const icon = document.createElement("i");
    icon.className = "bi bi-shield-check";
    p.append(
      icon,
      document.createTextNode("Sin amenazas registradas en este período."),
    );
    root.append(p);
    return;
  }
  for (const threat of threats.slice(0, 8)) {
    const row = document.createElement("div");
    row.className = "threat-row";
    const button = document.createElement("button");
    button.type = "button";
    const icon = document.createElement("i");
    icon.className = "bi bi-exclamation-triangle";
    const title = document.createElement("span");
    title.textContent = threat.tipo_evento;
    title.title = threat.ubicacion || "Sin ubicación";
    button.append(icon, title);
    button.addEventListener("click", () => openEvent(threat));
    const severity = document.createElement("span");
    severity.className = "severity " + threat.severidad;
    severity.textContent = severityNames[threat.severidad] || "Advertencia";
    row.append(button, severity);
    root.append(row);
  }
}
function cell(text, className = "") {
  const td = document.createElement("td");
  td.textContent = text ?? "—";
  td.className = className;
  return td;
}
function renderTable(records) {
  const body = $("events-body");
  body.replaceChildren();
  for (const event of records.eventos) {
    const row = document.createElement("tr");
    const date = cell(dateFormat.format(new Date(event.fecha)), "date-cell");
    const time = document.createElement("small");
    time.textContent = timeFormat.format(new Date(event.fecha));
    date.append(time);
    row.append(date);
    const species = cell("");
    const wrap = document.createElement("div");
    wrap.className = "species-label";
    const emoji = document.createElement("span");
    emoji.className = "species-emoji";
    emoji.textContent = speciesIcons[event.especie];
    const label = document.createElement("span");
    label.textContent = speciesNames[event.especie] || event.especie;
    const count = document.createElement("small");
    count.textContent = event.cantidad + " objetos reportados";
    label.append(count);
    wrap.append(emoji, label);
    species.append(wrap);
    row.append(species);
    const place = cell("", "location-cell");
    const pin = document.createElement("i");
    pin.className = "bi bi-geo-alt";
    place.append(
      pin,
      document.createTextNode(event.ubicacion || "Sin ubicación"),
    );
    row.append(place);
    row.append(
      cell(
        event.tipo_evento === "deteccion" ? "Avistamiento" : event.tipo_evento,
      ),
    );
    const confidence = cell("");
    if (event.confianza == null) confidence.textContent = "—";
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
    const status = cell("");
    const badge = document.createElement("span");
    badge.className = "badge " + event.estado;
    const symbol = document.createElement("i");
    symbol.className =
      "bi " +
      ({
        nuevo: "bi-circle",
        en_revision: "bi-clock-history",
        confirmado: "bi-check-circle",
        resuelto: "bi-check2-all",
      }[event.estado] || "bi-circle");
    badge.append(
      symbol,
      document.createTextNode(stateNames[event.estado] || "Nuevo"),
    );
    status.append(badge);
    row.append(status);
    const actionCell = cell("");
    const action = document.createElement("button");
    action.type = "button";
    action.className = "icon-button";
    action.setAttribute(
      "aria-label",
      "Revisar evento del " + dateFormat.format(new Date(event.fecha)),
    );
    action.innerHTML = '<i class="bi bi-three-dots" aria-hidden="true"></i>';
    action.addEventListener("click", () => openEvent(event));
    actionCell.append(action);
    row.append(actionCell);
    body.append(row);
  }
  $("records-empty").hidden = records.total > 0;
  $("record-count").textContent =
    records.total + " eventos en el período seleccionado";
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
  $("event-title").textContent =
    speciesNames[event.especie] || "Detalle del evento";
  const root = $("event-detail");
  root.replaceChildren();
  const details = {
    Fecha:
      dateFormat.format(new Date(event.fecha)) +
      " " +
      timeFormat.format(new Date(event.fecha)),
    Ubicación: event.ubicacion || "Sin ubicación",
    Tipo: event.tipo_evento,
    Cantidad: event.cantidad,
    Severidad: severityNames[event.severidad] || "Informativa",
    Dispositivo: event.dispositivo,
  };
  for (const [name, value] of Object.entries(details)) {
    const item = document.createElement("div");
    const label = document.createElement("span");
    label.textContent = name;
    item.append(label, document.createTextNode(value));
    root.append(item);
  }
  const image = event.tiene_imagen
    ? "/api/detecciones/" + encodeURIComponent(event.id) + "/imagen"
    : event.imagen_url;
  $("event-image").hidden = !image;
  if (image) $("event-image").src = image;
  else $("event-image").removeAttribute("src");
  $("event-state").value = event.estado;
  $("event-dialog").showModal();
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
      link.download = "nawi-apu-detecciones.csv";
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
$("apply-mode").addEventListener("click", () =>
  mutate(async () => {
    await post("/api/control", { mode: $("control-mode").value });
    notify("Modo actualizado. El monitor conectado recibirá el cambio.");
    await refresh();
  }),
);
$("pin-button").addEventListener("click", () => {
  if (!map) {
    showLocation();
    return;
  }
  placing = !placing;
  $("map-help").hidden = !placing;
  $("map").style.cursor = placing ? "crosshair" : "";
  if (placing)
    notify("Haz clic en el punto del mapa donde se instalará el prototipo.");
});
$("location-form").addEventListener("submit", (event) => {
  event.preventDefault();
  mutate(async () => {
    await post("/api/ubicaciones", {
      nombre: $("location-name").value.trim(),
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
$("global-search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(resetAndRefresh, 300);
});
for (const id of ["species-filter", "state-filter", "rows-per-page"])
  $(id).addEventListener("change", resetAndRefresh);
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
for (const button of document.querySelectorAll("[data-species]"))
  button.addEventListener("click", () => {
    $("species-filter").value = button.dataset.species;
    resetAndRefresh();
    $("registros").scrollIntoView({ behavior: "smooth", block: "nearest" });
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
$("refresh-button").addEventListener("click", refresh);
$("save-event-state").addEventListener("click", () =>
  mutate(async () => {
    if (!focusedEvent) return;
    await post(
      "/api/detecciones/" + encodeURIComponent(focusedEvent.id),
      { estado: $("event-state").value },
      "PATCH",
    );
    $("event-dialog").close();
    notify("Estado del evento actualizado.");
    await refresh();
  }),
);
$("report-threat").addEventListener("click", () =>
  $("threat-dialog").showModal(),
);
$("threat-form").addEventListener("submit", (event) => {
  event.preventDefault();
  mutate(async () => {
    await post("/api/detecciones", {
      evento_id: crypto.randomUUID(),
      especie: "invasores",
      cantidad: 1,
      tipo_evento: $("threat-type").value,
      severidad: $("threat-severity").value,
      estado: "en_revision",
      dispositivo: "Observación manual",
    });
    $("threat-dialog").close();
    notify("Amenaza observada registrada.");
    await refresh();
  });
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
for (const anchor of document.querySelectorAll(".sidebar nav a"))
  anchor.addEventListener("click", () => {
    document
      .querySelector(".sidebar nav a.selected")
      ?.classList.remove("selected");
    anchor.classList.add("selected");
    $("sidebar").classList.remove("open");
  });
document.addEventListener("keydown", (event) => {
  if (
    event.key === "/" &&
    !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)
  ) {
    event.preventDefault();
    $("global-search").focus();
  }
  if (event.key === "Escape") {
    $("sidebar").classList.remove("open");
    clearLocationDraft();
  }
});
initMap();
refresh();
setInterval(refresh, 5000);
