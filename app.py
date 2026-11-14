import os
import json
import time
import shutil
import tempfile
import threading
from flask import Flask, request, jsonify, abort
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from twilio.request_validator import RequestValidator
from twilio.base.exceptions import TwilioRestException
from werkzeug.middleware.proxy_fix import ProxyFix
from datetime import datetime, timezone, timedelta
from utils.dashboard import initialize, configured_mode, set_mode, save_detection, prototype, connect, authorize, public_location, device_status, daily_summary
from utils import verificacion, respuestas

app = Flask(__name__)
# Railway termina HTTPS en su proxy: sin esto Flask vería "http://" y la firma de Twilio no coincidiría.
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024
app.config["ALERTA_KEY"] = os.environ.get("ALERTA_KEY", "")
initialize(app)

# -----------------------
# Configuración
# -----------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LEGACY_DATA_DIR = os.path.join(BASE_DIR, "data")
DATA_DIR = str(app.config["MONITOR_DATA_DIR"])
os.makedirs(DATA_DIR, exist_ok=True)

# El volumen de Railway conserva los archivos entre despliegues.
USUARIOS_FILE = os.path.join(DATA_DIR, "usuarios.json")
ESTADOS_FILE = os.path.join(DATA_DIR, "estados.json")

for name in ("usuarios.json", "estados.json"):
    old_path = os.path.join(LEGACY_DATA_DIR, name)
    new_path = os.path.join(DATA_DIR, name)
    if os.path.exists(old_path) and not os.path.exists(new_path):
        shutil.copyfile(old_path, new_path)

# Crear archivos si no existen
for f in [USUARIOS_FILE, ESTADOS_FILE]:
    if not os.path.exists(f):
        with open(f, "w") as file:
            json.dump({}, file)

# Variables de entorno
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_FROM = os.environ.get("TWILIO_WHATSAPP_FROM")
ALERTA_KEY = os.environ.get("ALERTA_KEY", "")
DASHBOARD_URL = os.environ.get(
    "DASHBOARD_URL", "https://sistemamonitoreo-production-8883a6d5.up.railway.app/")
# URL pública exacta configurada en Twilio. Solo hace falta si difiere de la que ve Flask.
TWILIO_WEBHOOK_URL = os.environ.get("TWILIO_WEBHOOK_URL", "").strip()
# Si es "0", no se valida la firma de Twilio (solo para pruebas locales).
VALIDAR_FIRMA_TWILIO = os.environ.get("VALIDAR_FIRMA_TWILIO", "1") != "0"


def normalizar_numero(numero):
    """Convierte '+51 999 888 777' o 'whatsapp:+51999888777' en 'whatsapp:+51999888777'."""
    numero = "".join(str(numero).split())
    if not numero:
        return ""
    return numero if numero.startswith("whatsapp:") else f"whatsapp:{numero}"


# Números que pueden usar el bot y recibir alertas, separados por comas.
# Vacío = cualquiera que escriba al bot queda registrado (solo recomendable en pruebas).
WHATSAPP_AUTORIZADOS = {
    normalizar_numero(n) for n in os.environ.get("WHATSAPP_AUTORIZADOS", "").split(",") if n.strip()
}


def numero_autorizado(numero):
    return not WHATSAPP_AUTORIZADOS or normalizar_numero(numero) in WHATSAPP_AUTORIZADOS


# Una alerta "procesando" más antigua que esto se considera interrumpida y se reintenta.
ALERTA_TRABADA_SEGUNDOS = 120
_json_lock = threading.Lock()

# Guardamos el tiempo de inicio para calcular el Uptime
TIEMPO_INICIO = datetime.now()

# -----------------------
# Funciones de Diseño (UI de Texto)
# -----------------------
# -----------------------
# Textos del bot (tono cercano, en "tú", sin tecnicismos)
# -----------------------
PALABRAS_MENU = {"menu", "hola", "holi", "inicio", "opciones", "empezar", "buenas", "buenos dias",
                 "buenas tardes", "buenas noches", "hi", "hello"}
