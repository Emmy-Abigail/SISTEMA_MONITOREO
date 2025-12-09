import os

import json

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

    """Envía WhatsApp usando Twilio REST API"""

    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_FROM):

        app.logger.error("❌ Credenciales Twilio no configuradas")

        return False

   

    try:

        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

        msg_params = {

            "from_": TWILIO_WHATSAPP_FROM,

            "body": texto,

            "to": numero_destino

        }

        if media_url:

            msg_params["media_url"] = [media_url]

       

        message = client.messages.create(**msg_params)

        app.logger.info(f"✅ Mensaje enviado a {numero_destino} - SID: {message.sid}")

        return True

    except Exception as e:

        app.logger.error(f"❌ Error enviando WhatsApp: {e}")

        return False



def obtener_menu():

    """Retorna el menú de opciones"""

    return """🦅 *SISTEMA DE MONITOREO DE VIDA SILVESTRE* 🐢



Selecciona qué deseas monitorear:



1️⃣ Tortugas marinas 🐢

2️⃣ Gaviotines 🐦

3️⃣ Amenazas/Invasores ⚠️

4️⃣ Detener monitoreo 🛑

5️⃣ Estado actual 📊



Responde con el número de tu opción o escribe *menu* para ver este mensaje nuevamente."""



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



    # ---- 1. Comando: menu / hola / ayuda (debe registrar si no existe) ----

    if incoming_msg in ["menu", "hola", "inicio", "ayuda", "help"]:

        if from_number not in usuarios:

            usuarios[from_number] = {

                "registrado": True,

                "fecha_registro": datetime.now().isoformat()

            }

            guardar_json(USUARIOS_FILE, usuarios)



        msg.body(obtener_menu())

        return str(resp)



    # ---- 2. Registro automático general ----

    if from_number not in usuarios:

        usuarios[from_number] = {

            "registrado": True,

            "fecha_registro": datetime.now().isoformat()

        }

        guardar_json(USUARIOS_FILE, usuarios)



        msg.body(f"✅ *¡Bienvenido al sistema de monitoreo!*\n\n{obtener_menu()}")

        return str(resp)



    # ---- 3. Comando: detener ----

    if incoming_msg in ["4", "stop", "detener", "salir"]:

        estado_actual = estados.get(from_number, {}).get("modo")

       

        if estado_actual and estado_actual != "detenido":

            estados[from_number] = {

                "modo": "detenido",

                "fecha_cambio": datetime.now().isoformat()

            }

            guardar_json(ESTADOS_FILE, estados)

            msg.body(f"🛑 *Monitoreo de {estado_actual} detenido correctamente.*\n\n{obtener_menu()}")

        else:

            msg.body(f"No hay monitoreo activo.\n\n{obtener_menu()}")

        return str(resp)



    # ---- 4. Comando: estado ----

    if incoming_msg in ["5", "estado"]:

        estado = estados.get(from_number, {})

        modo_actual = estado.get("modo", "ninguno")

       

        if modo_actual and modo_actual != "detenido":

            fecha = estado.get("fecha_cambio", "desconocida")

            msg.body(f"📊 *Estado actual*\n\n"

                     f"Monitoreando: *{modo_actual.upper()}*\n"

                     f"Desde: {fecha}\n\n"

                     f"Envía *4* para detener.")

        else:

            msg.body(f"No hay monitoreo activo.\n\n{obtener_menu()}")

        return str(resp)



    # ---- 5. Mapeo de opciones ----

    especie_map = {

        "1": "tortugas",

        "tortugas": "tortugas",

        "tortuga": "tortugas",

        "2": "gaviotines",

        "gaviotines": "gaviotines",

        "gaviotin": "gaviotines",

        "3": "invasores",

        "invasores": "invasores",

        "amenazas": "invasores",

        "amenaza": "invasores"

    }



    especie_elegida = especie_map.get(incoming_msg)



    if especie_elegida:

        estados[from_number] = {

            "modo": especie_elegida,

            "fecha_cambio": datetime.now().isoformat()

        }

        guardar_json(ESTADOS_FILE, estados)



        emojis = {

            "tortugas": "🐢",

            "gaviotines": "🐦",

            "invasores": "⚠️"

        }

        emoji = emojis.get(especie_elegida, "📊")



        msg.body(f"{emoji} *Monitoreo de {especie_elegida.upper()} iniciado*\n\n"

                 f"Recibirás alertas automáticas cuando se detecten {especie_elegida}.\n\n"

                 f"Envía *4* o *detener* para parar el monitoreo.")

        return str(resp)



    # ---- 6. Mensaje no reconocido ----

    msg.body(f"❌ No entendí tu mensaje.\n\n{obtener_menu()}")

    return str(resp)



