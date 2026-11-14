# ÑAWI APU — Dashboard y monitoreo desde WhatsApp

## Qué está implementado

Página propia en Flask, disponible en `/` y `/dashboard`. La fuente de Railway continúa siendo **Emmy-Abigail/SISTEMA_MONITOREO, rama main**.

El dashboard muestra KPI, mapa de Lima/Callao, gráfico temporal, amenazas, tarjetas de especies y tabla con búsqueda, filtros, ordenamiento, paginación, detalle y exportación CSV. No solicita acceso a la cámara del navegador. La evidencia fotográfica se consulta dentro del detalle de cada detección.

Las tres variantes funcionan sobre los mismos componentes y datos:

| Variante | Dirección visual | Vista de demostración |
|---|---|---|
| Pacífico profundo, predeterminada | Azul marino, iluminación turquesa, imágenes submarinas sutiles | `/?theme=pacifico&demo=1` |
| Abisal | Azul nocturno, celeste glaciar, paneles más sobrios | `/?theme=abisal&demo=1` |
| Reserva marina | Verde oceánico, acentos arena, superficies más suaves | `/?theme=reserva&demo=1` |

Se cambian en Configuración. La maqueta está preparada para escritorio 1440 × 900 y tiene adaptaciones para laptop, tablet y móvil. La revisión visual automatizada quedó pendiente porque el control de navegador se detuvo al no poder verificar la URL activa. No se ha generado una captura del frontend renderizado.

El modo demostración lleva un aviso visible y no escribe datos en el servidor. Las imágenes decorativas de tortuga y gaviotín son generadas; no son evidencias del detector. Los datos reales empiezan vacíos. Los porcentajes comparativos aparecen únicamente si existe un periodo anterior con valor mayor que cero.

## Flujo que debes dejar abierto en la PC

```text
Tu WhatsApp → Twilio → /whatsapp en Railway → modo guardado
PC consulta /config → abre cámara local → ejecuta modelo
Detección → cola local → Railway → dashboard + alerta de WhatsApp
Comando 4 → modo detenido → PC libera físicamente la cámara
```

Railway permanece disponible sin ejecutar Flask en la PC. Lo que dejas abierto en Visual Studio Code es el agente local:

```powershell
.\.venv\Scripts\python.exe -m src.monitor_pc
```

Lee la URL configurada en RAILWAY_URL o utiliza el dominio del proyecto. Lee ALERTA_KEY del entorno o de `.env`; si falta, la solicita de forma oculta. Debe ser exactamente la misma clave que configuraste en Railway. No necesitas los tokens de Twilio en la laptop: permanecen en Railway.

| Mensaje al bot | Resultado |
|---|---|
| `menu` | Muestra las opciones; no inicia inferencia |
| `1` | Activa la cámara local y el modelo tortugas |
| `2` | Activa/cambia al modelo gaviotines |
| `3` | Activa/cambia al modelo invasores |
| `4` | Detiene la inferencia y libera la cámara |
| `5` | Devuelve el estado y enlace del dashboard |

La selección es global para este único prototipo: se aplica la última orden recibida. El agente consulta cada dos segundos; si pasan doce segundos sin configuración válida o falla la autorización, deja la cámara en reposo. Mientras espera, no abre la cámara. Al terminar con Ctrl+C o Q/Esc en la ventana local también se libera el dispositivo. El agente tiene que seguir abierto y la PC debe permanecer encendida con internet.

El mensaje del bot confirma la recepción de la orden. La confirmación de cámara activa aparece en la terminal y en el estado del dashboard después del siguiente latido del dispositivo. Si se seleccionó un modo antes de ejecutar el agente, arrancará con esa selección.

La inferencia utiliza CPU, imágenes de 416 px y umbral de confianza 0.60 por defecto. Puedes ajustar `--conf 0.75`, `--imgsz 416`, `--camera 0` y `--interval 20`. No ejecutes simultáneamente el agente y la prueba independiente de cámara.

Se registra un evento como máximo cada veinte segundos por modo mientras haya detecciones. Los KPI de especies suman objetos reportados; **no representan individuos únicos**. Un mismo animal puede aparecer en eventos sucesivos. El modelo de invasores reconoce perro, persona y vehículo; no clasifica automáticamente contaminación, plásticos o extracción de nidos. Estas otras amenazas se pueden registrar como observaciones manuales en el dashboard.

## Preparar otra laptop

```powershell
git clone https://github.com/Emmy-Abigail/SISTEMA_MONITOREO.git
cd SISTEMA_MONITOREO
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
.\.venv\Scripts\python.exe -m pip install -r requirements_pc.txt
.\.venv\Scripts\python.exe -m src.monitor_pc
```

En una copia existente del repositorio correcto, usa `git pull`. El archivo `.env` es privado y está excluido de Git. Puedes copiar `.env.pc.example` como `.env` e introducir solamente la clave real. La ruta alternativa `src.probar_camara` sirve para comprobar el modelo local sin enviar registros ni mensajes.

## Fijar la ubicación del prototipo

1. Abre el dashboard real, sin el aviso de demostración.
2. Pulsa **Fijar ubicación** y haz clic en el lugar del mapa.
3. Escribe un nombre, confirma coordenadas y elige Ubicación del prototipo.
4. Guarda con la clave de control ALERTA_KEY cuando se solicite.

El marcador queda guardado en el servidor y acompaña las próximas detecciones. Puedes actualizarlo cuando muevas el prototipo; las detecciones anteriores conservan la ubicación histórica. No representa seguimiento GPS. El selector Nido observado permite guardar marcadores adicionales independientes. Si no fijaste ninguna ubicación, las detecciones quedan explícitamente sin ubicación.

