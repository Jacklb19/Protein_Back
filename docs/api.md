# Contratos de la API

Referencia de los endpoints REST y de los servicios gRPC. FastAPI genera el OpenAPI (`/docs`, `/redoc`, `/openapi.json`), que es la referencia ejecutable de REST; solo se sirve con `API_DOCS_ENABLED=enabled` (desarrollo local) y responde 404 por omisión, también en producción. Los contratos Protocol Buffers están en `proto/proteinforge/`. REST y gRPC comparten las mismas funciones de dominio, validación y autorización.

## Endpoints REST

Prefijo `/v1`. «JWT» es un `Authorization: Bearer` de Supabase Auth.

| Método y ruta | Propósito | Autenticación |
|---|---|---|
| `GET /health` | Estado del proceso: `status`, `version`, `identity` (identidad configurada) y `live` (canal en vivo disponible) | Pública |
| `GET /me` | Identidad del portador: `id` (coincide con `profiles.id`) y `email` | JWT |
| `POST /projects` | Crear proyecto con su instantánea transaccional (201). `requestId` (UUID) opcional: repetirlo devuelve el proyecto ya creado | JWT |
| `GET /projects` | Listar proyectos propios sin secuencias, paginados | JWT |
| `GET /projects/{id}` | Abrir un proyecto propio; ajeno o ausente, 404 | JWT |
| `PUT /projects/{id}` | Guardar o renombrar con la versión conocida; conflicto, 409 | JWT |
| `DELETE /projects/{id}` | Eliminar con la versión conocida y en cascada (204) | JWT |
| `GET /projects/{id}/versions` | Hasta 5 versiones anteriores, más recientes primero | JWT |
| `POST /projects/{id}/versions/{versionId}/restore` | Restaurar una versión | JWT |
| `POST`, `GET`, `DELETE /projects/{id}/share` | Crear, consultar y revocar el enlace de solo lectura | JWT |
| `POST /shared` | Leer un proyecto compartido con `{"token"}` en el cuerpo | Pública |
| `GET /uniprot/search`, `GET /uniprot/{accession}` | Búsqueda y ficha de UniProt | JWT |
| `GET /pdb/{pdbId}/residue-mapping` | Correspondencia SIFTS entre UniProt y PDB | JWT |
| `POST /assistant/interpret` | Interpretación de resultados ya calculados | JWT |
| `POST /predictions/fold` | Predicción de estructura con ESM Atlas | JWT |
| `POST /reports`, `GET /reports`, `POST /reports/{id}/complete`, `POST /reports/{id}/download`, `DELETE /reports/{id}` | Informes en Storage | JWT |
| `GET /live` (WebSocket) | Avisos de cambios en proyectos ([canal en vivo](live.md)) | Primer mensaje con el token |

**Validación del token.** La API consulta `GET {SUPABASE_URL}/auth/v1/user` con la `apikey` pública y el token del cliente; no verifica la firma localmente ni guarda ningún secreto de JWT. Un token inválido o caducado devuelve 401 con `WWW-Authenticate: Bearer`. Si Supabase responde que la cuenta tiene un factor `verified` y la reclamación `aal` del token no es `aal2`, la respuesta es 401 `mfa-required` (`UNAUTHENTICATED` en RPC) con `WWW-Authenticate: Bearer error="insufficient_user_authentication"`; la reclamación se lee del JWT sin verificar la firma, solo después de que Supabase lo haya aceptado. Las cuentas sin factores verificados no cambian. Si Supabase no responde, 502 o 504; sin configuración de identidad, 503.

**Errores.** Formato RFC 9457 (`application/problem+json`) con `type`, `title`, `status`, `detail`, `instance` y `requestId`. Cada respuesta lleva `X-Request-Id` (el cliente puede enviarlo para correlacionar), `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` y `Cache-Control: no-store` salvo que la ruta fije otra política; también las de CORS y los errores 500. Los errores de validación nombran campos, nunca valores: ninguna secuencia ni secreto aparece en una respuesta de error.

