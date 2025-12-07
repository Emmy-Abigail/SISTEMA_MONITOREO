import os
import json
from flask import Flask, request, jsonify
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
import tempfile

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
TWILIO_WHATSAPP_FROM = os.environ.get("TWILIO_WHATSAPP_FROM")  # ej: whatsapp:+1415xxxxxxx
ALERTA_KEY = os.environ.get("ALERTA_KEY")  # secreto para /alerta

# -----------------------
# Funciones auxiliares
# -----------------------
def cargar_json(ruta):
    try:
        # Si es el archivo de usuarios, SIEMPRE asegurar que tu número esté
        if "usuarios" in ruta:
            # Tu número SIEMPRE registrado
            usuarios_default = {"whatsapp:+51918516679": True}
            
            if os.path.exists(ruta):
                with open(ruta, "r") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        # Combinar usuarios existentes con el default
                        usuarios_default.update(data)
            
            # Guardar con tu número incluido
            with open(ruta, "w") as f:
                json.dump(usuarios_default, f, indent=4)
            
            app.logger.info(f"✅ Usuarios cargados: {list(usuarios_default.keys())}")
            return usuarios_default
        
        # Para otros archivos (estados, etc.)
        if not os.path.exists(ruta):
            with open(ruta, "w") as f:
                json.dump({}, f)
        
        with open(ruta, "r") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
            
    except Exception as e:
        app.logger.warning(f"Error cargando JSON {ruta}: {e}")
        # Si es usuarios y hay error, devolver tu número
        if "usuarios" in ruta:
            return {"whatsapp:+51918516679": True}
        return {}

# -----------------------
# Endpoints
# -----------------------

@app.route("/whatsapp", methods=["POST"])
def whatsapp_reply():
    from_number = request.values.get("From", "")
    incoming_msg = request.values.get("Body", "").strip().lower()

    usuarios = cargar_json(USUARIOS_FILE)
    estados = cargar_json(ESTADOS_FILE)

    resp = MessagingResponse()
    msg = resp.message()

    # Registrar usuario
    if incoming_msg.startswith("join"):
        usuarios[from_number] = True
        guardar_json(USUARIOS_FILE, usuarios)
        msg.body("🟢 *Registro exitoso*\nTu número ha sido registrado y ahora recibirás alertas.")
        return str(resp)

    # Menú principal
    if incoming_msg in ["menu", "hola", "inicio"]:
        texto = (
            "🟢 *Monitoreo de especies*\n\n"
            "¿Qué deseas monitorear hoy?\n"
            "1️⃣ Tortugas 🐢\n"
            "2️⃣ Gaviotines 🐦\n\n"
            "Responde con *1* o *2*."
        )
        msg.body(texto)
        return str(resp)

    # Elección de especie
    if incoming_msg in ["1", "tortugas"]:
        estados[from_number] = "tortugas"
        guardar_json(ESTADOS_FILE, estados)
        msg.body("Has elegido 🐢 *Tortugas*. El sistema iniciará la detección.")
        return str(resp)

    if incoming_msg in ["2", "gaviotines"]:
        estados[from_number] = "gaviotines"
        guardar_json(ESTADOS_FILE, estados)
        msg.body("Has elegido 🐦 *Gaviotines*. El sistema iniciará la detección.")
        return str(resp)

    msg.body("No entendí tu mensaje. Escribe *menu* para ver opciones.")
    return str(resp)

@app.route("/config", methods=["GET"])
def obtener_configuracion():
    """
    La Raspberry Pi consulta este endpoint para saber qué especie monitorear.
    Devuelve {"mode": "tortugas"} o {"mode": "gaviotines"}.
    """
    estados = cargar_json(ESTADOS_FILE)
    mode = list(estados.values())[-1] if estados else "tortugas"
    return jsonify({"mode": mode})

@app.route("/alerta", methods=["POST"])
def recibir_alerta():
    """
    Endpoint usado por detector.py para notificar detecciones.
    """
    if ALERTA_KEY:
        header_key = request.headers.get("X-ALERTA-KEY", "")
        if header_key != ALERTA_KEY:
            app.logger.warning("Intento de acceso a /alerta con llave inválida")
            return jsonify({"error": "Unauthorized"}), 401

    try:
        data = request.get_json(force=True)
        especie = data.get("especie", "tortugas")
        cantidad = data.get("cantidad", 1)
        imagen_url = data.get("imagen", None)
    except Exception as e:
        app.logger.error(f"Error parsing JSON en /alerta: {e}")
        return jsonify({"error": "bad request"}), 400

    usuarios = cargar_json(USUARIOS_FILE)
    if not usuarios:
        app.logger.info("No hay usuarios registrados.")
        return jsonify({"status": "no_users"}), 200

    texto = f"🚨 *DETECCIÓN AUTOMÁTICA*\n\nEspecie: *{especie}*\nCantidad: *{cantidad}*"
    if imagen_url:
        texto += "\n\n📸 Imagen adjunta."

    enviados = []
    fallos = []

    # Enviar mensaje a todos los usuarios
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
            app.logger.info(f"Mensaje enviado a {numero}: {message.sid}")
            enviados.append(numero)
        except Exception as e:
            app.logger.error(f"Error enviando a {numero}: {e}")
            fallos.append(numero)

    return jsonify({"status": "ok", "enviados": enviados, "fallos": fallos}), 200

# -----------------------
# Run
# -----------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
