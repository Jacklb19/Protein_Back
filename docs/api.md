# Contratos de API

## Presupuesto compartido — implementación local del 13 nov 2026

Migración `20261113000001_shared_budget.sql` (**solo local, sin aplicar en Supabase**) y `SHARED_BUDGET_ENABLED=enabled` (apagado por omisión). Con la bandera, PostgreSQL es la fuente de verdad del presupuesto por cuenta del asistente, UniProt (búsqueda, ficha y mapeo de residuos) y predicción: `pf_spend` cuenta una ventana por cuenta y ámbito en una sola sentencia atómica (la ventana empieza con la primera petición, como el contador anterior) y, si se pide, una ventana común a todos. La API la llama con el JWT de quien pide y la clave pública: cobra a `auth.uid()` y a nadie más. Tabla `spend_windows` privada (sin permisos ni políticas; solo las funciones `security definer`).

- Asistente: `ASSISTANT_REQUESTS_PER_MINUTE` por cuenta y `ASSISTANT_REQUESTS_PER_DAY` para todas las cuentas juntas en 24 h (0 = sin tope, valor por omisión). Un acierto de caché no gasta y `X-Assistant-Remaining` se lee con `pf_spend_remaining`; las consultas inválidas se rechazan antes de gastar.
- UniProt: `UNIPROT_REQUESTS_PER_MINUTE` por cuenta. Predicción: 1 por minuto por cuenta y 3 por minuto en total, ahora entre instancias; la puerta en memoria de una petición a la vez se mantiene.
- Techos en SQL (60, 120 y 3 por ventana; 100000 para la ventana común): quien llame a `pf_spend` directamente solo gasta su presupuesto y no puede inflar la ventana común por encima de ese ritmo. Un valor de entorno mayor se recorta al techo.
- **Si la base no responde** (red, tiempo, migración sin aplicar, respuesta inesperada), cada proceso decide con sus contadores en memoria, como antes, y lo registra sin datos. Decisión explícita: no se falla cerrado porque los proveedores son públicos y gratuitos con sus propios límites, el contador en memoria sigue acotando cada instancia y una caída de la base apagaría el asistente, UniProt y la predicción para todos. Un token inválido nunca es respaldo: sigue siendo 401.
- El límite por dirección de `/v1/shared` sigue en memoria.

Orden de activación: aplicar la migración, definir `SHARED_BUDGET_ENABLED=enabled` (y, si se quiere, `ASSISTANT_REQUESTS_PER_DAY`) en Vercel y redesplegar. Las filas de cuentas borradas quedan (una por ámbito, sin datos personales salvo el identificador).

## Cuota de proyectos por cuenta — implementación local del 13 nov 2026

Migración `20261113000000_project_quota.sql` (**solo local, sin aplicar en Supabase**). Cada cuenta puede crear hasta 50 proyectos (`PROJECT_LIMIT` en `app/project_config.py`, repetido en la migración y comprobado por prueba). Un disparador `before insert` en `projects` cuenta bajo un bloqueo consultivo por propietario, como la cuota de informes, así que dos creaciones simultáneas no ocupan a la vez la última plaza. Afecta a `pf_save_project` y a cualquier inserción directa; nunca a actualizaciones: una cuenta que ya supere el límite conserva y edita sus proyectos, y borrar uno libera una plaza. Repetir una creación con un `requestId` ya guardado devuelve ese proyecto aunque la cuenta esté en el límite.

- `POST /v1/projects`: 413 `project-quota-exceeded`. `ProjectService/CreateProject`: `RESOURCE_EXHAUSTED` con el mismo mensaje. El mensaje SQL es fijo (`Project quota exceeded`) y no lleva datos.
- El frontend lo traduce a `project-quota` y lo muestra donde ya muestra los errores de guardado; la cola sin conexión no lo reintenta. Un frontend anterior lo vería como límite de consultas y lo reintentaría tres veces antes de dejarlo a la persona.

## Enlaces de solo lectura — implementación local del 11 nov 2026

Migración `20261111000000_project_shares.sql` (**solo local, sin aplicar en Supabase**). Un enlace por proyecto; caduca a los 7 días (`PROJECT_SHARE_LIFETIME_DAYS`, repetido en la migración y comprobado por prueba).