**CORS.** Limitado a `ALLOWED_ORIGINS` (lista separada por comas) o, en su defecto, `FRONTEND_ORIGIN`. Sin credenciales de cookie: el token viaja en `Authorization`.

## Proyectos

Detalle de persistencia, límites y RLS en [persistencia de proyectos](project-persistence.md).

- **Listado.** Acepta `limit` (1–100), `offset` (0–100.000), `query` (subcadena literal del nombre, sin distinguir mayúsculas, hasta 120 caracteres; `%` y `_` no son comodines) y `order` (`recent` por defecto, o `name`). Filtra y ordena en SQL antes de paginar. Los resúmenes incluyen un `preview` opcional: longitud, prefijo de hasta 40 residuos, exclusiones, propiedades guardadas, procedencia sin secuencia y longitudes, prefijos, parámetros y conteos de comparación. Los resultados ausentes son `null`, no ceros. No se descargan instantáneas completas para la lista. `pf_list_projects_with_links` reutiliza `pf_list_projects` y su orden. `workspace.source` opcional conserva la procedencia ligada a la secuencia exacta.
- **Cuota.** Una cuenta puede crear hasta 50 proyectos (`PROJECT_LIMIT` en `app/project_config.py`, repetido en la migración `20261113000000_project_quota.sql` y comprobado por una prueba). Un disparador `before insert` cuenta bajo un bloqueo consultivo por propietario, de modo que dos creaciones simultáneas no ocupan a la vez la última plaza. Afecta a `pf_save_project` y a cualquier inserción directa, nunca a las actualizaciones: una cuenta que supere el límite conserva y edita sus proyectos, y borrar uno libera una plaza. Repetir una creación con un `requestId` ya guardado devuelve ese proyecto aunque la cuenta esté en el límite. Error: 413 `project-quota-exceeded` en REST y `RESOURCE_EXHAUSTED` en RPC; el mensaje SQL es fijo y no lleva datos.
- **Creación idempotente.** `CreateProjectRequest.request_id` (campo 4) es un UUID generado por el cliente. La pareja (propietario, `request_id`) es única: un reintento tras perder la respuesta devuelve el proyecto existente sin tocar su instantánea, y la misma clave en otra cuenta crea otro proyecto. Un valor que no es UUID responde `INVALID_ARGUMENT` (`validation: request_id`). Requiere `20261102000000_project_create_idempotency.sql`; sin ella, una creación con clave falla (`FAILED_PRECONDITION`, 503) y una sin clave sigue funcionando, porque la API solo envía `p_create_request_id` cuando existe.
- **Historial de búsquedas del generador.** El workspace acepta `schemaVersion` 3 con `generatorHistory = {schemaVersion: 1, documentJson, documentSha256}` opcional (y `laboratory` opcional); v1 y v2 conservan sus reglas. El documento `proteinforge-generator-history` v1 admite hasta 100.000 bytes UTF-8 y de 1 a 10 ejecuciones; cada una guarda el texto exacto exportado (≤ 10.000 bytes), su SHA-256 como `id`, `origin: "local-generator"`, `recordedAt` con zona, `appBuild` hexadecimal o `null` y una `selection` opcional. Se valida la forma sin recalcular candidatos. REST y RPC usan la misma validación (422 / `INVALID_ARGUMENT` con nombres de campo, sin valores). Protobuf: `Workspace.generator_history = 7` y `ProjectPreview.generator_runs = 9`. Límites totales: 1.900.000 bytes en SQL y 2.000.000 por escritura y por mensaje RPC; nunca se trunca. Detalle en [historial del generador](generator-history.md).
- **Guardia monótona.** SQL rechaza guardar una versión de workspace inferior a la almacenada (`PT409`, «Project requires current client») sin escritura parcial. REST responde `409 project-requires-current-client` y RPC `ABORTED`; un conflicto de concurrencia mantiene `project-conflict`. Las versiones de workspace son: v1 (sin laboratorio), v2 (laboratorio), v3 (historial del generador) y v4 (laboratorio con predicciones).
- **Laboratorio.** `Workspace.laboratory = 6` contiene `{schemaVersion: 1, documentJson, documentSha256}`. El documento portable `proteinforge-laboratory` admite hasta 1.000.000 bytes UTF-8, tres variantes y 120 residuos; el hash, la fuente y los borradores se validan sin descomprimir el ZIP ni ejecutar ciencia. `activeSequence` del proyecto sigue siendo la entrada independiente del editor de análisis. Los resúmenes añaden `hasLaboratory` y `laboratoryVariants` sin el documento. SQL rechaza v1 sobre un snapshot v2 con `PT409`/`ABORTED`; `PT422` se traduce a validación.
- **Versiones.** La tabla `project_versions` (RLS por propietario; escribe solo `pf_keep_project_version`, `SECURITY DEFINER`, que copia lo guardado ahora y no recibe contenido del cliente) conserva hasta 5 versiones por proyecto (`PROJECT_VERSION_LIMIT`). Cada guardado que cambia la secuencia o la instantánea conserva el contenido anterior; renombrar no crea versión; borrar el proyecto borra sus versiones. Cada versión lleva `sameSequence` (REST) / `ProjectVersion.same_sequence` (RPC, `optional`), comparada entera en SQL con `pf_list_project_versions_compared`, porque la vista previa solo trae un prefijo. Restaurar (`{"expectedUpdatedAt"}`) ocurre en una transacción, conserva el nombre y el bloqueo optimista (409), guarda antes lo actual como versión y quita la restaurada de la lista; 404 `project-version-not-found`. Es la única escritura que puede bajar `schemaVersion`, porque esa versión ya fue una instantánea válida. Coste: hasta 5 × 1,9 MB por proyecto en el peor caso.
- **Enlaces de solo lectura.** Un enlace por proyecto. La API genera el token (32 bytes aleatorios, 43 caracteres) y lo entrega **una sola vez**; Supabase guarda solo su SHA-256, y crear otro sustituye al anterior. El propietario usa `POST|GET|DELETE /v1/projects/{id}/share` y `CreateProjectShare`, `GetProjectShare` y `DeleteProjectShare`; GET devuelve fechas, nunca el token ni su hash. El visitante usa `POST /v1/shared` con `{"token"}` en el cuerpo o `GetSharedProject`: el token no va en la ruta ni en la consulta para que no quede en registros. La petición a Supabase lleva solo la clave pública, sin `Authorization`, y llama a la única función concedida a `anon`, `pf_shared_project`. Devuelve nombre, secuencia y un workspace v1 con fuente, descriptores y alineamiento; nunca laboratorio, búsquedas guardadas ni datos del propietario. Un token mal formado, desconocido, revocado o caducado da el mismo 404 `shared-not-found`. Límite por dirección: `SHARED_REQUESTS_PER_MINUTE` (30 por omisión, por proceso). En el frontend el enlace es `/shared#<token>`: lo que va tras `#` no se envía al servidor ni en `Referer`.
- **Duración del enlace.** Acepta 1, 7 o 30 días (`PROJECT_SHARE_DURATIONS_DAYS`, repetida en la migración): `POST /v1/projects/{id}/share` con `{"durationDays"}` y `CreateProjectShareRequest.duration_days`. Sin duración (cuerpo vacío, o 0 en RPC) son 7 días; cualquier otra es 422 / `INVALID_ARGUMENT` sin llegar a la base. El listado lleva la caducidad del enlace activo: `shareExpiresAt` en REST y `ProjectSummary.share_expire_time` en RPC, ausente si no hay enlace o caducó.

