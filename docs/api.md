# Contratos de API

Actualizado el 12 de octubre de 2026. OpenAPI generado por FastAPI (`/docs` y `/openapi.json` en local) es la referencia ejecutable de lo implementado. Proyectos implementados localmente; activación de base remota pendiente.

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

Validación del token: la API consulta `GET {SUPABASE_URL}/auth/v1/user` con la `apikey` pública y el token del cliente. No se verifica la firma localmente ni se guarda ningún secreto de JWT; un token inválido o caducado devuelve 401 con `WWW-Authenticate: Bearer`. Si Supabase no responde, 502 o 504 explícitos; si la API no tiene configuración de identidad, 503.

Errores en formato RFC 9457 (`application/problem+json`) con `type`, `title`, `status`, `detail`, `instance` y `requestId`. Cada respuesta lleva `X-Request-Id`; el cliente puede enviarlo para correlacionar. Los errores de validación nombran campos, nunca valores: ninguna secuencia ni secreto aparece en una respuesta de error.

CORS limitado a `ALLOWED_ORIGINS` (lista separada por comas) o, en su defecto, `FRONTEND_ORIGIN`. Sin credenciales de cookie; el token viaja en `Authorization`.

## Previsto, sin implementar

Contrato, límites, SECURITY INVOKER/RLS, pruebas reales y pasos externos de proyectos en [persistencia](project-persistence.md). Respuestas no-store; guardar requiere las dos migraciones y publicación autorizadas por el usuario.

| Método y ruta | Propósito | Autenticación prevista |
|---|---|---|
| POST /v1/assistant/interpret | Interpretar contexto ya calculado | Requerida |
| POST /v1/reports | Registrar informe generado en cliente | Requerida |

`GET /v1/pdb/{id}/structure` y `GET /v1/catalog/domains` dejan de ser rutas necesarias: BinaryCIF público se descarga de RCSB y el catálogo se sirve como JSON estático. RF-17/RF-18 no tienen rutas hasta acordar persistencia de candidatos, consentimiento, validación de PDB y procedencia.

El cliente obtiene la URL de la API mediante `VITE_API_BASE_URL`. No emitir datos de otro propietario ni usar el consentimiento del asistente como autorización para enviar secuencias a predicción.