- La API genera el token (32 bytes aleatorios, 43 caracteres) y lo entrega **una sola vez**; Supabase guarda solo su SHA-256. Crear otro sustituye al anterior.
- Propietario (con sesión): `POST|GET|DELETE /v1/projects/{id}/share` y `ProjectService/CreateProjectShare`, `GetProjectShare`, `DeleteProjectShare`. GET devuelve fechas, nunca el token ni su hash.
- Visitante (sin sesión): `POST /v1/shared` con `{"token"}` en el cuerpo, o `ProjectService/GetSharedProject`. El token no va en la ruta ni en la consulta para que no quede en registros. La petición a Supabase lleva solo la clave pública (`apikey`), sin `Authorization`, y llama a la única función concedida a `anon`: `pf_shared_project`.
- Devuelve nombre, secuencia y un workspace de versión 1 con fuente, descriptores y alineamiento. Nunca laboratorio, búsquedas guardadas ni datos del propietario.
- Token mal formado, desconocido, revocado o caducado: el mismo 404 `shared-not-found`. Límite por dirección: `SHARED_REQUESTS_PER_MINUTE` (30 por omisión, por proceso).

En el frontend el enlace es `/shared#<token>`: lo que va tras `#` no se envía al servidor ni en el Referer.

## Versiones de proyecto — implementación local del 10 nov 2026

Migración `20261110000000_project_versions.sql` (**solo local, sin aplicar en Supabase**). Tabla `project_versions` con RLS: el propietario solo lee sus filas; la única escritora es `pf_keep_project_version` (SECURITY DEFINER), que copia lo guardado ahora y no recibe contenido del cliente. Cada guardado que cambia la secuencia o el snapshot conserva el contenido anterior; renombrar no crea versión; se guardan 5 por proyecto (`PROJECT_VERSION_LIMIT`, repetido en la migración y comprobado por prueba). Borrar el proyecto borra sus versiones.

- `GET /v1/projects/{id}/versions` y `ProjectService/ListProjectVersions`: hasta 5, más recientes primero, con vista previa compacta; nunca el snapshot.
- `POST /v1/projects/{id}/versions/{versionId}/restore` (`{"expectedUpdatedAt"}`) y `ProjectService/RestoreProjectVersion`: restaura en una transacción, conserva el nombre y el bloqueo optimista (409), guarda antes lo actual como versión (se puede deshacer) y quita de la lista la versión restaurada. 404 `project-version-not-found`. Es la única escritura que puede bajar `schemaVersion`, porque la versión ya fue un snapshot válido.

Coste: hasta 5 × 1,9 MB por proyecto en el peor caso (laboratorio completo); las búsquedas guardadas del generador también crean versión. Pendiente: aplicar la migración en Supabase (autorización del propietario), publicar backend y después repinchar `buf.gen.yaml` del frontend.

## Canal en vivo — implementación local del 10 nov 2026

[Protocolo, seguridad y activación](live.md): `GET /v1/live` (WebSocket, subprotocolo `proteinforge.live.v1`), desactivado salvo `LIVE_ENABLED=enabled`. Token en el primer mensaje, origen comprobado, avisos `projects.changed` con identificadores y marcas de tiempo. Sin migraciones. Sin publicar.

## Informes en Storage — implementación local del 1 nov 2026

