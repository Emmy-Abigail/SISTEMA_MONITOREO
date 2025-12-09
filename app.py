import os
import json
import time
from flask import Flask, request, jsonify
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from datetime import datetime

app = Flask(__name__)

# -----------------------
# Configuración
# -----------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# NOTA PARA RAILWAY: Los archivos JSON se borran cada vez que redepsliegas.
# Para producción real, deberías usar una base de datos (Postgres/Redis).
# Para prototipo, esto funciona bien.
USUARIOS_FILE = os.path.join(DATA_DIR, "usuarios.json")
ESTADOS_FILE = os.path.join(DATA_DIR, "estados.json")

# Crear archivos si no existen
for f in [USUARIOS_FILE, ESTADOS_FILE]:
    if not os.path.exists(f):
        with open(f, "w") as file:
            json.dump({}, file)

# Variables de entorno
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_FROM = os.environ.get("TWILIO_WHATSAPP_FROM")
ALERTA_KEY = os.environ.get("ALERTA_KEY", "tu_clave_secreta_123")
DASHBOARD_URL = "https://tu-grafana-o-web.railway.app" # <--- PON TU LINK AQUÍ

# Guardamos el tiempo de inicio para calcular el Uptime
TIEMPO_INICIO = datetime.now()

# -----------------------
# Funciones de Diseño (UI de Texto)
# -----------------------
def generar_menu_principal():
    """Genera el diseño visual PRO para el menú"""
    return """*ÑAWI APU* | _Guardián Costero_
─────────────────────
👋 *Hola, Ranger.*
El sistema de visión artificial está listo.

*` [ PANEL DE CONTROL ] `*

1️⃣ *TORTUGAS* 🐢
   ↳ _Vigilancia de nidos_

2️⃣ *GAVIOTINES* 🐦
   ↳ _Censo de aves_

3️⃣ *AMENAZAS* ⚠️
   ↳ _Intrusos en zona_

4️⃣ *DETENER* 🛑
   ↳ _Modo Standby / Ahorro_

5️⃣ *DASHBOARD / ESTADO* 📊
   ↳ _Ver gráficos en vivo_
─────────────────────
_Responde con el número de tu opción._"""

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
Para ver mapas de calor y reportes detallados:
👇 *Accede al Centro de Comando:*
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
    try:
        with open(ruta, "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        app.logger.error(f"Error guardando JSON {ruta}: {e}")

def enviar_whatsapp(numero_destino, texto, media_url=None):
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_FROM):
        app.logger.error("❌ Credenciales Twilio no configuradas")
        return False
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        msg_params = {"from_": TWILIO_WHATSAPP_FROM, "body": texto, "to": numero_destino}
        if media_url:
            msg_params["media_url"] = [media_url]
        
        message = client.messages.create(**msg_params)
        app.logger.info(f"✅ Mensaje a {numero_destino} - SID: {message.sid}")
        return True
    except Exception as e:
        app.logger.error(f"❌ Error enviando WhatsApp: {e}")
        return False

# -----------------------
# Webhook de WhatsApp
# -----------------------
@app.route("/whatsapp", methods=["POST"])
def whatsapp_reply():
    from_number = request.values.get("From", "").strip()
    incoming_msg = request.values.get("Body", "").strip().lower()

    usuarios = cargar_json(USUARIOS_FILE)
    estados = cargar_json(ESTADOS_FILE)
    resp = MessagingResponse()
    msg = resp.message()

    # --- REGISTRO DE USUARIO NUEVO ---
    if from_number not in usuarios:
        usuarios[from_number] = {"registrado": True, "fecha_registro": datetime.now().isoformat()}
        guardar_json(USUARIOS_FILE, usuarios)
        # Si es nuevo, forzamos el menú de bienvenida
        msg.body(generar_menu_principal())
        return str(resp)

    # --- LÓGICA DE COMANDOS ---
    
    # 1. Menú Principal
    if incoming_msg in ["menu", "hola", "inicio", "0", "start"]:
        msg.body(generar_menu_principal())
        return str(resp)

    # 2. Detener (Opción 4)
    if incoming_msg in ["4", "stop", "detener", "apagar"]:
        estados[from_number] = {"modo": "detenido", "fecha_cambio": datetime.now().isoformat()}
        guardar_json(ESTADOS_FILE, estados)
        
        # Respuesta limpia sin repetir menú gigante
        msg.body("🛑 *SISTEMA DETENIDO*\n\nÑawi Apu entra en modo reposo (Standby).\n\n_Escribe *Menu* para reactivar._")
        return str(resp)

    # 3. Estado / Dashboard (Opción 5)
    if incoming_msg in ["5", "estado", "status", "dashboard"]:
        estado_user = estados.get(from_number, {}).get("modo", "detenido")
        msg.body(generar_telemetria(estado_user))
        return str(resp)

    # 4. Selección de Modos (1, 2, 3)
    especie_map = {
        "1": "tortugas", "tortugas": "tortugas",
        "2": "gaviotines", "gaviotines": "gaviotines",
        "3": "invasores", "amenazas": "invasores"
    }
    
    seleccion = especie_map.get(incoming_msg)

    if seleccion:
        estados[from_number] = {"modo": seleccion, "fecha_cambio": datetime.now().isoformat()}
        guardar_json(ESTADOS_FILE, estados)

        emojis = {"tortugas": "🐢", "gaviotines": "🐦", "invasores": "⚠️"}
        emoji = emojis.get(seleccion, "👁️")

        # Respuesta de confirmación profesional
        texto_confirmacion = (
            f"✅ *MODO {seleccion.upper()} ACTIVADO* {emoji}\n\n"
            f"El algoritmo de visión está buscando {seleccion}.\n"
            "Te notificaré inmediatamente si detecto algo.\n\n"
            "_Escribe *4* para Pausar o *Menu* para opciones._"
        )
        msg.body(texto_confirmacion)
        return str(resp)

    # 5. Mensaje no entendido
    msg.body("❌ Comando no reconocido.\n_Escribe *Menu* para ver las opciones._")
    return str(resp)

# -----------------------
# Endpoint Config (Para Raspberry Pi)
# -----------------------
@app.route("/config", methods=["GET"])
def obtener_configuracion():
    """La Raspberry consulta esto para saber si prender la cámara o dormir"""
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
    return jsonify({"mode": modo})

# -----------------------
# Endpoint Alerta (Recibe de Raspberry)
# -----------------------
@app.route("/alerta", methods=["POST"])
def recibir_alerta():
    # ... (Mantén tu lógica actual de alerta, está perfecta) ...
    # Solo asegúrate de usar 'generar_telemetria' o textos bonitos si modificas algo aquí.
    
    # Verificación de seguridad
    if request.headers.get("X-ALERTA-KEY") != ALERTA_KEY:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        data = request.get_json(force=True)
        especie = data.get("especie", "desconocida")
        cantidad = data.get("cantidad", 1)
        imagen_url = data.get("imagen")
        mensaje_prefix = data.get("mensaje_prefix", "🔔 *DETECCIÓN CONFIRMADA*")
    except:
        return jsonify({"error": "bad request"}), 400

    usuarios = cargar_json(USUARIOS_FILE)
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
    for numero in usuarios.keys():
        if enviar_whatsapp(numero, texto, media_url=imagen_url):
            enviados += 1

    return jsonify({"status": "ok", "enviados": enviados}), 200

# -----------------------
# Ejecución
# -----------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