## Referencias, UniProt y SIFTS

- **UniProt** ([detalle](uniprot.md)): `GET /v1/uniprot/search` y `GET /v1/uniprot/{accession}` (`ReferenceService`). Cada identidad autenticada dispone de `UNIPROT_REQUESTS_PER_MINUTE` consultas por minuto (20 por omisión; 0 desactiva el presupuesto), compartidas entre búsqueda, ficha y los métodos equivalentes de RPC. Al agotarlo, 429 `uniprot-rate-limit` (`RESOURCE_EXHAUSTED`) sin llamar al proveedor.
- **Anotaciones posicionales.** `GET /v1/uniprot/{accession}` y `ReferenceService/GetEntry` devuelven `features` y `featuresTruncated`. Cada anotación lleva `kind` (sitio activo, unión, sitio, unión a ADN, dedo de zinc, motivo, dominio, región, transmembrana, señal, péptido de tránsito, propéptido, cadena, péptido, puente disulfuro, residuo modificado, glicosilación o variante natural), `description` en el idioma de la fuente (hasta 400 caracteres), `begin`, `end` y `paired` (verdadero cuando nombra dos residuos unidos, no el tramo entre ellos). Solo se devuelven anotaciones con los dos extremos exactos y dentro de la secuencia: lo incierto se omite. Límites: 60 por tipo y 300 en total, ordenadas por tipo y posición.
- **SIFTS** ([detalle](sifts.md)): `GET /v1/pdb/{pdb_id}/residue-mapping` y `ReferenceService/GetResidueMapping`, autenticados, con versión y hash completos, presupuesto de referencias y respuesta máxima de 2.000.000 bytes.