[Propuesta y contrato](https://github.com/Jacklb19/ProteinForge/blob/dev/docs/reports-storage-proposal-2026-11-01.md) (RF-14). `/v1/reports` (`POST` reserva y URL firmada de subida, `GET` lista, `POST /{id}/complete`, `POST /{id}/download`, `DELETE /{id}`) y `proteinforge.report.v1.ReportService`, autenticados. La API nunca recibe los bytes: el navegador sube directamente a Storage con la URL firmada; PostgREST y Storage reciben el JWT del usuario. Bucket privado `reports`, tabla `reports` con RLS, cuota por propietario y límites en `app/report_config.py`, repetidos en la migración [20261101000000_report_storage.sql](../supabase/migrations/20261101000000_report_storage.sql), aplicada por el propietario el 2 nov 2026 (declarado, sin inspección remota). Interfaz en el frontend detrás de `VITE_REPORT_STORAGE`, todavía sin activar.

## Predicción — implementación local del 30 oct 2026

[Contrato y verificación](folding.md): `POST /v1/predictions/fold` y `FoldingService/Predict`, autenticados y desactivados por defecto. Secuencia exacta de 3–120 residuos más consentimiento por solicitud; respuesta PDB/hash/procedencia sin métricas inventadas. Comparte cuota REST/RPC y no envía el token al proveedor. Laboratorio portable v2 requiere workspace v4 y la migración nueva solo local `20261030000000_structure_predictions.sql`. Sin publicación ni aplicación remota. La implementación del historial mencionada abajo ya está publicada y su migración fue declarada aplicada por el propietario el 28 oct; las secciones fechadas conservan su contexto anterior.

## Estado del 26 de octubre de 2026

Asistente y laboratorio publicados en la entrega del 23; no volver a solicitar sus migraciones/publicación como si siguieran pendientes. Los apartados inferiores mantienen la historia de cada contrato. El propietario aprobó el suministro SIFTS y la maqueta de selección. Implementados localmente `GET /v1/pdb/{pdb_id}/residue-mapping` y `ReferenceService/GetResidueMapping`, autenticados, con versión/hash completos, presupuesto de referencias y respuesta máxima de 2.000.000 bytes. [Contrato y pruebas SIFTS](sifts.md). Sin migraciones nuevas; interfaz Biblioteca/Analizar integrada y verificada localmente el 26 con identidad simulada. Publicar backend primero, luego actualizar pin/regenerar frontend. Lectura autenticada de Groq y recorrido SIFTS con JWT real todavía pendientes de comprobar.

## Historial del generador en Proyectos — 27 oct 2026

Implementado localmente en backend `dev` tras las decisiones tomadas por delegación ([propuesta y decisiones](generator-history-contract-proposal.md)). Workspace acepta `schemaVersion` 3: `laboratory` opcional y `generatorHistory={schemaVersion:1,documentJson,documentSha256}` opcional; v1 y v2 conservan sus reglas (v2 sigue exigiendo laboratorio y ninguno admite historial). Documento `proteinforge-generator-history` v1 de hasta 100.000 bytes UTF-8 y 1–10 ejecuciones; cada una guarda el texto exacto exportado (≤ 10.000 bytes), su SHA-256 como `id`, `origin: "local-generator"`, `recordedAt` con zona (hora del cliente), `appBuild` hexadecimal o `null` y `selection` opcional. Se valida la forma sin recalcular candidatos. REST y RPC usan la misma validación: 422/`INVALID_ARGUMENT` con nombres de campo, sin valores. Protobuf añade `Workspace.generator_history = 7` y `ProjectPreview.generator_runs = 9`, sin renumerar; la lista solo da el recuento. Límites totales sin cambios: 1.900.000 bytes en SQL, 2.000.000 por escrito y por mensaje RPC; nunca se trunca.

Guardia monótona: SQL rechaza guardar una versión inferior a la almacenada (`PT409` «Project requires current client»), sin escritura parcial. La API lo expone como `409` con código `project-requires-current-client` (REST, `type` del problema) y `ABORTED` con ese mensaje (RPC); los clientes actuales siguen viéndolo como conflicto. Un conflicto de concurrencia mantiene `project-conflict`. Migración nueva [20261027000000_generator_history.sql](../supabase/migrations/20261027000000_generator_history.sql), comprobada solo en PostgreSQL 18 local; **no aplicada en Supabase** y sin autorización para ello. Frontend sin cambios: el pin, la regeneración, los contratos del cliente y la interfaz son una fase posterior.

## Asistente de interpretación — 23 oct 2026

`POST /v1/assistant/interpret` y `proteinforge.assistant.v1.AssistantService/Interpret`, autenticados y equivalentes. Reciben el tema (`analysis`, `alignment`, `laboratory`, `candidates`), el idioma (`es`/`en`), de 1 a 80 datos ya calculados como los muestra la interfaz y, opcionalmente, hasta 2.000 residuos; devuelven texto, modelo, proveedor y fecha. La interfaz aprobada solo usa Analizar/Laboratorio y omite la secuencia. La API reenvía los datos a Groq con su propia clave; el token del usuario no sale. Errores: 422/`INVALID_ARGUMENT`, 503 `assistant-not-configured`/`FAILED_PRECONDITION`, 429 `assistant-rate-limit`/`RESOURCE_EXHAUSTED`, 502 (`assistant-provider-error`, `-invalid-response`, `-incomplete`, `-unverified-figure`)/`UNAVAILABLE` y 504/`DEADLINE_EXCEEDED`. El verificador rechaza cifras fuera de los datos, redondeos y excepciones explícitas de conteos/cotas; no comprueba la semántica completa. Sin esquema ni migraciones. Implementado localmente, pendiente de publicación backend→actualizar pin/regenerar→frontend. [Detalle](assistant.md).

## Presupuesto de consultas a UniProt — 22 oct 2026

Cada identidad autenticada dispone de `UNIPROT_REQUESTS_PER_MINUTE` consultas por minuto (20 por omisión, 0 desactiva el presupuesto), compartidas entre `GET /v1/uniprot/search`, `GET /v1/uniprot/{accession}` y los métodos equivalentes de `ReferenceService`. Al agotarlo, la API responde 429 `uniprot-rate-limit` (`RESOURCE_EXHAUSTED`) sin llamar al proveedor. El recuento vive en el proceso: varias instancias multiplican el presupuesto, de modo que acota lo que una instancia reenvía y no constituye una cuota global. Sin cambios de esquema, migraciones ni contratos protobuf. [Detalle](uniprot.md).

## Laboratorio en Proyectos — 21 oct 2026

Workspace acepta schemaVersion 1 (anterior) y 2 con laboratory={schemaVersion:1,documentJson,documentSha256}. Documento portable proteinforge-laboratory v1, hasta 1.000.000 bytes UTF-8, tres variantes/120 residuos; hash exacto, fuente y borradores validados sin descomprimir ZIP ni ejecutar ciencia. La secuencia activa coherente pertenece al state del laboratorio; activeSequence del proyecto sigue siendo el documento independiente del editor de análisis. REST y gRPC-Web usan la misma validación. Preview añade hasLaboratory/laboratoryVariants sin documento; protobuf añade Workspace.laboratory=6 y preview campos 7/8 sin renumerar. Mensaje RPC completo máximo 2.000.000 bytes, sin ampliar el límite. SQL rechaza v1 sobre un snapshot v2 con PT409/ABORTED; PT422 se traduce a validación. Nueva migración 20261021000000_laboratory_projects.sql comprobada en PostgreSQL 18 local y aplicada por el propietario según su confirmación del 21 oct; sin inspección remota. El código nuevo sigue pendiente de autorización para publicarse. No volver a ejecutar las migraciones.

Actualizado el 19 de octubre de 2026. OpenAPI generado por FastAPI (`/docs`, `/redoc` y `/openapi.json`) es la referencia ejecutable de REST; desde el 2 nov 2026 solo se sirve con `API_DOCS_ENABLED=enabled` (desarrollo local) y responde 404 por omisión, también en producción. CRUD/gRPC-Web publicados previamente. Esta entrega añade vistas previas y consulta global; requiere la nueva migración antes de publicar el backend.

## Implementado

También implementados GET /v1/uniprot/search y GET /v1/uniprot/{accession}; ver [contrato y evidencia](uniprot.md). workspace.source opcional conserva procedencia ligada a la secuencia exacta.

| Método y ruta | Propósito | Autenticación |
|---|---|---|
| GET /v1/health | Estado del proceso, versión y si la identidad está configurada | Pública |
| GET /v1/me | Identidad del portador del token: `id` (coincide con `profiles.id`) y `email` | Bearer JWT de Supabase |
| POST /v1/projects | Crear proyecto/snapshot transaccional; 201. `requestId` (UUID) opcional: repetirlo devuelve el proyecto ya creado | Bearer JWT |
| GET /v1/projects | Listar propios sin secuencias, paginados | Bearer JWT |
| GET /v1/projects/{id} | Abrir propio; ajeno/ausente: 404 | Bearer JWT |
| PUT /v1/projects/{id} | Guardar o renombrar con versión conocida; 409 | Bearer JWT |
| DELETE /v1/projects/{id} | Eliminar con versión conocida y cascada; 204 | Bearer JWT |

El listado acepta `limit` (1–100), `offset` (0–100.000), `query` (substring literal del nombre, sin distinguir mayúsculas, hasta 120 caracteres) y `order` (`recent` por defecto, o `name`). Filtra/ordena en SQL antes de paginar. Los resúmenes incluyen `preview` opcional: longitud, prefijo de hasta 40 residuos, exclusiones, propiedades guardadas, procedencia sin secuencia, y longitudes/prefijos/parámetros/conteos de comparación. Resultados ausentes son `null`, no ceros. No descarga snapshots completos para la lista ni recalcula resultados. `pf_list_projects` y `pf_project_preview` son SECURITY INVOKER con permisos exclusivos de authenticated. [Migración nueva](../supabase/migrations/20261019000001_project_previews.sql).

Validación del token: la API consulta `GET {SUPABASE_URL}/auth/v1/user` con la `apikey` pública y el token del cliente. No se verifica la firma localmente ni se guarda ningún secreto de JWT; un token inválido o caducado devuelve 401 con `WWW-Authenticate: Bearer`. Si la respuesta de Supabase lista un factor con `status: "verified"` y la reclamación `aal` del token no es `aal2`, 401 `mfa-required` (`UNAUTHENTICATED` en RPC) con `WWW-Authenticate: Bearer error="insufficient_user_authentication"`; la reclamación se lee del JWT sin verificar la firma, solo después de que Supabase lo haya aceptado. Usuarios sin factores verificados no cambian; todavía no hay interfaz TOTP. Si Supabase no responde, 502 o 504 explícitos; si la API no tiene configuración de identidad, 503.

Errores en formato RFC 9457 (`application/problem+json`) con `type`, `title`, `status`, `detail`, `instance` y `requestId`. Cada respuesta lleva `X-Request-Id` (el cliente puede enviarlo para correlacionar), `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` y `Cache-Control: no-store` salvo que la ruta fije otra política; también las de CORS y los errores 500. Los errores de validación nombran campos, nunca valores: ninguna secuencia ni secreto aparece en una respuesta de error.

CORS limitado a `ALLOWED_ORIGINS` (lista separada por comas) o, en su defecto, `FRONTEND_ORIGIN`. Sin credenciales de cookie; el token viaja en `Authorization`.

## Previsto, sin implementar

Contrato, límites, SECURITY INVOKER/RLS, pruebas reales y pasos externos de proyectos en [persistencia](project-persistence.md). Respuestas no-store; la nueva consulta requiere la migración de vistas previas. La persistencia original ya está activada según el propietario.

| Método y ruta | Propósito | Autenticación prevista |
|---|---|---|
| /v1/reports | Implementado localmente arriba; pendiente de migración remota y publicación | Requerida |

`GET /v1/pdb/{id}/structure` y `GET /v1/catalog/domains` dejan de ser rutas necesarias: BinaryCIF público se descarga de RCSB y el catálogo se sirve como JSON estático. RF-17/RF-18 no tienen rutas hasta acordar persistencia de candidatos, consentimiento, validación de PDB y procedencia.

El cliente obtiene la URL de la API mediante `VITE_API_BASE_URL`. No emitir datos de otro propietario ni usar el consentimiento del asistente como autorización para enviar secuencias a predicción.

## gRPC

Contratos Protocol Buffers en `proto/proteinforge/`. `AccountService`, `ProjectService`, `ReferenceService` y `AssistantService` están servidos y reutilizan las mismas funciones de dominio, validación Pydantic y autorización que las rutas REST, que siguen activas. `DesignService` (`v1alpha1`) es un borrador sin implementar.

| Servicio | Métodos | Equivale a |
| --- | --- | --- |
| `proteinforge.account.v1.AccountService` | `GetStatus`, `GetMe` | `GET /v1/health`, `GET /v1/me` |
| `proteinforge.project.v1.ProjectService` | `ListProjects`, `GetProject`, `CreateProject`, `UpdateProject`, `DeleteProject` | `/v1/projects` |

`CreateProjectRequest.request_id` (campo 4, local, sin publicar): UUID generado por el cliente; vacío conserva el comportamiento anterior. La pareja (propietario, `request_id`) es única: un reintento tras perder la respuesta devuelve el proyecto existente sin tocar su snapshot, y la misma clave en otra cuenta crea otro proyecto. Un valor que no es UUID responde `INVALID_ARGUMENT` `validation: request_id`. Requiere [20261102000000_project_create_idempotency.sql](../supabase/migrations/20261102000000_project_create_idempotency.sql); sin esa migración, un create con clave falla (`FAILED_PRECONDITION`/503) y uno sin clave sigue funcionando, porque la API solo envía `p_create_request_id` cuando existe.
| `proteinforge.reference.v1.ReferenceService` | `SearchEntries`, `GetEntry` | `/v1/uniprot` |
| `proteinforge.assistant.v1.AssistantService` | `Interpret` | `POST /v1/assistant/interpret` |
| `proteinforge.folding.v1.FoldingService` (local, sin publicar) | `Predict` | `POST /v1/predictions/fold` |
| `proteinforge.report.v1.ReportService` (local, sin publicar) | `CreateReportUpload`, `CompleteReport`, `ListReports`, `CreateReportDownload`, `DeleteReport` | `/v1/reports` |

Rutas `POST /<paquete>.<Servicio>/<Método>` en la raíz (los clientes gRPC nativos no admiten prefijo). Cada servicio responde a tres protocolos según el `Content-Type`:

- **gRPC-Web** (`application/grpc-web+proto`): el que usa el navegador. Funciona sobre HTTP/1.1.
- **Connect** (`application/json` o `application/proto`): útil para depurar con `curl`.
- **gRPC nativo** (`application/grpc`): requiere HTTP/2 y trailers ASGI. Uvicorn no los ofrece; Hypercorn sí (`requirements-dev.txt`). En Vercel no se espera gRPC nativo; queda para el ejecutor del laboratorio de diseño.

El token se envía en `Authorization: Bearer` igual que en REST. Errores: 401→`UNAUTHENTICATED`, 403→`PERMISSION_DENIED`, 404→`NOT_FOUND`, 409→`ABORTED` (`project-conflict` o `project-requires-current-client`), 400/422→`INVALID_ARGUMENT` (`validation: <campos>`, sin valores enviados), 429→`RESOURCE_EXHAUSTED`, 502/503→`UNAVAILABLE`, 504→`DEADLINE_EXCEEDED`, esquema o identidad sin configurar→`FAILED_PRECONDITION`, cuerpo o compresión imposibles de decodificar (JSON truncado, tipos erróneos, protobuf o gzip corruptos)→`INVALID_ARGUMENT` `malformed-request` (HTTP 400 en Connect, sin el texto del parser), otro→`INTERNAL` (`internal`). El mensaje es siempre un código estable. Paginación con `page_token` opaco; un token no emitido por el servidor da `INVALID_ARGUMENT`. Mensajes de hasta 2 MB. CORS expone `Grpc-Status`, `Grpc-Message` y admite las cabeceras de Connect y gRPC-Web. Todas las respuestas llevan `X-Request-Id`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` y `Cache-Control: no-store` (salvo que la ruta fije otro).

Código generado en `app/rpc/gen/` (versionado; no editar). Regenerar tras cambiar un `.proto`:

`ListProjectsRequest` añade `query` y `ProjectOrder`; `ProjectSummary.preview` es el campo 6, opcional. `ListProjectsResponse.query_applied` permite detectar servidores antiguos que ignoran los nuevos campos: el cliente no presenta esa respuesta como una búsqueda/orden por nombre correctos. No cambia ningún número/tipo anterior. El token opaco conserva compatibilidad con el listado por defecto y, cuando hay consulta u orden por nombre, queda ligado a esos valores. Cambiar consulta/orden con un token anterior da `INVALID_ARGUMENT`. La búsqueda es literal: `%` y `_` no son comodines. La paginación sigue usando desplazamiento; cambios concurrentes de la colección pueden desplazar filas, por lo que el cliente deduplica al ampliar y reinicia al refrescar o escribir. No es un snapshot transaccional de varias páginas.

```bash
.venv/Scripts/python -m pip install -r requirements-dev.txt
PATH="$PWD/.venv/Scripts:$PATH" npx --yes @bufbuild/buf@1.73.0 lint
PATH="$PWD/.venv/Scripts:$PATH" npx --yes @bufbuild/buf@1.73.0 generate
```

Prueba de los tres protocolos contra un servidor en marcha: `PYTHONPATH=. .venv/Scripts/python scripts/rpc_smoke.py http://127.0.0.1:8000` (con Hypercorn para incluir gRPC nativo). Evaluación y decisiones: [docs/grpc.md del frontend](https://github.com/Jacklb19/ProteinForge/blob/dev/docs/grpc.md).
