# Historial del generador en Proyectos

Contrato del historial de búsquedas del generador de candidatos (RF-17 del frontend, [candidatos](https://github.com/Jacklb19/ProteinForge/blob/main/docs/candidates.md)) dentro de un proyecto. La API valida la forma del documento; no recalcula la ciencia del generador.

## Diseño

El historial es un **sobre dentro de la instantánea del proyecto**, igual que el laboratorio: un campo opcional `generatorHistory` del `workspace`, sin tablas, columnas ni políticas nuevas. Hereda propietario, RLS, concurrencia optimista y borrado en cascada. Se descartaron las alternativas de una fila `analyses` por ejecución y de una tabla nueva `generator_runs`, porque exigen funciones, políticas de versión y contratos propios sin que haga falta consultar ejecuciones entre proyectos.

Cada ejecución guarda el **texto exacto exportado** por el generador (`resultJson`), y su SHA-256 es el identificador. El cliente puede volver a descargar exactamente el mismo archivo, con el mismo hash, y un cambio futuro del generador no obliga a cambiar el protobuf. Una variante con mensajes protobuf estructurados duplicaría el esquema del generador en Zod, Pydantic, protobuf y SQL.

## Campos

Sobre en `Workspace` (análogo a `LaboratorySnapshot`):

| Campo | Contenido |
|---|---|
| `generatorHistory.schemaVersion` | `1`: versión del sobre |
| `generatorHistory.documentJson` | Documento JSON UTF-8 compacto, **≤ 100.000 bytes** |
| `generatorHistory.documentSha256` | SHA-256 hexadecimal en minúsculas de los bytes exactos de `documentJson` |

Documento `proteinforge-generator-history` v1, con claves exactas y sin campos extra:

| Campo | Contenido y validación |
|---|---|
| `kind` | `"proteinforge-generator-history"` |
| `schemaVersion` | `1` |
| `runs` | Lista de 1 a **10** ejecuciones, en orden de grabación y sin identificadores repetidos |
| `runs[].id` | SHA-256 de los bytes UTF-8 de `resultJson`: identificador y prueba de integridad. Dos ejecuciones idénticas tienen el mismo id y no se duplican |
| `runs[].origin` | `"local-generator"`, la única procedencia admitida |
| `runs[].recordedAt` | ISO 8601 con zona; es la hora del cliente. La hora del servidor es `updatedAt` del proyecto |
| `runs[].appBuild` | Commit del build que ejecutó la búsqueda (7 a 40 caracteres hexadecimales en minúsculas) o `null` |
| `runs[].resultJson` | Texto exacto exportado (**≤ 10.000 bytes**): `schemaVersion` del resultado (1), `methodVersion` (`proteinforge-candidates-v1`), `parameters` con `seed`, ambos brazos con sus métricas y candidatos en el orden emitido (`descriptors`, `loss` y `satisfies`) y `references` |
| `runs[].selection` | `null` o `{arm: "guided" \| "reference", index}`; el índice debe existir en ese brazo |

La semilla de la referencia aleatoria no se guarda aparte: se deriva de la semilla y de `methodVersion`.

## Validación

- **API (Pydantic) y SQL (defensa en profundidad, `pf_validate_generator_history`):** sobre, versiones, tamaño en bytes, hash del documento, claves exactas, número de ejecuciones, unicidad, `id = SHA-256(resultJson)`, fecha con zona, `origin`, que `resultJson` sea JSON con `schemaVersion = 1` y un `methodVersion` conocido, el alfabeto de 20 residuos y las longitudes de 5 a 300 de los candidatos, como máximo cinco candidatos por brazo y `selection` dentro de rango. No se recalculan descriptores, penalizaciones ni la búsqueda. Los errores son 422 / `INVALID_ARGUMENT` con nombres de campo, sin valores (`PT422`, «Invalid generator history»).
- **Cliente:** revalida con `searchParametersSchema` y repite la búsqueda de forma determinista solo bajo demanda, con una acción explícita por ejecución y un aviso si no coincide. Una discrepancia (por variaciones de coma flotante entre plataformas) se muestra como tal sin corregir los datos guardados.
- El hash prueba integridad de bytes, no autoría, función ni estabilidad.

## Límites

| Límite | Valor |
|---|---|
| Ejecución (`resultJson`) | ≤ 10.000 bytes |
| Documento de historial | ≤ 100.000 bytes y ≤ 10 ejecuciones |
| Documento del laboratorio | ≤ 1.000.000 bytes |
| Instantánea en SQL (`jsonb::text`) | ≤ 1.900.000 bytes |
| Escritura del proyecto (JSON REST) y mensaje RPC | ≤ 2.000.000 bytes |

El historial tiene un tope propio y no comparte los 1.000.000 bytes del laboratorio. Un laboratorio cercano a su máximo más un historial máximo puede superar el total: los límites totales siguen siendo la autoridad y no se amplían. Si se excede, el rechazo es explícito y atómico: el servidor no escribe nada (ni nombre, ni marca de tiempo, ni instantánea) y responde 422 con el nombre del campo. Nunca se trunca ni se descartan ejecuciones en silencio.

## Versiones y compatibilidad

Se necesita `workspace.schemaVersion = 3`, porque un cliente v2 que leyera un proyecto con historial lo perdería sin avisar y, al guardar, borraría el historial.

| Situación | Comportamiento |
|---|---|
| Cliente v1 o v2 abre un proyecto v3 | Su contrato solo acepta 1 y 2 y rechaza la apertura con un error explícito |
| Cliente v1 o v2 escribe sobre un proyecto v3 | La guardia **monótona** de SQL rechaza cualquier versión inferior a la guardada (`PT409`, «Project requires current client»), sin escritura parcial. La API responde 409 `project-requires-current-client` (REST) o `ABORTED` (RPC); la concurrencia sigue siendo `project-conflict` |
| Cliente v3 abre un proyecto v1 o v2 | Lectura normal; la ausencia de `generatorHistory` significa «sin historial» |
| Cliente v3 guarda | Versión = máximo entre la guardada y la mínima que exige el contenido (3 con historial, 2 con laboratorio, 1 si no). Conserva laboratorio e historial leídos al guardar desde cualquier herramienta |
| Servidor antiguo recibe v3 | Pydantic `Literal[1, 2]` y SQL `in ('1','2')` responden con un error de validación: no se pierde nada en silencio |

Un proyecto v3 no vuelve a v2 aunque se retiren todas sus ejecuciones. En v3 el laboratorio es opcional (v2 lo sigue exigiendo). La vista previa añade `generatorRuns` (0 a 10) y reconoce el laboratorio en v2 y v3, sin devolver documentos.

Protobuf: `Workspace.generator_history = 7` y `ProjectPreview.generator_runs = 9`, sin renumerar campos ni cambiar tipos; `buf breaking` contra `main` debe pasar. Ningún campo existente cambia de significado: `profileWindow`, `source`, `descriptors`, `alignment` y `laboratory` conservan contrato y validación, y `analyses.type = 'workspace-v1'` sigue siendo la clave de almacenamiento. La tabla `alignments` del esquema inicial queda fuera del contrato.

La migración es [20261027000000_generator_history.sql](../supabase/migrations/20261027000000_generator_history.sql). Orden de despliegue: migración → backend → repin y regeneración del frontend → frontend. El backend publicado funciona con o sin la migración: sin ella, el recuento de búsquedas vale cero y el SQL anterior rechaza las instantáneas v3, de modo que la interfaz permanece oculta (`VITE_GENERATOR_HISTORY`).

## Pruebas

- Contrato (Pydantic): sobre y documento válidos; claves extra o duplicadas; versión desconocida; hash incorrecto; id repetido; 0 y 11 ejecuciones; `resultJson` de 10.000 y 10.001 bytes; documento de 100.000 y 100.001 bytes; fecha sin zona; `origin` desconocido; candidatos con alfabeto o longitud fuera de rango; `selection` fuera de rango; NaN e Infinity rechazados.
- REST y RPC (Connect JSON, Connect binario y gRPC-Web): crear, abrir, actualizar y borrar con historial; errores 422, 409; un mensaje justo bajo 2.000.000 bytes aceptado y por encima rechazado; `buf lint` y `buf breaking`.
- SQL y RLS en PostgreSQL (`scripts/test-postgres.ps1` en local y el servicio de CI): un segundo propietario y `anon` no leen ni escriben historial ajeno; cascada; la guardia monótona rechaza v1 y v2 sobre v3 sin cambiar nombre ni marca de tiempo; dos escritores con un único ganador; rollback ante un historial inválido; validación SQL equivalente a Pydantic; vista previa con recuento y sin documento; la migración repetida no cambia filas.