## Asistente

`POST /v1/assistant/interpret` y `AssistantService/Interpret`. Reciben el tema (`analysis`, `alignment`, `laboratory`, `candidates`), el idioma (`es` o `en`), de 1 a 80 datos ya calculados como los muestra la interfaz y, opcionalmente, hasta 2.000 residuos (la interfaz no los envía); devuelven texto, modelo, proveedor y fecha. La API reenvía los datos a Groq con su propia clave y el token del usuario no sale. Errores: 422 / `INVALID_ARGUMENT`; 503 `assistant-not-configured` / `FAILED_PRECONDITION`; 429 `assistant-rate-limit` / `RESOURCE_EXHAUSTED`; 502 (`assistant-provider-error`, `-invalid-response`, `-incomplete`, `-unverified-figure`) / `UNAVAILABLE`; 504 / `DEADLINE_EXCEEDED`. El verificador rechaza cifras fuera de los datos, redondeos y excepciones explícitas de conteos y cotas; no comprueba la semántica completa. Ver [asistente](assistant.md).

## Predicción

`POST /v1/predictions/fold` y `FoldingService/Predict`, autenticados y desactivados por defecto (`FOLDING_ENABLED`). Secuencia exacta de 3 a 120 residuos más consentimiento por solicitud; la respuesta incluye PDB, hash y procedencia sin métricas inventadas, y no se envía el token al proveedor. Un laboratorio portable v2 requiere workspace v4 y la migración `20261030000000_structure_predictions.sql`. Ver [predicción](folding.md).

## Informes en Storage

`/v1/reports` (reserva con URL firmada de subida, listado, finalización, descarga y borrado) y `ReportService`, autenticados. La API nunca recibe los bytes: el navegador sube directamente a Storage con la URL firmada, y PostgREST y Storage reciben el JWT del usuario. Bucket privado `reports`, tabla `reports` con RLS, cuota por propietario y límites en `app/report_config.py`, repetidos en la migración [20261101000000_report_storage.sql](../supabase/migrations/20261101000000_report_storage.sql). Los límites del frontend están en `src/features/reports/storage/config.ts`. El frontend lo activa con `VITE_REPORT_STORAGE`.

## Presupuesto compartido

Con `SHARED_BUDGET_ENABLED=enabled` (apagado por omisión) y la migración `20261113000001_shared_budget.sql`, PostgreSQL es la fuente de verdad del presupuesto por cuenta del asistente, UniProt (búsqueda, ficha y mapeo de residuos) y la predicción. `pf_spend` cuenta una ventana por cuenta y ámbito en una sola sentencia atómica (la ventana empieza con la primera petición) y, si se pide, una ventana común a todas las cuentas. La API la llama con el JWT de quien pide y la clave pública: cobra a `auth.uid()` y a nadie más. La tabla `spend_windows` es privada (sin permisos ni políticas; solo las funciones `SECURITY DEFINER`).

