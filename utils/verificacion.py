"""Orquesta la verificación con Gemini de cada detección guardada.

- Al recibir una detección con foto se lanza la verificación en segundo plano.
- Antes de enviar la alerta de WhatsApp se espera su resultado (con un límite):
  si Gemini dice que no aparece lo detectado, la detección se descarta como
  falso positivo y el guardián no recibe una falsa alarma.
- Cada detección se verifica una sola vez (el resultado queda en la base).
"""

import threading

from utils import gemini
from utils.dashboard import connect

_lock = threading.Lock()
_en_curso = {}

# Lo que Gemini puede decir de una amenaza, traducido al texto del evento.
TIPO_AMENAZA = {
    "persona": "Intrusión: persona",
    "perro": "Intrusión: perro",
    "otro animal": "Intrusión: animal",
    "vehículo": "Intrusión: vehículo",
    "embarcación": "Intrusión: embarcación",
}


def _resultado(event_id):
    with connect() as connection:
        row = connection.execute(
            "SELECT ia_resultado, ia_descripcion, ia_clase, estado, tipo_evento "
            "FROM detecciones WHERE id=?",
            (event_id,),
        ).fetchone()
    return dict(row) if row and row["ia_resultado"] else None


def _verificar(event_id):
    with connect() as connection:
        row = connection.execute(
            "SELECT especie, imagen, ia_resultado FROM detecciones WHERE id=?", (event_id,)
        ).fetchone()
    if not row or row["ia_resultado"] or not row["imagen"]:
        return
    analisis = gemini.analizar(row["especie"], bytes(row["imagen"]))
    if analisis is None:
        resultado, descripcion, clase = "error", None, None
    else:
        resultado = "coincide" if analisis["presente"] else "no_coincide"
        descripcion, clase = analisis["descripcion"], analisis.get("clase")
    with connect() as connection:
        connection.execute(
            "UPDATE detecciones SET ia_resultado=?, ia_descripcion=?, ia_clase=? WHERE id=?",
            (resultado, descripcion, clase, event_id),
        )
        if resultado == "no_coincide":
            # Falso positivo según Gemini: no cuenta (el guardián puede deshacerlo).
            connection.execute(
                "UPDATE detecciones SET estado='descartado', descartado_por='ia' "
                "WHERE id=? AND estado='nuevo'",
                (event_id,),
            )
        elif row["especie"] == "invasores" and clase in TIPO_AMENAZA:
            # YOLO pudo confundir la clase; se usa la que vio Gemini.
            connection.execute(
                "UPDATE detecciones SET tipo_evento=? WHERE id=?", (TIPO_AMENAZA[clase], event_id)
            )


def iniciar(app, event_id):
    """Lanza la verificación en segundo plano (si Gemini está configurado)."""
    if not gemini.configurado():
        return None
    with _lock:
        if event_id in _en_curso:
            return _en_curso[event_id]
        terminado = threading.Event()
        _en_curso[event_id] = terminado

    def trabajo():
        try:
            with app.app_context():
                _verificar(event_id)
        except Exception as error:  # nunca debe tumbar el servidor
            print(f"Verificación con Gemini fallida para {event_id}: {error}", flush=True)
        finally:
            terminado.set()
            with _lock:
                _en_curso.pop(event_id, None)

    threading.Thread(target=trabajo, name=f"gemini-{event_id[:8]}", daemon=True).start()
    return terminado


def esperar(app, event_id, timeout=26):
    """Devuelve el resultado de Gemini, esperando como máximo `timeout` segundos."""
    resultado = _resultado(event_id)
    if resultado or not gemini.configurado():
        return resultado
    terminado = iniciar(app, event_id)
    if terminado is not None:
        terminado.wait(timeout)
    return _resultado(event_id)
