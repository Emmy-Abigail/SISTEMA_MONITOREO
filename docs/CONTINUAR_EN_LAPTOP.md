# Continuar desde la laptop — ÑAWI APU GOGO

Actualizado el 13 de noviembre de 2026.

La guía vigente es [Dashboard y monitoreo desde WhatsApp](DASHBOARD.md). Reemplaza las instrucciones anteriores de cámara incrustada en el dashboard y de subida de evidencias a GitHub para el nuevo agente de PC.

## Qué quedó preparado

- Dashboard ambiental propio con tres variantes: Pacífico profundo, Abisal y Reserva marina.
- Mapa con ubicación fija del prototipo y nidos observados; coordenadas históricas por detección.
- KPI, gráfico interactivo, amenazas, tarjetas de especies, filtros, tabla y CSV.
- Agente local que abre cámara al recibir 1, 2 o 3 y la libera con 4.
- Registro y alerta en segundo plano, cola SQLite durable y reintentos.
- Tres modelos disponibles: tortugas, gaviotines e invasores.
- Cámara física comprobada a 640 × 480 y los tres modelos con inferencia correcta.
- Diecisiete pruebas funcionales aprobadas. El recorrido WhatsApp → cámara → registro → alerta → apagado se verificó con cámara y Twilio simulados.

La fuente de Railway sigue siendo **Emmy-Abigail/SISTEMA_MONITOREO, main**, según lo solicitado. La copia de Frank se mantiene sincronizada para trabajar localmente.

## Qué falta comprobar con las cuentas reales

1. Desplegar el último commit de main en el servicio Railway. El acceso directo a Railway no está conectado a esta sesión; no se pudo ejecutar ni confirmar ese despliegue.
2. Agregar/verificar el volumen /data para conservar historial y usuarios entre despliegues.
3. Verificar el webhook POST /whatsapp del Sandbox y una respuesta real en el teléfono.
4. Ejecutar el agente con la ALERTA_KEY real y comprobar una detección de fauna y la entrega de su alerta.

Las cuatro variables de Railway fueron completadas por el usuario. Sus valores privados no se incorporaron al código ni se conocen en esta sesión. Twilio mostró previamente un saldo de prueba agotado; revisar capacidad de envío si rechaza mensajes.

La revisión visual automática del frontend fue detenida por el control del navegador al no poder verificar su URL. Está pendiente validar el renderizado final 1440 × 900 en el navegador; no se entrega una captura del frontend como si hubiese sido verificada.

## Direcciones

| Recurso | Dirección |
|---|---|
| Fuente de Railway | https://github.com/Emmy-Abigail/SISTEMA_MONITOREO |
| Copia de Frank | https://github.com/FRANKEVIN25/SISTEMA_MONITOREO |
| Dashboard | https://sistemamonitoreo-production-8883a6d5.up.railway.app/ |
| Configuración para el agente | https://sistemamonitoreo-production-8883a6d5.up.railway.app/config |
| Webhook Twilio, POST | https://sistemamonitoreo-production-8883a6d5.up.railway.app/whatsapp |

## Arranque diario

En Visual Studio Code, desde la carpeta del proyecto:

```powershell
.\.venv\Scripts\python.exe -m src.monitor_pc
```

Introduce la ALERTA_KEY cuando se solicite. Mantén esa terminal abierta. Desde el WhatsApp inscrito en el Sandbox: menu, después 1 para tortugas, 2 para gaviotines o 3 para amenazas. La opción 4 apaga la cámara y deja el agente esperando. La opción 5 muestra estado y dashboard.

No hace falta levantar Flask local para consultar el dashboard de Railway. Para revisar el diseño local antes del despliegue, consulta los comandos de [DASHBOARD.md](DASHBOARD.md).
