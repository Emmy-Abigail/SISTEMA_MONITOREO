# Entrega a Abi — ÑAWI APU GOGO

El código del dashboard y del agente está en **Emmy-Abigail/SISTEMA_MONITOREO, rama main**. El rediseño está en `32a8c8a`, con limpieza posterior en `b1eb1e4`. Las credenciales privadas no están en Git.

## Ya comprobado

- 17 pruebas automatizadas aprobadas: registro, autorización, filtros, fechas, migración de SQLite, ubicación histórica, reintentos y control de cámara.
- Tres modelos cargan y ejecutan inferencia: tortugas, gaviotines e invasores (perro, persona y vehículo).
- Cámara física de la PC probada a 640 × 480 y liberada al finalizar.
- Flujo de comandos, detección, registro, alerta y apagado comprobado con cámara y Twilio simulados. Falta comprobarlo con fauna y mensajería reales.

## Pendientes de infraestructura

1. En Railway, conservar el repositorio de Abi y rama main. Desplegar el último commit con **Ctrl+K → Deploy Latest Commit**. La última comprobación del dominio devolvió 404; subir código a GitHub no confirmó un despliegue nuevo.
2. Abrir `https://sistemamonitoreo-production-8883a6d5.up.railway.app/` y comprobar que carga ÑAWI APU GOGO. `/config` debe devolver `mode` y `ubicacion`.
3. Verificar las variables de Railway: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM` y `ALERTA_KEY`. El usuario indicó que las completó; sus valores no fueron verificados en esta sesión.
4. Agregar/verificar un volumen montado en `/data`, con una sola réplica. Sin volumen, el historial puede perderse al desplegar.
5. En el Sandbox de la misma cuenta Twilio, guardar el webhook `https://sistemamonitoreo-production-8883a6d5.up.railway.app/whatsapp`, método POST. Vincular el teléfono con el código y número que muestre ese Sandbox.
6. Revisar capacidad de envío de Twilio: antes mostró saldo de prueba agotado. La entrega real de mensajes sigue sin verificarse.

## Preparar la PC o laptop

Desde una copia del repositorio de Abi:

```powershell
git pull origin main
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
.\.venv\Scripts\python.exe -m pip install -r requirements_pc.txt
.\.venv\Scripts\python.exe -m src.monitor_pc
```

Si el entorno ya existe y tiene esas dependencias, ejecutar solamente el último comando. El agente solicita de forma oculta la **misma ALERTA_KEY de Railway** si falta en el entorno o `.env`. Los tokens de Twilio permanecen en Railway.

Dejar la terminal abierta y la PC encendida con internet. No ejecutar simultáneamente `src.probar_camara` u otra aplicación que use la cámara. No hace falta iniciar Flask local para usar Railway.

## Prueba de aceptación

- [ ] Enviar `menu` desde el WhatsApp inscrito y recibir el menú.
- [ ] Enviar `4`: el agente debe esperar con la cámara apagada.
- [ ] En el dashboard real, pulsar **Fijar ubicación**, seleccionar el punto del prototipo y guardar con ALERTA_KEY.
- [ ] Enviar `1`: debe encenderse la cámara local y cargarse tortugas.
- [ ] Mostrar una escena reconocible: comprobar especie, fecha, cantidad, confianza y ubicación en la tabla; revisar KPI, mapa y gráfico.
- [ ] Comprobar la alerta y su fotografía en WhatsApp. Un envío aceptado por la API no acredita entrega final al teléfono.
- [ ] Enviar `2` y comprobar el cambio a gaviotines; enviar `3` y comprobar invasores.
- [ ] Enviar `4`: debe liberarse físicamente la cámara y el agente debe seguir esperando.
- [ ] Comprobar búsqueda, filtros, rango de fechas, ordenamiento, paginación, detalle, cambio de estado y exportación CSV.
- [ ] Mover la ubicación del prototipo y comprobar que eventos anteriores conservan sus coordenadas.
- [ ] Reiniciar el despliegue y comprobar persistencia del historial y ubicación con el volumen.
- [ ] Revisar escritorio **1440 × 900**, laptop y móvil. Probar Pacífico profundo, Abisal y Reserva marina en Configuración.

## Límites y pendientes conocidos

- La captura visual final de 1440 × 900 y la revisión del renderizado quedaron pendientes: el control del navegador se detuvo al no poder verificar la URL activa.
- El modo demostración está identificado y no guarda datos. Salir de él para probar registros reales.
- La cámara se ejecuta en la PC; el dashboard sólo muestra datos y fotografías de eventos.
- Las especies suman objetos por evento, no individuos únicos. El mismo animal puede aparecer en registros sucesivos.
- El modelo de invasores no clasifica automáticamente contaminación ni residuos; esas amenazas se registran manualmente.
- Si Twilio rechaza un envío, queda pendiente para reintento. Una interrupción del servidor durante un envío puede dejarlo en `procesando` y requiere revisión; no se garantiza entrega exactamente una vez ante ese corte.
- La integración con Raspberry Pi sigue pendiente; esta entrega prepara y prueba el agente para PC.

Guía ampliada: [DASHBOARD.md](DASHBOARD.md). Punto de continuación: [CONTINUAR_EN_LAPTOP.md](CONTINUAR_EN_LAPTOP.md).