PALABRAS_AYUDA = {"ayuda", "help", "como funciona", "info", "informacion"}
PALABRAS_PAUSA = {"3", "pausa", "pausar", "detener", "apagar", "parar"}
PALABRAS_ESTADO = {"4", "estado", "resumen", "status", "dashboard", "como va"}
PALABRAS_MODO = {
    "1": "tortugas", "tortuga": "tortugas", "tortugas": "tortugas",
    "2": "gaviotines", "gaviotin": "gaviotines", "gaviotines": "gaviotines", "aves": "gaviotines",
}
PALABRAS_DESHACER = {"deshacer", "deshaz", "corregir", "si era", "si habia"}
SEP = "─────────────────────"
MODOS = {
    "tortugas": ("🐢", "Tortugas", "incluye alerta de intrusos"),
    "gaviotines": ("🐦", "Gaviotines", "incluye alerta de intrusos"),
    "invasores": ("⚠️", "Solo intrusos", "personas, perros o vehículos"),
}


def generar_menu_principal():
    return (
        "*ÑAWI APU* | _Guardián costero_\n"
        f"{SEP}\n"
        "*Bienvenido, encargado de vigilancia.*\n\n"
        "1️⃣ *Vigilancia de tortugas* 🐢\n"
        "     ↳ _Incluye alerta de intrusos_\n"
        "2️⃣ *Vigilancia de gaviotines* 🐦\n"
        "     ↳ _Incluye alerta de intrusos_\n"
        "3️⃣ *Pausar vigilancia* ⏸️\n"
        "4️⃣ *Estado y dashboard* 📊\n"
        f"{SEP}\n"
        "_Responda con el número de la opción._"
    )


def generar_ayuda():
    return (
        "ℹ️ *AYUDA*\n"
        f"{SEP}\n"
        "‣ *1 o 2:* activar vigilancia\n"
        "‣ *3:* pausar vigilancia\n"
        "‣ *4:* estado y dashboard\n"
        "‣ *Menú:* ver las opciones\n"
        f"{SEP}\n"
        "_Ante una falsa alarma, responda la alerta con «NO» o descríbala "
        "(«no había nadie»). Para revertirlo, escriba «deshacer»._"
    )


def hora_peru(fecha_iso):
    return datetime.fromisoformat(fecha_iso).astimezone(HORA_PERU).strftime("%H:%M")


def texto_camara(modo):
    """Estado real de la cámara (según su último latido), no solo el modo elegido."""
    camara = device_status()
    if not camara["conectado"]:
        desde = f" desde las {hora_peru(camara['fecha'])}" if camara["fecha"] else ""
        return f"🔴 Sin conexión{desde}"
    if modo == "detenido" or camara["estado"] == "esperando":
        return "⏸️ En pausa"
    emoji, nombre, _ = MODOS.get(modo, ("👁️", modo, ""))
    extra = " + intrusos" if modo in ("tortugas", "gaviotines") else ""
    return f"🟢 Vigilando {nombre.lower()}{extra}"


def generar_estado(modo):
    """Opción 5: estado real de la cámara, resumen del día y acceso al dashboard."""
    resumen = daily_summary()
    ubicacion = prototype()
    lineas = ["📊 *ESTADO DE ÑAWI APU*", SEP, f"‣ *Cámara:* {texto_camara(modo)}"]
    if ubicacion:
        lineas.append(f"‣ *Estación:* {ubicacion['nombre']}")
    intrusos = resumen["invasores"]
    lineas.append(
        f"‣ *Hoy:* {resumen['tortugas']} tortugas · {resumen['gaviotines']} gaviotines · "
        f"{intrusos} {'intruso' if intrusos == 1 else 'intrusos'}"
    )
    if resumen["ultima"]:
        ultima = resumen["ultima"]
        que = {"tortugas": "tortuga", "gaviotines": "gaviotín"}.get(
            ultima["especie"], (ultima["tipo_evento"] or "intrusión").split(":", 1)[-1].strip())
        lineas.append(f"‣ *Última detección:* {hora_peru(ultima['fecha'])} ({que})")
    if resumen["falsas_alarmas"]:
        lineas.append(f"‣ *Falsas alarmas descartadas:* {resumen['falsas_alarmas']}")
    lineas += [SEP, f"📡 *Dashboard:* {DASHBOARD_URL}"]
    return "\n".join(lineas)


# Perú no usa horario de verano: UTC-5 fijo. El servidor de Railway corre en UTC,
# así que sin esto las alertas mostraban 5 horas de más.
HORA_PERU = timezone(timedelta(hours=-5), "PET")

ESPECIES_ALERTA = {
    "tortugas": ("🐢", "TORTUGA MARINA DETECTADA"),
    "gaviotines": ("🐦", "GAVIOTÍN DETECTADO"),
    "invasores": ("🚨", "ALERTA DE INTRUSIÓN"),
}


