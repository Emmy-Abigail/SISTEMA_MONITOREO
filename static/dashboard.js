"use strict";

const byId = (id) => document.getElementById(id);
const dateFormat = new Intl.DateTimeFormat("es-PE", {
  dateStyle: "short",
  timeStyle: "short",
});
let controlKey = sessionStorage.getItem("monitor-control-key") || "";
let pendingMode = null;
let initialized = false;
let lastSnapshot = null;
let loading = false;
let chartSeries = [];

function notice(message) {
  byId("notice").textContent = message;
  byId("notice").hidden = !message;
}

async function api(path, options = {}) {
  const response = await fetch(path, { cache: "no-store", ...options });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(
      data.error || `El servidor respondió con HTTP ${response.status}.`,
    );
    error.status = response.status;
    throw error;
  }
  return data;
}

function formatDate(value) {
  return dateFormat.format(new Date(value));
}

function imageUrl(event) {
  if (event.tiene_imagen) {
    return `/api/detecciones/${encodeURIComponent(event.id)}/imagen`;
  }
  return event.imagen_url || null;
}

function renderEvents(events) {
  const fragment = document.createDocumentFragment();
  for (const event of events) {
    const row = document.createElement("tr");
    const captureCell = document.createElement("td");
    const source = imageUrl(event);
    if (source) {
      const button = document.createElement("button");
      button.className = "thumbnail";
      button.type = "button";
      button.setAttribute("aria-label", `Abrir captura de ${event.especie}`);
      const image = document.createElement("img");
      image.src = source;
      image.alt = "";
      image.loading = "lazy";
      image.referrerPolicy = "no-referrer";
      button.append(image);
      button.addEventListener("click", () => {
        byId("image-title").textContent =
          `${event.especie === "tortugas" ? "Tortugas" : "Gaviotines"} · ${formatDate(event.fecha)}`;
        byId("detail-image").src = source;
        byId("image-dialog").showModal();
      });
      captureCell.append(button);
    } else {
      captureCell.textContent = "Sin captura";
      captureCell.className = "no-image";
    }
    row.append(captureCell);
    const values = [
      event.especie === "tortugas" ? "Tortugas" : "Gaviotines",
      String(event.cantidad),
      event.confianza == null ? "—" : `${Math.round(event.confianza * 100)} %`,
      event.dispositivo,
      formatDate(event.fecha),
    ];
    for (const [index, value] of values.entries()) {
      const cell = document.createElement("td");
      cell.textContent = value;
      if (index === 0) {
        const dot = document.createElement("span");
        dot.className = `event-dot ${event.especie}`;
        const label = document.createElement("span");
        label.className = "event-species";
        label.append(dot, document.createTextNode(value));
        cell.replaceChildren(label);
      }
      row.append(cell);
    }
    fragment.append(row);
  }
  byId("events-body").replaceChildren(fragment);
  byId("table-empty").hidden = events.length > 0;
}

function drawChart() {
  const canvas = byId("activity-chart");
  const ratio = window.devicePixelRatio || 1;
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  canvas.width = width * ratio;
  canvas.height = height * ratio;
  const context = canvas.getContext("2d");
  context.scale(ratio, ratio);
  const hours = Number(byId("period-filter").value);
  const end = Date.now();
  const start = end - hours * 3600000;
  const bins = Array(24).fill(0);
  for (const item of chartSeries) {
    const timestamp = Date.parse(`${item.hora}:00:00+00:00`);
    const position = Math.floor(
      ((timestamp - start) / (end - start)) * bins.length,
    );
    bins[Math.max(0, Math.min(bins.length - 1, position))] += item.eventos;
  }
  const max = Math.max(1, ...bins);
  const plotHeight = height - 28;
  const spacing = width / bins.length;
  context.strokeStyle = "#e6edf1";
  for (let level = 0; level <= 2; level += 1) {
    const y = 4 + (level * (plotHeight - 4)) / 2;
    context.beginPath();
    context.moveTo(0, y);
    context.lineTo(width, y);
    context.stroke();
  }
  for (const [index, value] of bins.entries()) {
    context.fillStyle = value ? "#138693" : "#edf3f6";
    const barHeight = value ? Math.max(5, (value / max) * (plotHeight - 6)) : 3;
    context.fillRect(
      index * spacing + 3,
      plotHeight - barHeight,
      Math.max(3, spacing - 6),
      barHeight,
    );
  }
  context.fillStyle = "#617685";
  context.font = "11px Barlow, sans-serif";
  context.textAlign = "left";
  context.fillText(
    new Date(start).toLocaleString("es-PE", {
      day: "2-digit",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    }),
    0,
    height - 5,
  );
  context.textAlign = "right";
  context.fillText("Ahora", width, height - 5);
  canvas.setAttribute(
    "aria-label",
    `${chartSeries.reduce((total, item) => total + item.eventos, 0)} eventos en las últimas ${hours} horas.`,
  );
}

