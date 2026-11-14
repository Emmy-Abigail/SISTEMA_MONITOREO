"""Entiende lo que escribe el guardián tras revisar una alerta en terreno.

Ejemplos: «no», «no había intruso», «la tortuga era una roca»,
«la de las 3:10 no era nada», «falsa alarma del perro».

Primero se usan reglas en español (rápidas y sin internet). Si el mensaje no
es claro, se consulta a Gemini (utils.gemini.interpretar_respuesta).
"""

import re
import unicodedata

from utils import gemini


def normalizar(texto):
    texto = unicodedata.normalize("NFKD", str(texto).lower())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9: ]", " ", texto).split())


# Frases que indican que lo detectado no estaba ahí.
NEGACION = re.compile(
    r"^(no|nop|nones|negativo)$"
    r"|\bno (habia|hay|era|es|fue|estaba|vi|vimos|encontre|encontramos)\b"
    r"|\b(falso|falsa|falsos|falsas|error|equivoco|equivocado|equivocada|nada|ninguno|ninguna|nadie)\b"
    r"|\bera (un|una|solo|solamente|nomas)\b"
    r"|\bsolo (habia|hay|era)\b"
)
ESPECIES = {
    "invasores": re.compile(
        r"\b(intrus\w*|persona\w*|gente|alguien|hombre\w*|mujer\w*|nino\w*|perr\w*|"
        r"gat\w*|vehicul\w*|auto\w*|carro\w*|camion\w*|camioneta\w*|moto\w*|cuatrimoto\w*|"
        r"bote\w*|lancha\w*|embarcacion\w*|amenaza\w*|invasor\w*|nadie)\b"
    ),
    "tortugas": re.compile(r"\btortug\w*\b"),
    "gaviotines": re.compile(r"\b(gaviot\w*|ave|aves|pajar\w*|pichon\w*)\b"),
}
HORA = re.compile(r"\b([01]?\d|2[0-3])[:.h]([0-5]\d)\b")


def interpretar(texto, usar_ia=True):
    """Devuelve {"descarta", "especie", "hora", "fuente"} o None si no es una respuesta de descarte."""
    limpio = normalizar(texto)
    if not limpio:
        return None
    especies = [nombre for nombre, patron in ESPECIES.items() if patron.search(limpio)]
    hora = HORA.search(limpio)
    hora = f"{int(hora.group(1)):02d}:{hora.group(2)}" if hora else None
    if NEGACION.search(limpio):
        return {
            "descarta": True,
            # Si menciona más de una especie no se adivina: se usa la alerta citada o la última.
            "especie": especies[0] if len(especies) == 1 else None,
            "hora": hora,
            "fuente": "reglas",
        }
    if usar_ia:
        resultado = gemini.interpretar_respuesta(texto)
        if resultado and resultado["descarta"]:
            return {**resultado, "fuente": "gemini"}
    return None