def generar_alerta(data, titulo=None):
    """Alerta de una detección (va con la foto): qué, cuántos, dónde y cuándo."""
    especie = str(data.get("especie", ""))
    emoji, nombre = ESPECIES_ALERTA.get(especie, ("👁️", "DETECCIÓN REGISTRADA"))
    try:
        fecha = datetime.fromisoformat(str(data.get("fecha")).replace("Z", "+00:00"))
        if fecha.tzinfo is None:
            fecha = fecha.replace(tzinfo=timezone.utc)
    except ValueError:
        fecha = datetime.now(timezone.utc)

    lineas = [titulo or f"{emoji} *{nombre}*", SEP]
    if especie == "invasores":
        # Si la IA identificó la amenaza, manda su clase (el modelo pudo confundirse).
        detalle = data.get("ia_clase") or str(data.get("tipo_evento") or "").split(":", 1)[-1].strip()
        if detalle:
            lineas.append(f"‣ *Detectado:* {detalle}")
    lineas.append(f"‣ *Cantidad:* {data.get('cantidad', 1)}")
    lineas.append(f"‣ *Hora:* {fecha.astimezone(HORA_PERU).strftime('%H:%M')}")
    ubicacion = data.get("ubicacion")
    if isinstance(ubicacion, dict) and ubicacion.get("nombre"):
        lineas.append(f"‣ *Lugar:* {ubicacion['nombre']}")
    if data.get("ia_descripcion"):
        lineas.append(f"_{data['ia_descripcion']}_")
    lineas += [SEP, "_Si es una falsa alarma, responda «NO» a este mensaje._"]
    return "\n".join(lineas)


# Solo se puede descartar con "NO" una alerta reciente, para no tocar eventos antiguos por error.
VENTANA_DESCARTE_HORAS = 24


# Plantilla de WhatsApp para alertas (Content Template de Twilio, categoría "Utility").
# Sin ella, las alertas solo llegan si el guardián escribió al bot en las últimas 24 h.
TWILIO_PLANTILLA_ALERTA = os.environ.get("TWILIO_PLANTILLA_ALERTA", "").strip()


def variables_plantilla(data):
    """Variables de la plantilla de alerta (WhatsApp no admite saltos de línea en ellas)."""
    especie = str(data.get("especie", ""))
    emoji, nombre = ESPECIES_ALERTA.get(especie, ("👁️", "Detección registrada"))
    if especie == "invasores":
        detalle = data.get("ia_clase") or str(data.get("tipo_evento") or "").split(":", 1)[-1].strip()
        titulo = f"{emoji} Intrusión: {detalle}" if detalle else f"{emoji} Intrusión detectada"
        cantidad = f"{data.get('cantidad', 1)} detectado(s)"
    else:
        titulo = f"{emoji} {nombre}"
        n = data.get("cantidad", 1)
        cantidad = f"{n} individuo" + ("" if n == 1 else "s")
    try:
        fecha = datetime.fromisoformat(str(data.get("fecha")).replace("Z", "+00:00"))
    except ValueError:
        fecha = datetime.now(timezone.utc)
    hora = fecha.astimezone(HORA_PERU).strftime("%d/%m %H:%M")
    return {
        "1": titulo,
        "2": f"{cantidad} · {hora}",
        "3": " ".join(str(data.get("ia_descripcion") or "Revise la zona.").split()),
        "4": str(data.get("evento_id", "")),  # para la URL de la foto
    }


