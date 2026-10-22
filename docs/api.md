# Contratos de API

## Laboratorio en Proyectos — 21 oct 2026

Workspace acepta schemaVersion 1 (anterior) y 2 con laboratory={schemaVersion:1,documentJson,documentSha256}. Documento portable proteinforge-laboratory v1, hasta 1.000.000 bytes UTF-8, tres variantes/120 residuos; hash exacto, fuente y borradores validados sin descomprimir ZIP ni ejecutar ciencia. La secuencia activa coherente pertenece al state del laboratorio; activeSequence del proyecto sigue siendo el documento independiente del editor de análisis. REST y gRPC-Web usan la misma validación. Preview añade hasLaboratory/laboratoryVariants sin documento; protobuf añade Workspace.laboratory=6 y preview campos 7/8 sin renumerar. Mensaje RPC completo máximo 2.000.000 bytes, sin ampliar el límite. SQL rechaza v1 sobre un snapshot v2 con PT409/ABORTED; PT422 se traduce a validación. Nueva migración 20261021000000_laboratory_projects.sql comprobada en PostgreSQL 18 local y aplicada por el propietario según su confirmación del 21 oct; sin inspección remota. El código nuevo sigue pendiente de autorización para publicarse. No volver a ejecutar las migraciones.

Actualizado el 19 de octubre de 2026. OpenAPI generado por FastAPI (`/docs` y `/openapi.json` en local) es la referencia ejecutable de REST. CRUD/gRPC-Web publicados previamente. Esta entrega añade vistas previas y consulta global; requiere la nueva migración antes de publicar el backend.

## Implementado

También implementados GET /v1/uniprot/search y GET /v1/uniprot/{accession}; ver [contrato y evidencia](uniprot.md). workspace.source opcional conserva procedencia ligada a la secuencia exacta.

| Método y ruta | Propósito | Autenticación |
|---|---|---|
| GET /v1/health | Estado del proceso, versión y si la identidad está configurada | Pública |
| GET /v1/me | Identidad del portador del token: `id` (coincide con `profiles.id`) y `email` | Bearer JWT de Supabase |
| POST /v1/projects | Crear proyecto/snapshot transaccional; 201 | Bearer JWT |
| GET /v1/projects | Listar propios sin secuencias, paginados | Bearer JWT |
| GET /v1/projects/{id} | Abrir propio; ajeno/ausente: 404 | Bearer JWT |
| PUT /v1/projects/{id} | Guardar o renombrar con versión conocida; 409 | Bearer JWT |
| DELETE /v1/projects/{id} | Eliminar con versión conocida y cascada; 204 | Bearer JWT |

El listado acepta `limit` (1–100), `offset` (0–100.000), `query` (substring literal del nombre, sin distinguir mayúsculas, hasta 120 caracteres) y `order` (`recent` por defecto, o `name`). Filtra/ordena en SQL antes de paginar. Los resúmenes incluyen `preview` opcional: longitud, prefijo de hasta 40 residuos, exclusiones, propiedades guardadas, procedencia sin secuencia, y longitudes/prefijos/parámetros/conteos de comparación. Resultados ausentes son `null`, no ceros. No descarga snapshots completos para la lista ni recalcula resultados. `pf_list_projects` y `pf_project_preview` son SECURITY INVOKER con permisos exclusivos de authenticated. [Migración nueva](../supabase/migrations/20261019000001_project_previews.sql).

Validación del token: la API consulta `GET {SUPABASE_URL}/auth/v1/user` con la `apikey` pública y el token del cliente. No se verifica la firma localmente ni se guarda ningún secreto de JWT; un token inválido o caducado devuelve 401 con `WWW-Authenticate: Bearer`. Si Supabase no responde, 502 o 504 explícitos; si la API no tiene configuración de identidad, 503.

Errores en formato RFC 9457 (`application/problem+json`) con `type`, `title`, `status`, `detail`, `instance` y `requestId`. Cada respuesta lleva `X-Request-Id`; el cliente puede enviarlo para correlacionar. Los errores de validación nombran campos, nunca valores: ninguna secuencia ni secreto aparece en una respuesta de error.

CORS limitado a `ALLOWED_ORIGINS` (lista separada por comas) o, en su defecto, `FRONTEND_ORIGIN`. Sin credenciales de cookie; el token viaja en `Authorization`.

## Previsto, sin implementar

Contrato, límites, SECURITY INVOKER/RLS, pruebas reales y pasos externos de proyectos en [persistencia](project-persistence.md). Respuestas no-store; la nueva consulta requiere la migración de vistas previas. La persistencia original ya está activada según el propietario.

| Método y ruta | Propósito | Autenticación prevista |
|---|---|---|
| POST /v1/assistant/interpret | Interpretar contexto ya calculado | Requerida |
| POST /v1/reports | Registrar informe generado en cliente | Requerida |