- Asistente: `ASSISTANT_REQUESTS_PER_MINUTE` por cuenta y `ASSISTANT_REQUESTS_PER_DAY` para todas las cuentas juntas en 24 h (0 = sin tope). Un acierto de caché no gasta, y `X-Assistant-Remaining` se lee con `pf_spend_remaining`.
- UniProt: `UNIPROT_REQUESTS_PER_MINUTE` por cuenta. Predicción: 1 por minuto por cuenta y 3 por minuto en total, entre instancias; se mantiene la puerta en memoria de una petición a la vez.
- Techos en SQL (60, 120 y 3 por ventana; 100.000 para la ventana común): quien llame a `pf_spend` directamente solo gasta su presupuesto y no puede inflar la ventana común. Un valor de entorno mayor se recorta al techo.
- Si la base no responde (red, tiempo, migración sin aplicar o respuesta inesperada), cada proceso decide con sus contadores en memoria y lo registra sin datos. La decisión es explícita: no se falla cerrado porque los proveedores son públicos y gratuitos con sus propios límites y una caída de la base apagaría el asistente, UniProt y la predicción para todos. Un token inválido nunca es respaldo: sigue siendo 401.
- El límite por dirección de `/v1/shared` sigue en memoria.

## MFA en RLS

La migración `20261113000002_mfa_policies.sql` añade una variante opt-in de la guía MFA de Supabase: una sesión necesita `aal2` solo si su cuenta tiene un factor `verified`; sin factor, o con uno sin verificar, todo sigue en `aal1`. Políticas `as restrictive` para `authenticated` en `projects`, `analyses`, `reports`, `project_versions`, `project_shares` y `storage.objects` del bucket `reports`: no conceden nada, solo estrechan las políticas del propietario. La comprobación vive en `pf_session_meets_mfa()` (`SECURITY DEFINER`), para no depender de que `authenticated` pueda leer `auth.mfa_factors`; un JWT sin `aal` cuenta como `aal1`.

- Las funciones `SECURITY DEFINER` saltan RLS, así que `pf_share_project`, `pf_unshare_project` y `pf_keep_project_version` repiten la comprobación (`PT401 MFA required`). `pf_shared_project` (anónima) y `pf_spend` (solo el propio presupuesto) no la necesitan.
- La API ya rechaza esas sesiones antes (`mfa-required`, `app/identity.py`); la migración cubre las llamadas directas a PostgREST y Storage con la clave pública.
- Las pruebas emulan `auth.mfa_factors` y la reclamación `aal` en `tests/postgres_bootstrap.sql`.

## Canal en vivo

`GET /v1/live` (WebSocket, subprotocolo `proteinforge.live.v1`), desactivado salvo `LIVE_ENABLED=enabled`. Token en el primer mensaje, origen comprobado y avisos `projects.changed` con identificadores y marcas de tiempo. Sin migraciones. Ver [canal en vivo](live.md).

## gRPC

Contratos en `proto/proteinforge/`. Los servicios reutilizan las funciones de dominio, la validación y la autorización de REST, que sigue activo. `DesignService` (`v1alpha1`) es un borrador sin implementar.

| Servicio | Métodos | Equivale a |
|---|---|---|
| `proteinforge.account.v1.AccountService` | `GetStatus`, `GetMe` | `GET /v1/health`, `GET /v1/me` |
| `proteinforge.project.v1.ProjectService` | Proyectos (`ListProjects`, `GetProject`, `CreateProject`, `UpdateProject`, `DeleteProject`), versiones, enlaces y `GetSharedProject` | `/v1/projects`, `/v1/shared` |
| `proteinforge.reference.v1.ReferenceService` | `SearchEntries`, `GetEntry`, `GetResidueMapping` | `/v1/uniprot`, `/v1/pdb` |
| `proteinforge.assistant.v1.AssistantService` | `Interpret` | `POST /v1/assistant/interpret` |
| `proteinforge.folding.v1.FoldingService` | `Predict` | `POST /v1/predictions/fold` |
| `proteinforge.report.v1.ReportService` | `CreateReportUpload`, `CompleteReport`, `ListReports`, `CreateReportDownload`, `DeleteReport` | `/v1/reports` |