def descartar_alerta(numero, sid_citado=None, especie=None, hora=None):
    """Marca como falso positivo la alerta que el guardián revisó en terreno.

    Prioridad: 1) la alerta citada (respuesta deslizando el mensaje en WhatsApp);
    2) la de la especie que menciona («no había intruso») y/o la hora («la de las 3:10»);
    3) la última que recibió. Solo alertas de las últimas horas. Devuelve el evento o None."""
    limite = (datetime.now(timezone.utc) - timedelta(hours=VENTANA_DESCARTE_HORAS)).isoformat(timespec="seconds")
    columnas = "d.id, d.especie, d.cantidad, d.fecha, d.estado, d.tipo_evento"
    with connect() as connection:
        evento = None
        if sid_citado:
            evento = connection.execute(
                f"SELECT {columnas} FROM alert_messages m JOIN detecciones d ON d.id = m.evento_id "
                "WHERE m.sid = ? AND m.destinatario = ?",
                (sid_citado, numero),
            ).fetchone()
        if evento is None:
            candidatos = connection.execute(
                f"SELECT {columnas} FROM alert_deliveries a JOIN detecciones d ON d.id = a.evento_id "
                "WHERE a.destinatario = ? AND d.fecha >= ? AND d.estado != 'descartado' "
                "ORDER BY d.fecha DESC",
                (numero, limite),
            ).fetchall()
            if especie:
                candidatos = [c for c in candidatos if c["especie"] == especie]
            if hora and candidatos:
                h, m = (int(x) for x in hora.split(":"))
                def distancia(c):
                    local = datetime.fromisoformat(c["fecha"]).astimezone(HORA_PERU)
                    minutos = local.hour * 60 + local.minute
                    # "3:10" puede ser 03:10 o 15:10: se toma la más cercana.
                    return min(abs(minutos - (h * 60 + m)), abs(minutos - ((h + 12) % 24 * 60 + m)))
                cercanos = [c for c in candidatos if distancia(c) <= 20]
                candidatos = sorted(cercanos, key=distancia)
            evento = candidatos[0] if candidatos else None
        if evento is None:
            return None
        connection.execute(
            "UPDATE detecciones SET estado='descartado', descartado_por='guardian' WHERE id=?",
            (evento["id"],),
        )
    return dict(evento)


# -----------------------
# Funciones auxiliares
# -----------------------
def cargar_json(ruta):
    try:
        with open(ruta, "r") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception as e:
        app.logger.warning(f"Error cargando JSON {ruta}: {e}")
        return {}

def guardar_json(ruta, data):
    # Escritura atómica: se escribe un archivo temporal y se reemplaza de una vez,
    # así un corte o dos peticiones simultáneas nunca dejan el JSON a medias.
    try:
        with _json_lock:
            fd, temporal = tempfile.mkstemp(dir=os.path.dirname(ruta), suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=4)
            os.replace(temporal, ruta)
    except Exception as e:
        app.logger.error(f"Error guardando JSON {ruta}: {e}")


# Errores de Twilio que no se arreglan reintentando (número inválido, no unido al
# Sandbox, cuenta trial restringida...). Reintentarlos solo gasta llamadas.
ESTADOS_HTTP_PERMANENTES = {400, 403, 404}


def enviar_whatsapp(numero_destino, texto, media_url=None, evento_id=None, plantilla=None):
    """Devuelve "ok", "permanente" (no reintentar) o "temporal" (reintentar más tarde).

    Si se indica `evento_id`, se guarda el SID del mensaje: así, cuando el guardián
    responda citando esa alerta, se sabe exactamente a qué detección se refiere."""
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_FROM):
        app.logger.error("❌ Credenciales Twilio no configuradas")
        return "temporal"
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        if plantilla:
            # Plantilla aprobada por Meta: permite alertar fuera de la ventana de 24 h.
            msg_params = {"from_": TWILIO_WHATSAPP_FROM, "to": numero_destino,
                          "content_sid": plantilla["sid"],
                          "content_variables": json.dumps(plantilla["variables"], ensure_ascii=False)}
        else:
            msg_params = {"from_": TWILIO_WHATSAPP_FROM, "body": texto, "to": numero_destino}
            if media_url:
                msg_params["media_url"] = [media_url]

        message = client.messages.create(**msg_params)
        app.logger.info(f"✅ Mensaje a {numero_destino} - SID: {message.sid}")
        if evento_id:
            with connect() as connection:
                connection.execute(
                    "INSERT OR IGNORE INTO alert_messages(sid,evento_id,destinatario) VALUES(?,?,?)",
                    (message.sid, str(evento_id), numero_destino),
                )
        return "ok"
    except TwilioRestException as e:
        tipo = "permanente" if e.status in ESTADOS_HTTP_PERMANENTES else "temporal"
        app.logger.error(f"❌ Error enviando WhatsApp ({tipo}, código {e.code}): {e.msg}")
        return tipo
    except Exception as e:
        app.logger.error(f"❌ Error enviando WhatsApp (temporal): {e}")
        return "temporal"


def resultado_envio(valor):
    # Compatibilidad: True/False de versiones anteriores equivalen a ok/temporal.
    if valor is True:
        return "ok"
    if valor is False:
        return "temporal"
    return valor