function renderDevice(device) {
  const online = Boolean(device?.conectado);
  const labels = {
    esperando: "Cámara conectada · en espera",
    monitoreando: "Cámara monitoreando",
    error: "Error de cámara o modelo",
    desconectado: "Monitor desconectado",
  };
  byId("camera-dot").className = `signal-dot ${online ? "online" : ""}`;
  byId("device-status").textContent = online
    ? labels[device.estado]
    : "Sin dispositivo conectado";
  byId("device-name").textContent = device?.nombre || "Cámara PC";
  const hasImage = Boolean(device?.tiene_imagen);
  byId("camera-image").hidden = !hasImage;
  byId("camera-empty").hidden = hasImage;
  byId("camera-caption").hidden = !hasImage;
  byId("camera-caption").textContent = online
    ? "Última imagen del dispositivo"
    : "Última imagen · dispositivo sin conexión";
  byId("last-frame").textContent = device
    ? formatDate(device.fecha)
    : "Sin imagen recibida";
  if (hasImage && lastSnapshot !== device.fecha) {
    byId("camera-image").src =
      `/api/camara/imagen?t=${encodeURIComponent(device.fecha)}`;
    lastSnapshot = device.fecha;
  }
}

async function refresh() {
  if (loading) return;
  loading = true;
  try {
    const params = new URLSearchParams({
      horas: byId("period-filter").value,
      especie: byId("species-filter").value,
    });
    const [data, config] = await Promise.all([
      api(`/api/resumen?${params}`),
      api("/config"),
    ]);
    byId("connection-dot").className = "signal-dot online";
    byId("connection-label").textContent = "Servidor conectado";
    const counts = Object.fromEntries(
      data.totales.map((item) => [item.especie, item.cantidad]),
    );
    const total = data.totales.reduce((count, item) => count + item.eventos, 0);
    byId("turtle-count").textContent = counts.tortugas || 0;
    byId("bird-count").textContent = counts.gaviotines || 0;
    byId("chart-total").textContent = `${total} eventos`;
    byId("event-caption").textContent =
      total > 100
        ? `${total} eventos · mostrando los 100 más recientes`
        : `${total} eventos registrados en el período`;
    byId("chart-empty").hidden = total > 0;
    const mode = config.mode;
    byId("mode-label").textContent =
      mode === "detenido" ? "En espera" : `Modo ${mode}`;
    if (!initialized && ["tortugas", "gaviotines"].includes(mode))
      byId("mode-select").value = mode;
    initialized = true;
    byId("last-update").textContent =
      `Actualizado ${new Date(data.actualizado).toLocaleTimeString("es-PE")}`;
    byId("clock").textContent = new Date().toLocaleDateString("es-PE", {
      day: "numeric",
      month: "long",
      year: "numeric",
    });
    renderDevice(data.dispositivo);
    renderEvents(data.eventos);
    chartSeries = data.serie;
    drawChart();
    if (!data.almacenamiento_persistente)
      notice(
        "El historial usa almacenamiento temporal. Conecta un volumen al servicio de Railway para conservar detecciones entre despliegues.",
      );
  } catch (error) {
    byId("connection-dot").className = "signal-dot error";
    byId("connection-label").textContent = "Sin conexión al servidor";
    notice(`No se pudo actualizar el monitoreo. ${error.message}`);
  } finally {
    loading = false;
  }
}

async function sendMode(mode) {
  byId("start-button").disabled = true;
  byId("stop-button").disabled = true;
  try {
    await api("/api/control", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-ALERTA-KEY": controlKey,
      },
      body: JSON.stringify({ mode }),
    });
    byId("control-hint").textContent =
      mode === "detenido"
        ? "Orden de detención guardada. El dispositivo la recibirá en su próxima consulta."
        : "Modo guardado. El dispositivo conectado lo recibirá en su próxima consulta.";
    notice("");
    await refresh();
  } catch (error) {
    if (error.status === 401) {
      pendingMode = mode;
      byId("key-dialog").showModal();
      byId("control-key").focus();
    } else {
      notice(error.message);
    }
  } finally {
    byId("start-button").disabled = false;
    byId("stop-button").disabled = false;
  }
}

byId("start-button").addEventListener("click", () =>
  sendMode(byId("mode-select").value),
);
byId("stop-button").addEventListener("click", () => sendMode("detenido"));
byId("key-button").addEventListener("click", () => {
  pendingMode = null;
  byId("key-dialog").showModal();
});
byId("key-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  controlKey = byId("control-key").value;
  sessionStorage.setItem("monitor-control-key", controlKey);
  const mode = pendingMode;
  pendingMode = null;
  byId("key-dialog").close();
  byId("control-key").value = "";
  if (mode) await sendMode(mode);
});
byId("setup-button").addEventListener("click", () => {
  byId("setup-command").textContent =
    `.\\.venv\\Scripts\\python.exe -m src.monitor_pc --url ${window.location.origin}`;
  byId("setup-dialog").showModal();
});
for (const button of document.querySelectorAll("[data-close]")) {
  button.addEventListener("click", () => {
    pendingMode = null;
    button.closest("dialog").close();
  });
}
byId("species-filter").addEventListener("change", refresh);
byId("period-filter").addEventListener("change", refresh);
window.addEventListener("resize", drawChart);
refresh();
setInterval(refresh, 5000);