Las rutas son `POST /<paquete>.<Servicio>/<Método>` en la raíz (los clientes gRPC nativos no admiten prefijo). Cada servicio responde a tres protocolos según el `Content-Type`:

- **gRPC-Web** (`application/grpc-web+proto`): el que usa el navegador, sobre HTTP/1.1.
- **Connect** (`application/json` o `application/proto`): útil para depurar con `curl`.
- **gRPC nativo** (`application/grpc`): requiere HTTP/2 y *trailers* ASGI. Uvicorn no los ofrece; Hypercorn sí (`requirements-dev.txt`). En Vercel no se usa gRPC nativo.

El token se envía en `Authorization: Bearer`, igual que en REST. Errores: 401 → `UNAUTHENTICATED`; 403 → `PERMISSION_DENIED`; 404 → `NOT_FOUND`; 409 → `ABORTED` (`project-conflict` o `project-requires-current-client`); 400 y 422 → `INVALID_ARGUMENT` (`validation: <campos>`, sin los valores enviados); 429 → `RESOURCE_EXHAUSTED`; 502 y 503 → `UNAVAILABLE`; 504 → `DEADLINE_EXCEEDED`; esquema o identidad sin configurar → `FAILED_PRECONDITION`; cuerpo o compresión imposibles de decodificar (JSON truncado, tipos erróneos, protobuf o gzip corruptos) → `INVALID_ARGUMENT` con `malformed-request` (HTTP 400 en Connect, sin el texto del analizador); cualquier otro → `INTERNAL` (`internal`). El mensaje es siempre un código estable. La paginación usa `page_token` opaco y un token no emitido por el servidor da `INVALID_ARGUMENT`. Los mensajes admiten hasta 2 MB. CORS expone `Grpc-Status` y `Grpc-Message` y admite las cabeceras de Connect y gRPC-Web. Todas las respuestas llevan `X-Request-Id`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` y `Cache-Control: no-store` (salvo que la ruta fije otro).

`ListProjectsRequest` añade `query` y `ProjectOrder`; `ProjectSummary.preview` es el campo 6, opcional. `ListProjectsResponse.query_applied` permite detectar servidores antiguos que ignoran los campos nuevos, de modo que el cliente no presenta esa respuesta como una búsqueda u orden por nombre correctos. El token opaco queda ligado a la consulta y al orden: cambiarlos con un token anterior da `INVALID_ARGUMENT`. La paginación usa desplazamiento; los cambios concurrentes de la colección pueden desplazar filas, por lo que el cliente deduplica al ampliar y reinicia al refrescar o escribir. No es una instantánea transaccional de varias páginas.

### Código generado

El código generado vive en `app/rpc/gen/` (versionado; no se edita). Se regenera tras cambiar un `.proto`; la versión de buf es la misma que usa el frontend (1.75.0), y CI comprueba que el resultado coincide con lo versionado:

```bash
.venv/Scripts/python -m pip install -r requirements-dev.txt
PATH="$PWD/.venv/Scripts:$PATH" npx --yes @bufbuild/buf@1.75.0 lint
PATH="$PWD/.venv/Scripts:$PATH" npx --yes @bufbuild/buf@1.75.0 generate
```

En POSIX, el directorio de los ejecutables del entorno virtual es `.venv/bin`. Para probar los tres protocolos contra un servidor en marcha: `PYTHONPATH=. .venv/Scripts/python scripts/rpc_smoke.py http://127.0.0.1:8000` (con Hypercorn para incluir gRPC nativo). La evaluación de gRPC está en el [documento gRPC del frontend](https://github.com/Jacklb19/ProteinForge/blob/main/docs/grpc.md).
