# Contratos previstos de API

No hay endpoints implementados. Esta tabla conserva el diseño previamente aprobado y sus correcciones; OpenAPI será la referencia ejecutable al implementar FastAPI. Los payloads, errores y tipos exactos deben definirse en esa tarea, no inferirse durante la limpieza.

| Método y ruta | Propósito | Autenticación prevista |
|---|---|---|
| GET /v1/health | Estado del servicio | Pública |
| GET /v1/uniprot/search | Búsqueda por texto o identificador | Requerida |
| GET /v1/uniprot/{acc} | Anotación de una entrada | Requerida |
| POST /v1/projects | Crear proyecto | Requerida |
| GET /v1/projects | Listar proyectos propios | Requerida |
| GET /v1/projects/{id} | Abrir proyecto propio | Requerida |
| PUT /v1/projects/{id} | Actualizar con concurrencia optimista | Requerida |
| DELETE /v1/projects/{id} | Eliminar proyecto propio | Requerida |
| POST /v1/assistant/interpret | Interpretar contexto ya calculado | Requerida |
| POST /v1/reports | Registrar informe generado en cliente | Requerida |

`GET /v1/pdb/{id}/structure` y `GET /v1/catalog/domains` dejan de ser rutas necesarias: BinaryCIF público se descarga de RCSB y catálogo se sirve como JSON estático. El backend deberá resolver consultas/mapeo PDBe cuando se diseñe ese flujo; su contrato exacto aún no está definido.

RF-17/RF-18 amplían el producto con candidatos/predicción. No se crean rutas ficticias para esos flujos. Antes de implementarlos acordar cómo guardar candidatos, solicitar predicción, conservar consentimiento, validar PDB y registrar procedencia/confianza.

El cliente obtiene la URL API mediante `VITE_API_BASE_URL`. El backend limita CORS a orígenes configurados, valida JWT y devuelve errores explícitos; el diseño previo pide RFC 9457 y correlación para errores del servidor. No emitir datos de otro propietario ni usar el consentimiento del asistente como autorización para enviar secuencias a predicción.