`GET /v1/pdb/{id}/structure` y `GET /v1/catalog/domains` dejan de ser rutas necesarias: BinaryCIF público se descarga de RCSB y el catálogo se sirve como JSON estático. RF-17/RF-18 no tienen rutas hasta acordar persistencia de candidatos, consentimiento, validación de PDB y procedencia.

El cliente obtiene la URL de la API mediante `VITE_API_BASE_URL`. No emitir datos de otro propietario ni usar el consentimiento del asistente como autorización para enviar secuencias a predicción.

## gRPC

Contratos Protocol Buffers en `proto/proteinforge/`. `AccountService`, `ProjectService` y `ReferenceService` están servidos y reutilizan las mismas funciones de dominio, validación Pydantic y autorización que las rutas REST, que siguen activas. `DesignService` (`v1alpha1`) es un borrador sin implementar.

| Servicio | Métodos | Equivale a |
| --- | --- | --- |
| `proteinforge.account.v1.AccountService` | `GetStatus`, `GetMe` | `GET /v1/health`, `GET /v1/me` |
| `proteinforge.project.v1.ProjectService` | `ListProjects`, `GetProject`, `CreateProject`, `UpdateProject`, `DeleteProject` | `/v1/projects` |
| `proteinforge.reference.v1.ReferenceService` | `SearchEntries`, `GetEntry` | `/v1/uniprot` |

Rutas `POST /<paquete>.<Servicio>/<Método>` en la raíz (los clientes gRPC nativos no admiten prefijo). Cada servicio responde a tres protocolos según el `Content-Type`:

- **gRPC-Web** (`application/grpc-web+proto`): el que usa el navegador. Funciona sobre HTTP/1.1.
- **Connect** (`application/json` o `application/proto`): útil para depurar con `curl`.
- **gRPC nativo** (`application/grpc`): requiere HTTP/2 y trailers ASGI. Uvicorn no los ofrece; Hypercorn sí (`requirements-dev.txt`). En Vercel no se espera gRPC nativo; queda para el ejecutor del laboratorio de diseño.

El token se envía en `Authorization: Bearer` igual que en REST. Errores: 401→`UNAUTHENTICATED`, 403→`PERMISSION_DENIED`, 404→`NOT_FOUND`, 409→`ABORTED`, 400/422→`INVALID_ARGUMENT` (`validation: <campos>`, sin valores enviados), 429→`RESOURCE_EXHAUSTED`, 502/503→`UNAVAILABLE`, 504→`DEADLINE_EXCEEDED`, esquema o identidad sin configurar→`FAILED_PRECONDITION`, otro→`INTERNAL` (`internal`). El mensaje es siempre un código estable. Paginación con `page_token` opaco; un token no emitido por el servidor da `INVALID_ARGUMENT`. Mensajes de hasta 2 MB. CORS expone `Grpc-Status`, `Grpc-Message` y admite las cabeceras de Connect y gRPC-Web. Todas las respuestas llevan `X-Request-Id` y `Cache-Control: no-store`.

Código generado en `app/rpc/gen/` (versionado; no editar). Regenerar tras cambiar un `.proto`:

`ListProjectsRequest` añade `query` y `ProjectOrder`; `ProjectSummary.preview` es el campo 6, opcional. `ListProjectsResponse.query_applied` permite detectar servidores antiguos que ignoran los nuevos campos: el cliente no presenta esa respuesta como una búsqueda/orden por nombre correctos. No cambia ningún número/tipo anterior. El token opaco conserva compatibilidad con el listado por defecto y, cuando hay consulta u orden por nombre, queda ligado a esos valores. Cambiar consulta/orden con un token anterior da `INVALID_ARGUMENT`. La búsqueda es literal: `%` y `_` no son comodines. La paginación sigue usando desplazamiento; cambios concurrentes de la colección pueden desplazar filas, por lo que el cliente deduplica al ampliar y reinicia al refrescar o escribir. No es un snapshot transaccional de varias páginas.

```bash
.venv/Scripts/python -m pip install -r requirements-dev.txt
PATH="$PWD/.venv/Scripts:$PATH" npx --yes @bufbuild/buf@1.73.0 lint
PATH="$PWD/.venv/Scripts:$PATH" npx --yes @bufbuild/buf@1.73.0 generate
```

Prueba de los tres protocolos contra un servidor en marcha: `PYTHONPATH=. .venv/Scripts/python scripts/rpc_smoke.py http://127.0.0.1:8000` (con Hypercorn para incluir gRPC nativo). Evaluación y decisiones: [docs/grpc.md del frontend](https://github.com/Jacklb19/ProteinForge/blob/dev/docs/grpc.md).