def firma_twilio_valida():
    if not VALIDAR_FIRMA_TWILIO:
        return True
    if not TWILIO_AUTH_TOKEN:
        # Sin token no se puede validar: se permite en local, nunca en Railway.
        return not os.environ.get("RAILWAY_ENVIRONMENT_ID")
    firma = request.headers.get("X-Twilio-Signature", "")
    url = TWILIO_WEBHOOK_URL or request.url
    valida = RequestValidator(TWILIO_AUTH_TOKEN).validate(url, request.form, firma)
    if not valida:
        app.logger.warning(f"🔒 Webhook rechazado: firma de Twilio inválida para {url}")
    return valida

# -----------------------
# Webhook de WhatsApp
# -----------------------
@app.route("/whatsapp", methods=["POST"])
def whatsapp_reply():
    # Solo Twilio puede usar este webhook: sin esto, cualquiera podría enviar "4" y apagar la vigilancia.
    if not firma_twilio_valida():
        abort(403)

    from_number = normalizar_numero(request.values.get("From", ""))
    incoming_msg = request.values.get("Body", "").strip().lower()

    # 🔍 LOG PARA DEBUG
    app.logger.info(f"📱 Webhook | De: {from_number} | Msg: '{incoming_msg}'")

    resp = MessagingResponse()
    msg = resp.message()

    if not numero_autorizado(from_number):
        app.logger.warning(f"🔒 Número no autorizado: {from_number}")
        msg.body("🔒 Este número no está registrado en *ÑAWI APU*.\n_Contacte al coordinador del proyecto._")
        return str(resp)

    usuarios = cargar_json(USUARIOS_FILE)
    estados = cargar_json(ESTADOS_FILE)
    # Sin tildes, mayúsculas ni signos: «Menú», «MENU» y «menu.» son lo mismo.
    texto = respuestas.normalizar(request.values.get("Body", ""))

    # Primer contacto: se registra y recibe una bienvenida junto con la respuesta.
    nuevo = from_number not in usuarios
    if nuevo:
        usuarios[from_number] = {"registrado": True, "fecha_registro": datetime.now(timezone.utc).isoformat()}
        guardar_json(USUARIOS_FILE, usuarios)
    def responder(cuerpo):
        msg.body(cuerpo)
        return str(resp)

    if texto in PALABRAS_MENU or texto.startswith("join "):
        return responder(generar_menu_principal())

    if texto in PALABRAS_AYUDA:
        return responder(generar_ayuda())

    # Pausa. Ojo: no se usa "stop", que en WhatsApp/Twilio da de baja al número.
    if texto in PALABRAS_PAUSA:
        estados[from_number] = {"modo": "detenido", "fecha_cambio": datetime.now(timezone.utc).isoformat()}
        guardar_json(ESTADOS_FILE, estados)
        set_mode("detenido")
        return responder("⏸️ *VIGILANCIA EN PAUSA*\n_Responda 1 o 2 para reanudar._")

    if texto in PALABRAS_ESTADO:
        modo = configured_mode() or estados.get(from_number, {}).get("modo", "detenido")
        return responder(generar_estado(modo))

    seleccion = PALABRAS_MODO.get(texto)
    if seleccion:
        anterior = configured_mode()
        estados[from_number] = {"modo": seleccion, "fecha_cambio": datetime.now(timezone.utc).isoformat()}
        guardar_json(ESTADOS_FILE, estados)
        set_mode(seleccion)
        emoji, nombre, detalle = MODOS[seleccion]
        if anterior == seleccion:
            texto_modo = f"ℹ️ La vigilancia de *{nombre.lower()}* ya está activa."
        else:
            texto_modo = f"✅ *VIGILANCIA ACTIVADA* {emoji}\n{nombre} · _{detalle}_"
        # La cámara se enciende sola; solo se avisa si está sin conexión (excepción).
        if not device_status()["conectado"]:
            texto_modo += "\n⚠️ _Cámara sin conexión: iniciará al reconectarse._"
        return responder(texto_modo)

    # Deshacer el último descarte del guardián (por si el bot entendió mal).
    if texto in PALABRAS_DESHACER:
        with connect() as connection:
            ultimo = connection.execute(
                "SELECT d.id, d.fecha, d.especie FROM alert_deliveries a JOIN detecciones d ON d.id = a.evento_id "
                "WHERE a.destinatario = ? AND d.descartado_por = 'guardian' ORDER BY d.fecha DESC LIMIT 1",
                (from_number,),
            ).fetchone()
            if ultimo:
                connection.execute(
                    "UPDATE detecciones SET estado='nuevo', descartado_por=NULL WHERE id=?", (ultimo["id"],))
        if ultimo:
            return responder(f"↩️ La alerta de las {hora_peru(ultimo['fecha'])} vuelve a contar en los registros.")
        return responder("No hay falsas alarmas recientes para revertir.")

    # 6. Respuesta del guardián tras revisar en terreno: «no había intruso», «la tortuga era una roca»…
    interpretacion = respuestas.interpretar(request.values.get("Body", ""))
    citado = request.values.get("OriginalRepliedMessageSid")
    if interpretacion and interpretacion["descarta"]:
        evento = descartar_alerta(from_number, citado, interpretacion["especie"], interpretacion["hora"])
        if evento:
            que = {"tortugas": "tortuga marina", "gaviotines": "gaviotín"}.get(
                evento["especie"], (evento["tipo_evento"] or "intrusión").split(":", 1)[-1].strip())
            return responder(
                "✅ *Falsa alarma registrada*\n"
                f"Alerta de las {hora_peru(evento['fecha'])} ({que}). Ya no cuenta en los registros.\n"
                "_Si no era esa, escriba «deshacer»._"
            )
        que = {"tortugas": "de tortugas ", "gaviotines": "de gaviotines ", "invasores": "de intrusos "}.get(
            interpretacion["especie"], "")
        return responder(f"No se encontró una alerta {que}reciente para descartar.")

    # Mensaje no entendido: al primer contacto se muestra el menú; luego, una guía breve.
    if nuevo:
        return responder(generar_menu_principal())
    return responder("Opción no reconocida.\n_Escriba «menú» para ver las opciones o «ayuda»._")

