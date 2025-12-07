import os
import json
from flask import Flask, request, jsonify
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client

app = Flask(__name__)

# -----------------------
# Carpeta persistente
# -----------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

USUARIOS_FILE = os.path.join(DATA_DIR, "usuarios.json")
ESTADOS_FILE = os.path.join(DATA_DIR, "estados.json")

# Crear archivos vacíos si no existen
for f in [USUARIOS_FILE, ESTADOS_FILE]:
    if not os.path.exists(f):
        with open(f, "w") as file:
            json.dump({}, file)

# -----------------------
# Variables de entorno
# -----------------------
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_FROM = os.environ.get("TWILIO_WHATSAPP_FROM")  # ej: whatsapp:+14155238886
ALERTA_KEY = os.environ.get("ALERTA_KEY", "tu_clave_secreta_123")  # secreto para /alerta

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

def enviar_whatsapp(numero_destino, texto, imagen_url=None):
    """
    Envía WhatsApp usando Twilio REST API
    """
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_FROM):
        app.logger.error("Credenciales Twilio no configuradas")
        return False
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        msg_params = {
            "from_": TWILIO_WHATSAPP_FROM,
            "body": texto,
            "to": numero_destino
        }
        if imagen_url:
            msg_params["media_url"] = [imagen_url]
            
        message = client.messages.create(**msg_params)
        app.logger.info(f"Mensaje Twilio SID: {message.sid} enviado a {numero_destino}")
        return True
    except Exception as e:
        app.logger.error(f"Error enviando WhatsApp: {e}")
        return False

# -----------------------
# Endpoints
# -----------------------

@app.route("/", methods=["GET"])
def home():
    """Endpoint raíz para verificar que el servidor está corriendo"""
    return jsonify({
        "status": "online",
        "service": "Sistema de Monitoreo Wildlife",
        "endpoints": ["/whatsapp", "/config", "/alerta", "/api/usuarios", "/debug"]
    }), 200

@app.route("/debug", methods=["GET"])
def debug():
    """Endpoint de debug para verificar configuración"""
    return jsonify({
        "twilio_sid_configured": bool(TWILIO_ACCOUNT_SID),
        "twilio_token_configured": bool(TWILIO_AUTH_TOKEN),
        "twilio_from_configured": bool(TWILIO_WHATSAPP_FROM),
        "twilio_from_value": TWILIO_WHATSAPP_FROM if TWILIO_WHATSAPP_FROM else "NOT SET",
        "alerta_key_configured": bool(ALERTA_KEY),
        "usuarios_file": USUARIOS_FILE,
        "estados_file": ESTADOS_FILE
    })

@app.route("/whatsapp", methods=["POST"])
def whatsapp_reply():
    """Maneja mensajes entrantes de WhatsApp vía Twilio"""
    from_number = request.values.get("From", "")
    incoming_msg = request.values.get("Body", "").strip().lower()
    
    # 🔍 DEBUG: Logs para ver qué llega
    app.logger.info(f"📱 Mensaje recibido de WhatsApp")
    app.logger.info(f"   De: {from_number}")
    app.logger.info(f"   Mensaje: '{incoming_msg}'")
    
    usuarios = cargar_json(USUARIOS_FILE)
    estados = cargar_json(ESTADOS_FILE)
    
    app.logger.info(f"   Usuarios registrados actualmente: {len(usuarios)}")
    
    resp = MessagingResponse()
    msg = resp.message()

    # Registrar usuario
    if incoming_msg.startswith("join") or incoming_msg == "registrar":
        usuarios[from_number] = True
        guardar_json(USUARIOS_FILE, usuarios)
        app.logger.info(f"✅ Usuario {from_number} registrado exitosamente")
        msg.body("🟢 *Registro exitoso*\n\nTu número ha sido registrado y ahora recibirás alertas de detección.\n\nEscribe *menu* para ver opciones.")
        return str(resp)

    # Salir/Desregistrar
    if incoming_msg == "salir" or incoming_msg == "stop":
        if from_number in usuarios:
            del usuarios[from_number]
            guardar_json(USUARIOS_FILE, usuarios)
            app.logger.info(f"❌ Usuario {from_number} desregistrado")
            msg.body("👋 Has sido removido de las alertas.\n\nEscribe *join* para volver a registrarte.")
        else:
            msg.body("No estabas registrado.")
        return str(resp)

    # Menú principal
    if incoming_msg in ["menu", "hola", "inicio", "help"]:
        texto = (
            "🟢 *Monitoreo de Especies*\n\n"
            "¿Qué deseas monitorear?\n\n"
            "1️⃣ Tortugas 🐢\n"
            "2️⃣ Gaviotines 🐦\n\n"
            "Responde con *1*, *2*, *tortugas* o *gaviotines*.\n\n"
            "Otros comandos:\n"
            "• *salir* - Dejar de recibir alertas\n"
            "• *estado* - Ver modo actual"
        )
        msg.body(texto)
        return str(resp)

    # Ver estado actual
    if incoming_msg == "estado" or incoming_msg == "status":
        mode = list(estados.values())[-1] if estados else "tortugas"
        registrado = "Sí" if from_number in usuarios else "No"
        texto = f"📊 *Estado actual*\n\nModo: *{mode}*\nRegistrado: *{registrado}*"
        msg.body(texto)
        return str(resp)

    # Elección de especie
    if incoming_msg in ["1", "tortugas"]:
        estados[from_number] = "tortugas"
        guardar_json(ESTADOS_FILE, estados)
        app.logger.info(f"🐢 Usuario {from_number} eligió tortugas")
        msg.body("🐢 *Tortugas seleccionadas*\n\nEl sistema iniciará la detección de tortugas.")
        return str(resp)

    if incoming_msg in ["2", "gaviotines"]:
        estados[from_number] = "gaviotines"
        guardar_json(ESTADOS_FILE, estados)
        app.logger.info(f"🐦 Usuario {from_number} eligió gaviotines")
        msg.body("🐦 *Gaviotines seleccionados*\n\nEl sistema iniciará la detección de gaviotines.")
        return str(resp)

    # Mensaje no reconocido
    app.logger.warning(f"⚠️ Mensaje no reconocido: '{incoming_msg}'")
    msg.body("❓ No entendí tu mensaje.\n\nEscribe *menu* para ver las opciones disponibles.")
    return str(resp)