## Configuración de Railway y Twilio

En el servicio SISTEMA_MONITOREO, conservar:

- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_WHATSAPP_FROM=whatsapp:+14155238886` si sigue siendo el remitente mostrado por tu Sandbox.
- `ALERTA_KEY`, tu clave privada de control.
- Opcional `DASHBOARD_URL=https://sistemamonitoreo-production-8883a6d5.up.railway.app/`.

Publicar el último commit de main del repositorio de Abi. Si no hay despliegue automático: abrir el servicio en Railway → Ctrl+K → **Deploy Latest Commit**. La acción Redeploy puede reconstruir una revisión anterior; comprobar que el despliegue corresponde al último commit subido.

En Twilio → Messaging → Try it out → Send a WhatsApp message → Sandbox settings:
`When a message comes in` =
`https://sistemamonitoreo-production-8883a6d5.up.railway.app/whatsapp`, método POST, Save.

Enviar el código de unión que muestre ese Sandbox **al número de ese mismo Sandbox**. La pantalla Tryout de la nueva consola puede usar otro remitente. Usar la misma cuenta Twilio para el Sandbox y las credenciales de Railway.

Agregar un volumen al servicio Railway montado en `/data`; usar una sola réplica. Railway proporciona RAILWAY_VOLUME_MOUNT_PATH automáticamente. Allí quedan SQLite, usuarios registrados y estados. Sin volumen, Railway puede perder datos al volver a desplegar y el dashboard muestra Historial temporal. En local se usa `.runtime/`; MONITOREO_DATA_DIR permite elegir otra ruta.

## Entrega de registros y alertas

El agente guarda eventos pendientes en SQLite local antes de enviarlos. La cola sobrevive al cierre del programa y está separada por URL de backend. Los reintentos mantienen el UUID para no duplicar la detección. Los envíos de red se ejecutan en segundo plano para no bloquear la inferencia.

Primero se confirma el registro en `/api/detecciones`; después se solicita `/alerta`. Por defecto las alertas están activadas; `--sin-whatsapp` permite probar sólo el dashboard. El servidor recuerda los destinatarios aceptados por Twilio y reintenta los fallidos. La aceptación por la API no acredita entrega final al teléfono. Una interrupción del servidor durante un envío puede dejar una alerta en estado procesando y requiere revisión; no se garantiza entrega exactamente una vez ante un corte en ese punto.

Twilio debe tener capacidad de envío disponible, el teléfono debe seguir inscrito y deben cumplirse las condiciones de mensajería de la cuenta/Sandbox. El panel llegó a mostrar saldo de prueba agotado; si Twilio devuelve errores de saldo, resolverlos en la cuenta. No se ha realizado una entrega real verificada en esta revisión. La cola conserva los eventos aunque Twilio no acepte el envío.

Las fotos de evidencia y el dashboard son accesibles mediante el dominio público. La clave autoriza cambios, no protege la lectura del historial.

## Prueba final de integración

1. Confirmar que el nuevo dashboard abre en el dominio Railway y que `/config` incluye `ubicacion`.
2. Fijar la ubicación real del prototipo.
3. Ejecutar el agente con la clave correcta y comprobar que espera sin abrir la cámara.
4. Enviar `1` desde el WhatsApp inscrito; comprobar Cámara encendida en la terminal.
5. Mostrar una escena que el modelo reconozca; verificar el evento en tabla, gráfico y KPI.
6. Comprobar la alerta y fotografía en WhatsApp.
7. Enviar `4`; comprobar Cámara apagada y el estado Esperando.

Las pruebas automatizadas cubren ese recorrido con cámara/inferencia y entrega Twilio simuladas, además de autorización, filtros, fechas, migración de SQLite, ubicaciones históricas y cola durable. No sustituyen esta comprobación con tu cuenta y escena real.

## Desarrollo local del dashboard

```powershell
$env:ALERTA_KEY=""
.\.venv\Scripts\python.exe -m flask --app app run --host 127.0.0.1 --port 5000
```

Abrir `http://127.0.0.1:5000/?theme=pacifico&demo=1` para revisar diseño. Para un ensayo completamente local del agente:
`.\.venv\Scripts\python.exe -m src.monitor_pc --url http://127.0.0.1:5000 --sin-whatsapp`.

Cuando falta ALERTA_KEY, el backend sólo permite cambios desde loopback. En remoto exige una clave configurada.

## Componentes y recursos

- `templates/dashboard.html`: estructura semántica, paneles y diálogos reutilizables.
- `static/dashboard.css`: variables de tema, estados y adaptaciones responsive.
- `static/dashboard.js`: mapa, gráfico, filtros y conexión API.
- `utils/dashboard.py`: almacenamiento, filtros, mapa, exportación y control.
- `utils/agent_sync.py`: sincronización y cola durable.
- `src/monitor_pc.py`: cámara e inferencia locales.
- `static/vendor/`: Leaflet 1.9.4, Chart.js 4.4.9 y Bootstrap Icons 1.13.2.
- Cartografía: OpenStreetMap y CARTO; atribuciones visibles en el mapa. Requiere internet para teselas.
- Tipografía: Inter de Google Fonts con alternativa del sistema.
- Arte generado con ImageGen integrado: `static/images/pacifico-tortuga.png` y `gaviotin-peruano.png`. Direcciones de generación: tortuga marina realista bajo aguas azul marino con rayos turquesa y espacio negativo; gaviotín peruano realista sobre roca costera con fondo azul. Uso editorial decorativo, sin números ni evidencia inventada.