# -----------------------
# Endpoint Config (Para Raspberry Pi)
# -----------------------
@app.route("/config", methods=["GET"])
def obtener_configuracion():
    """La Raspberry consulta esto para saber si prender la cámara o dormir"""
    # Sin ALERTA_KEY la ubicación sale aproximada: la exacta solo la recibe el agente de cámara.
    ubicacion = public_location(prototype())
    dashboard_mode = configured_mode()
    if dashboard_mode:
        return jsonify({"mode": dashboard_mode, "ubicacion": ubicacion})
    estados = cargar_json(ESTADOS_FILE)

    # Buscamos si ALGUIEN tiene el sistema activo.
    # (Asumiendo que es 1 robot para todos. Si hay conflicto, gana el último cambio)
    activos = {k: v for k, v in estados.items() if v.get("modo") != "detenido"}

    if activos:
        # Obtenemos el modo del usuario que lo cambió más recientemente
        ultimo = max(activos.items(), key=lambda x: x[1].get("fecha_cambio", ""))
        modo = ultimo[1].get("modo")
    else:
        modo = "detenido" # Si todos están en stop o no hay nadie

    # Log para debug en Railway
    app.logger.info(f"📡 Robot consulta config -> Modo: {modo}")
    return jsonify({"mode": modo, "ubicacion": ubicacion})

