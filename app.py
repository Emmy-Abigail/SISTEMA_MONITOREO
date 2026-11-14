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
from datetime import datetime, timezone
from utils.dashboard import initialize, configured_mode, set_mode, save_detection, prototype, connect, authorize, public_location

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
def generar_menu_principal():
    """Genera el menú principal profesional para ÑAWI APU"""
    return """*ÑAWI APU* | _Guardián costero_
─────────────────────
*Bienvenido, Encargado de vigilancia.*

Control de vigilancia costera y alertas de conservación.

*` [ PANEL DE CONTROL ] `*

1️- *VIGILANCIA TORTUGAS* 🐢
   ↳ _Monitoreo de nidos y alertas de eclosión_

2️- *VIGILANCIA GAVIOTINES* 🐦
   ↳ _Monitoreo de nidos y alertas de eclosión_

3️- *AMENAZAS* ⚠️
   ↳ _Detección de intrusos o actividad sospechosa_

4️- *DETENER SISTEMA* 🛑
   ↳ _Modo Standby para ahorro de energía_

5️- *DASHBOARD / ESTADO* 📊
   ↳ _Visualización de métricas y gráficos en tiempo real_
─────────────────────
_Responda con el número correspondiente a su opción._"""

def generar_telemetria(modo_actual):
    """Genera el reporte técnico de la opción 5"""
    uptime = str(datetime.now() - TIEMPO_INICIO).split('.')[0]

    estado_icono = "🟢 ONLINE" if modo_actual != "detenido" else "🔴 STANDBY"

    return f"""📊 *TELEMETRÍA DE ÑAWI APU*
`Estado: {estado_icono}`

⚙️ *SISTEMA*
‣ *Modo:* {modo_actual.upper()}
‣ *Uptime:* {uptime}
‣ *Backend:* Railway Cloud

📡 *ENLACE DE DATOS*
Para ver mapas, gráficas y reportes detallados:
👇 *Accede a nuestro Dashboard:*
{DASHBOARD_URL}"""

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


def enviar_whatsapp(numero_destino, texto, media_url=None):
    """Devuelve "ok", "permanente" (no reintentar) o "temporal" (reintentar más tarde)."""
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_FROM):
        app.logger.error("❌ Credenciales Twilio no configuradas")
        return "temporal"
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        msg_params = {"from_": TWILIO_WHATSAPP_FROM, "body": texto, "to": numero_destino}
        if media_url:
            msg_params["media_url"] = [media_url]

        message = client.messages.create(**msg_params)
        app.logger.info(f"✅ Mensaje a {numero_destino} - SID: {message.sid}")
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
        msg.body("🔒 Este número no está autorizado para usar ÑAWI APU.\n"
                 "Pide al administrador que lo agregue a la lista de encargados.")
        return str(resp)

    usuarios = cargar_json(USUARIOS_FILE)
    estados = cargar_json(ESTADOS_FILE)

    # ---- 1. Registrar si no existe ----
    if incoming_msg in ["menu", "hola", "inicio", "ayuda", "help"]:
        if from_number not in usuarios:
            usuarios[from_number] = {
                "registrado": True,
                "fecha_registro": datetime.now().isoformat()
            }
            guardar_json(USUARIOS_FILE, usuarios)

        msg.body(generar_menu_principal())

        return str(resp)

    # ---- 2. Registro automático general ----

    if from_number not in usuarios:
        usuarios[from_number] = {
            "registrado": True,
            "fecha_registro": datetime.now().isoformat()
        }

        guardar_json(USUARIOS_FILE, usuarios)

        if incoming_msg.startswith("join"):
            msg.body(f"✅ *¡Bienvenido a ÑAWI APU!*\n\n{generar_menu_principal()}")
            return str(resp)

    # 3. Detener (Opción 4)
    if incoming_msg in ["4", "stop", "detener", "apagar"]:
        estados[from_number] = {"modo": "detenido", "fecha_cambio": datetime.now().isoformat()}
        guardar_json(ESTADOS_FILE, estados)
        set_mode("detenido")
        msg.body("🛑 *SISTEMA DETENIDO*\n\nÑawi Apu entra en modo reposo (Standby).\n\n_Escribe *Menu* para reactivar._")
        return str(resp)

    # 4. Estado / Dashboard (Opción 5)
    if incoming_msg in ["5", "estado", "status", "dashboard"]:
        estado_user = configured_mode() or estados.get(from_number, {}).get("modo", "detenido")
        msg.body(generar_telemetria(estado_user))
        return str(resp)

    # 5. Selección de Modos (1, 2, 3)
    especie_map = {
        "1": "tortugas", "tortugas": "tortugas",
        "2": "gaviotines", "gaviotines": "gaviotines",
        "3": "invasores", "amenazas": "invasores"
    }

    seleccion = especie_map.get(incoming_msg)

    if seleccion:
        estados[from_number] = {"modo": seleccion, "fecha_cambio": datetime.now().isoformat()}
        guardar_json(ESTADOS_FILE, estados)
        set_mode(seleccion)

        emojis = {"tortugas": "🐢", "gaviotines": "🐦", "invasores": "⚠️"}
        emoji = emojis.get(seleccion, "👁️")

        texto_confirmacion = (
            f"✅ *MODO {seleccion.upper()} ACTIVADO* {emoji}\n\n"
            f"Orden enviada al monitor local: buscar {seleccion}.\n"
            "La PC debe estar encendida con el agente abierto. Recibirás alertas cuando registre detecciones.\n\n"
            "_Escribe *4* para Pausar o *Menu* para opciones._"
        )
        msg.body(texto_confirmacion)
        return str(resp)

    # 6. Mensaje no entendido
    msg.body("❌ Comando no reconocido.\n_Escribe *Menu* para ver las opciones._")
    return str(resp)

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

    # Solo se alerta a encargados autorizados (si hay lista); los números antiguos se ignoran.
    usuarios = {numero: datos for numero, datos in cargar_json(USUARIOS_FILE).items() if numero_autorizado(numero)}
    texto = (
        f"{mensaje_prefix}\n"
        "─────────────────────\n"
        f"📍 *Especie:* {especie.upper()}\n"
        f"🔢 *Cantidad:* {cantidad}\n"
        f"🕐 *Hora:* {datetime.now().strftime('%H:%M:%S')}\n"
        "─────────────────────"
    )
    if imagen_url: texto += "\n📸 _Evidencia adjunta:_"

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
        resultado = resultado_envio(enviar_whatsapp(numero, texto, media_url=imagen_url))
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