@app.route("/config", methods=["GET"])
def obtener_configuracion():
    """
    La Raspberry Pi consulta este endpoint para saber qué especie monitorear.
    Devuelve {"mode": "tortugas"} o {"mode": "gaviotines"}.
    """
    estados = cargar_json(ESTADOS_FILE)
    mode = list(estados.values())[-1] if estados else "tortugas"
    app.logger.info(f"📡 Raspberry Pi consultó config, modo: {mode}")
    return jsonify({"mode": mode})

@app.route("/alerta", methods=["POST"])
def recibir_alerta():
    """
    Endpoint usado por detector.py para notificar detecciones.
    """
    # Validar clave de acceso
    if ALERTA_KEY:
        header_key = request.headers.get("X-ALERTA-KEY", "")
        if header_key != ALERTA_KEY:
            app.logger.warning(f"⚠️ Intento de acceso a /alerta con llave inválida: {header_key}")
            return jsonify({"error": "Unauthorized"}), 401

    try:
        data = request.get_json(force=True)
        especie = data.get("especie", "desconocido")
        cantidad = data.get("cantidad", 1)
        imagen_url = data.get("imagen", None)
    except Exception as e:
        app.logger.error(f"❌ Error parsing JSON en /alerta: {e}")
        return jsonify({"error": "bad request"}), 400

    app.logger.info(f"🚨 Alerta recibida: {especie} x{cantidad}")

    usuarios = cargar_json(USUARIOS_FILE)
    if not usuarios:
        app.logger.info("⚠️ No hay usuarios registrados para enviar alertas")
        return jsonify({"status": "no_users"}), 200

    # Crear mensaje personalizado
    texto = f"🚨 *DETECCIÓN AUTOMÁTICA*\n\nEspecie: *{especie}*\nCantidad: *{cantidad}*"
    if imagen_url:
        texto += "\n\n📸 Imagen de la detección adjunta."

    enviados = []
    fallos = []

    # Enviar mensaje a todos los usuarios registrados
    for numero in usuarios.keys():
        try:
            client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
            msg_params = {
                "from_": TWILIO_WHATSAPP_FROM,
                "body": texto,
                "to": numero
            }
            if imagen_url:
                msg_params["media_url"] = [imagen_url]
            
            message = client.messages.create(**msg_params)
            app.logger.info(f"✅ Mensaje enviado a {numero}: {message.sid}")
            enviados.append(numero)
        except Exception as e:
            app.logger.error(f"❌ Error enviando a {numero}: {e}")
            fallos.append(numero)

    return jsonify({
        "status": "ok",
        "enviados": len(enviados),
        "fallos": len(fallos),
        "usuarios_notificados": enviados
    }), 200

@app.route("/api/usuarios", methods=["GET"])
def listar_usuarios():
    """Lista todos los usuarios registrados"""
    usuarios = cargar_json(USUARIOS_FILE)
    return jsonify({
        "total": len(usuarios),
        "usuarios": list(usuarios.keys())
    }), 200

@app.route("/api/estados", methods=["GET"])
def listar_estados():
    """Lista los estados/preferencias de usuarios"""
    estados = cargar_json(ESTADOS_FILE)
    return jsonify({
        "total": len(estados),
        "estados": estados
    }), 200

# -----------------------
# Run
# -----------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.logger.info(f"🚀 Iniciando servidor en puerto {port}")
    app.run(host="0.0.0.0", port=port)





