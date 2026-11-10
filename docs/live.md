# Canal en vivo (WebSocket)

Implementado en local el 9–10 nov 2026, **sin commit, push ni publicación**. Desactivado por omisión en los dos repositorios.

## Qué hace

`GET /v1/live` con `Upgrade: websocket`. Avisa a un navegador con sesión de que los proyectos de su cuenta cambiaron en otra pestaña o dispositivo; la aplicación vuelve a pedir la lista sin que la persona haga nada. El canal no transporta datos de proyectos: solo identificadores y marcas de tiempo que quien llama ya puede leer.

Por qué aquí y no en otra parte: la lectura del asistente se verifica entera antes de mostrarse, así que no se puede transmitir por partes; la predicción es una sola petición de uno o dos segundos; y el cálculo corre en el navegador. Lo único que cambia fuera de la pestaña son los proyectos guardados.

## Protocolo `proteinforge.live.v1`

Mensajes JSON de texto. Nombres, códigos y límites en `app/live_config.py`; el frontend los repite en `src/features/live/config.ts`.

| Sentido | Mensaje | Cuándo |
|---|---|---|
| cliente → servidor | `{"type":"auth","token":"<JWT>"}` | Primer mensaje, antes de 10 s; otra vez cuando la sesión renueva el token |
| cliente → servidor | `{"type":"ping"}` | Cada `heartbeatSeconds` |
| servidor → cliente | `{"type":"ready","revision":"…","heartbeatSeconds":25}` | Tras validar el token y leer los proyectos por primera vez |
| servidor → cliente | `{"type":"pong"}` | Respuesta a `ping` |
| servidor → cliente | `{"type":"projects.changed","revision":"…","changed":[{"id","updatedAt"}],"removed":["id"]}` | Al cambiar la lista |

`revision` es el SHA-256 del total y de los pares (id, `updated_at`) de los 200 proyectos más recientes. Al reconectar, el cliente compara la de `ready` con la última que vio y así sabe si se perdió algo. `removed` solo se informa cuando la ventana contiene todos los proyectos; con más de 200, un cambio de total avisa sin afirmar qué se borró.

Códigos de cierre: 4000 rotación prevista (reconectar ya), 4400 mensaje mal formado o tipo desconocido, 4401 token ausente, rechazado o caducado, 4403 token de otra cuenta, 4408 silencio, 4429 demasiadas conexiones o mensajes, 1003 marco binario, 1009 mensaje de más de 8.192 bytes, 1013 Supabase no responde.

## Decisiones de seguridad

- **El token nunca va en la URL.** Un navegador no puede poner cabeceras en un WebSocket; va en el primer mensaje y se valida con `/auth/v1/user`, igual que REST y RPC, incluido el requisito `aal2`.
- **Origen comprobado en el servidor.** CORS no cubre WebSocket: el saludo solo se acepta desde `ALLOWED_ORIGINS` y con el subprotocolo; lo demás recibe 403 sin abrir nada.
- **RLS sigue decidiendo.** Cada conexión lee `projects?select=id,updated_at` con el JWT de quien llama. Sin `service_role`.
- **Una conexión, una cuenta.** Un `auth` posterior con otra identidad cierra con 4403.
- **Acotado:** 4 conexiones por cuenta y proceso, 30 mensajes por minuto por conexión, 8.192 bytes por mensaje, cierre tras 62,5 s sin mensajes.
- Los registros no contienen tokens ni mensajes del cliente.

## Por qué cada conexión mira por sí misma

En Vercel, dos conexiones pueden caer en instancias distintas y no comparten memoria ([documentación](https://vercel.com/docs/functions/websockets)). Por eso cada conexión consulta la lista cada `LIVE_POLL_SECONDS` (5 s) y esa consulta es la fuente de verdad. Además, una escritura atendida por el mismo proceso despierta al instante las conexiones de su propietario (`LiveHub`): es un atajo, no una dependencia.

Una función de Vercel Hobby dura 300 s como máximo; el servidor cierra antes, a los `LIVE_MAX_CONNECTION_SECONDS` (240 s), con 4000, y el cliente reconecta en el acto.

## Activación (pendiente del propietario)

1. Backend: publicar, y en Vercel definir `LIVE_ENABLED=enabled`. Requiere Fluid compute (por omisión en proyectos creados desde el 23 abr 2025). `wsproto` está fijado en `requirements.txt`.
2. Frontend: `VITE_PROJECT_LIVE=enabled` y redesplegar. La CSP de `vercel.json` ya incluye `wss://protein-back.vercel.app`.
3. Sin migraciones.

**Sin comprobar:** el canal en Vercel real. La documentación de Vercel muestra FastAPI con `uvicorn[standard]` en `pyproject.toml`; que baste `wsproto` en `requirements.txt` no está confirmado y hay que verlo al desplegar. Tampoco se midió el consumo del plan Hobby con conexiones abiertas: cada pestaña visible mantiene una función viva y hace una lectura a Supabase cada 5 s. Una pestaña oculta más de 60 s suelta la conexión.

## Verificación local

- `tests/test_live.py`, 14 pruebas: saludo rechazado (desactivado, otro origen, sin origen, sin subprotocolo, sin identidad), primer mensaje, silencio, avisos con el token de quien llama, ventana parcial, despertar tras una escritura REST, ping y token renovado, otra cuenta, mensajes fuera del protocolo, límites, rotación, token caducado y Supabase caído. Batería completa: 214 ejecutadas, 27 SQL omitidas sin base; ruff limpio; `app/live.py` 95 %.
- uvicorn real en loopback con un cliente WebSocket de Node: origen ajeno rechazado, `ready`, `pong`, `projects.changed` y cierre 4000.
- Navegador real contra esa API con identidad simulada: la lista de Proyectos mostró un proyecto nuevo sin recargar.
