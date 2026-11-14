"""Agrupa las detecciones de la cámara en "episodios", como un sistema de vigilancia real.

- Cuando algo aparece, se observa `confirmar` segundos y se envía la MEJOR foto
  (mayor confianza) con la mayor cantidad vista, en lugar de la primera.
- Mientras siga a la vista no se envía nada más: la misma persona o tortuga
  quieta no genera alertas repetidas ni infla los conteos.
- Si desaparece más de `ausencia` segundos y vuelve, es un episodio nuevo.
- Si sigue a la vista durante `recordatorio` segundos, se envía un recordatorio.

Cada especie (y los intrusos) tiene su propio episodio: un falso positivo de
"tortuga" no bloquea la detección de intrusos.
"""


class Episodios:
    def __init__(self, confirmar=1.5, ausencia=30, recordatorio=600):
        self.confirmar = confirmar
        self.ausencia = ausencia
        self.recordatorio = recordatorio
        self.estado = {}

    def actualizar(self, nombre, ahora, candidato=None):
        """Registra lo visto en un cuadro y devuelve el hallazgo que hay que enviar, o None.

        `candidato`: dict con "confianza", "cantidad" y los datos del evento (foto, etiquetas…),
        o None si en este cuadro no se vio nada de `nombre`."""
        st = self.estado.get(nombre)
        if st and ahora - st["ultimo_visto"] > self.ausencia:
            # Pasó suficiente tiempo sin verlo: el episodio terminó.
            st = self.estado.pop(nombre)
            if not st["enviado"] and st["mejor"]:
                return self._preparar(st)  # apareció y se fue antes de confirmar
            st = None
        if candidato:
            if st is None:
                st = self.estado[nombre] = {
                    "inicio": ahora, "ultimo_visto": ahora, "enviado": False,
                    "ultimo_envio": None, "mejor": None, "max_cantidad": 0,
                }
            st["ultimo_visto"] = ahora
            st["max_cantidad"] = max(st["max_cantidad"], candidato["cantidad"])
            if st["mejor"] is None or candidato["confianza"] > st["mejor"]["confianza"]:
                st["mejor"] = candidato
        if not st:
            return None
        if not st["enviado"] and st["mejor"] and ahora - st["inicio"] >= self.confirmar:
            hallazgo = self._preparar(st)
            st["enviado"], st["ultimo_envio"] = True, ahora
            return hallazgo
        if st["enviado"] and candidato and ahora - st["ultimo_envio"] >= self.recordatorio:
            st["ultimo_envio"] = ahora
            return {**candidato, "recordatorio": True}
        return None

    @staticmethod
    def _preparar(st):
        hallazgo = {**st["mejor"], "cantidad": max(st["max_cantidad"], st["mejor"]["cantidad"])}
        st["mejor"], st["max_cantidad"] = None, 0
        return hallazgo