# -----------------------
# Endpoint Alerta (Recibe de Raspberry)
# -----------------------
@app.route("/alerta", methods=["POST"])
def recibir_alerta():
    # ... (Mantén tu lógica actual de alerta, está perfecta) ...
    # Solo asegúrate de usar 'generar_telemetria' o textos bonitos si modificas algo aquí.

    # Verificación de seguridad
    authorize()

    try:
        data = request.get_json(force=True)
        especie = data.get("especie", "desconocida")
        cantidad = data.get("cantidad", 1)
        imagen_url = data.get("imagen")
        mensaje_prefix = data.get("mensaje_prefix", "🔔 *DETECCIÓN CONFIRMADA*")
    except:
        return jsonify({"error": "bad request"}), 400

    try:
        save_detection(data)
    except ValueError as error:
        app.logger.warning("Alerta sin registro en el dashboard: %s", error)

    event_id = data.get("evento_id")
    if event_id:
        ahora = datetime.now(timezone.utc)
        with connect() as connection:
            claim = connection.execute(
                "INSERT OR IGNORE INTO alert_runs(evento_id,estado,fecha) VALUES(?, 'procesando', ?)",
                (str(event_id), ahora.isoformat()))
            if not claim.rowcount:
                previous = connection.execute("SELECT * FROM alert_runs WHERE evento_id=?", (str(event_id),)).fetchone()
                # Si el servidor se reinició a mitad de un envío, la alerta quedaría "procesando"
                # para siempre. Pasado un tiempo prudente se considera interrumpida y se retoma.
                trabada = previous["estado"] == "procesando" and (
                    not previous["fecha"]
                    or (ahora - datetime.fromisoformat(previous["fecha"])).total_seconds() > ALERTA_TRABADA_SEGUNDOS
                )
                if previous["estado"] != "pendiente" and not trabada:
                    return jsonify({"status": previous["estado"], "enviados": previous["enviados"], "duplicado": True}), (503 if previous["estado"] == "procesando" else 200)
                connection.execute("UPDATE alert_runs SET estado='procesando',fecha=? WHERE evento_id=?",
                                   (ahora.isoformat(), str(event_id)))

    # Segunda opinión de Gemini antes de avisar: si no ve lo detectado, no se molesta
    # al guardián con una falsa alarma (la detección queda descartada en el dashboard).
    if event_id:
        ia = verificacion.esperar(app, str(event_id))
        if ia and ia["ia_resultado"] == "no_coincide":
            app.logger.info(f"🤖 Gemini descartó {event_id}: {ia['ia_descripcion']}")
            with connect() as connection:
                connection.execute("UPDATE alert_runs SET estado='filtrado_ia',enviados=0 WHERE evento_id=?",
                                   (str(event_id),))
            return jsonify({"status": "filtrado_ia", "enviados": 0, "fallidos": 0, "rechazados": 0,
                            "motivo": ia["ia_descripcion"]}), 200
        if ia and ia["ia_resultado"] == "coincide":
            data = {**data, "ia_descripcion": ia["ia_descripcion"], "ia_clase": ia["ia_clase"]}

    # Solo se alerta a encargados autorizados (si hay lista); los números antiguos se ignoran.
    usuarios = {numero: datos for numero, datos in cargar_json(USUARIOS_FILE).items() if numero_autorizado(numero)}
    texto = generar_alerta(data, titulo=data.get("mensaje_prefix"))

    enviados = 0
    fallidos = 0      # errores temporales: vale la pena reintentar
    rechazados = 0    # errores permanentes: reintentar no sirve
    for numero in usuarios.keys():
        if event_id:
            with connect() as connection:
                accepted = connection.execute("SELECT 1 FROM alert_deliveries WHERE evento_id=? AND destinatario=?", (str(event_id),numero)).fetchone()
                rejected = connection.execute("SELECT 1 FROM alert_rejections WHERE evento_id=? AND destinatario=?", (str(event_id),numero)).fetchone()
            if accepted:
                enviados += 1
                continue
            if rejected:
                rechazados += 1
                continue
        plantilla = (
            {"sid": TWILIO_PLANTILLA_ALERTA, "variables": variables_plantilla(data)}
            if TWILIO_PLANTILLA_ALERTA and event_id else None
        )
        resultado = resultado_envio(enviar_whatsapp(numero, texto, media_url=imagen_url,
                                                    evento_id=event_id, plantilla=plantilla))
        if resultado == "ok":
            enviados += 1
            if event_id:
                with connect() as connection:
                    connection.execute("INSERT OR IGNORE INTO alert_deliveries VALUES(?,?)", (str(event_id),numero))
        elif resultado == "permanente":
            rechazados += 1
            if event_id:
                with connect() as connection:
                    connection.execute("INSERT OR IGNORE INTO alert_rejections VALUES(?,?)", (str(event_id),numero))
        else:
            fallidos += 1

    # Solo los errores temporales hacen que el agente reintente (503).
    # Los permanentes se registran y se responde 200 para no reintentar sin fin.
    if fallidos:
        status = "pendiente"
    elif rechazados:
        status = "rechazado" if not enviados else "parcial"
    else:
        status = "ok"
    if event_id:
        with connect() as connection:
            connection.execute("UPDATE alert_runs SET estado=?,enviados=? WHERE evento_id=?", (status,enviados,str(event_id)))

    return jsonify({"status": status, "enviados": enviados, "fallidos": fallidos, "rechazados": rechazados}), (503 if fallidos else 200)

# -----------------------
# Ejecución
# -----------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