# -----------------------

# Endpoint de configuración

# -----------------------

@app.route("/config", methods=["GET"])

def obtener_configuracion():

    """

    La Raspberry Pi consulta este endpoint para saber qué especie monitorear.

    Retorna la última especie elegida por cualquier usuario activo.

    """

    estados = cargar_json(ESTADOS_FILE)

   

    # Filtrar solo estados activos (no detenidos)

    activos = {k: v for k, v in estados.items()

               if v.get("modo") != "detenido"}

   

    if activos:

        # Obtener el último estado activo

        ultimo_usuario = max(activos.items(),

                           key=lambda x: x[1].get("fecha_cambio", ""))

        modo = ultimo_usuario[1].get("modo", "detenido")

    else:

        modo = "detenido"

   

    app.logger.info(f"📡 Config solicitada. Modo actual: {modo}")

    return jsonify({"mode": modo})



# -----------------------

# Endpoint de alerta

# -----------------------

@app.route("/alerta", methods=["POST"])

def recibir_alerta():

    """

    Endpoint usado por detector.py (Raspberry Pi) para notificar detecciones.

    Envía WhatsApp a todos los usuarios registrados.

    """

    # Verificar clave de seguridad

    header_key = request.headers.get("X-ALERTA-KEY", "")

    if header_key != ALERTA_KEY:

        app.logger.warning("⚠️ Intento de acceso a /alerta con llave inválida")

        return jsonify({"error": "Unauthorized"}), 401



    try:

        data = request.get_json(force=True)

        especie = data.get("especie", "desconocida")

        cantidad = data.get("cantidad", 1)

        imagen_url = data.get("imagen")

        tipo = data.get("tipo", "deteccion")

        mensaje_prefix = data.get("mensaje_prefix", "🔔 Detección")

    except Exception as e:

        app.logger.error(f"❌ Error parsing JSON en /alerta: {e}")

        return jsonify({"error": "bad request"}), 400



    # Cargar usuarios registrados

    usuarios = cargar_json(USUARIOS_FILE)

    if not usuarios:

        app.logger.info("⚠️ No hay usuarios registrados.")

        return jsonify({"status": "no_users"}), 200



    # Construir mensaje

    fecha_hora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    texto = f"{mensaje_prefix}\n\n"

    texto += f"📍 Especie: *{especie.upper()}*\n"

    texto += f"🔢 Cantidad: *{cantidad}*\n"

    texto += f"🕐 Fecha: {fecha_hora}\n"

   

    if imagen_url:

        texto += "\n📸 Imagen adjunta a continuación."



    # Enviar a todos los usuarios

    enviados = []

    fallos = []



    for numero in usuarios.keys():

        if enviar_whatsapp(numero, texto, media_url=imagen_url):

            enviados.append(numero)

        else:

            fallos.append(numero)



    app.logger.info(f"✅ Alertas enviadas: {len(enviados)} exitosas, {len(fallos)} fallidas")

   

    return jsonify({

        "status": "ok",

        "enviados": len(enviados),

        "fallos": len(fallos)

    }), 200



# -----------------------

# Endpoint de salud

# -----------------------

@app.route("/health", methods=["GET"])

def health():

    """Health check para Railway"""

    estados = cargar_json(ESTADOS_FILE)

    usuarios = cargar_json(USUARIOS_FILE)

   

    activos = sum(1 for v in estados.values()

                  if v.get("modo") != "detenido")

   

    return jsonify({

        "status": "ok",

        "usuarios_registrados": len(usuarios),

        "monitoreos_activos": activos,

        "timestamp": datetime.now().isoformat()

    })



@app.route("/", methods=["GET"])

def index():

    """Página de inicio"""

    return """

    <h1>🦅 Sistema de Monitoreo de Vida Silvestre 🐢</h1>

    <p>Sistema activo y funcionando correctamente.</p>

    <ul>

        <li><a href="/health">Ver estado del sistema</a></li>

    </ul>

    """



# -----------------------

# Ejecución

# -----------------------

if __name__ == "__main__":

    port = int(os.environ.get("PORT", 5000))

    app.run(host="0.0.0.0", port=port, debug=False)