# Proyectos: contrato, persistencia y pruebas

Un proyecto es un nombre, una secuencia activa y una instantánea (`workspace`) con los resultados que el cliente quiere conservar. Este documento describe el contrato, el almacenamiento, las restricciones de tabla y cómo reproducir las pruebas. Las rutas y los códigos de error están en [API](api.md).

## Contrato

- `POST /v1/projects` recibe `name` (1 a 120 caracteres sin controles), `activeSequence` (0 a 100.000 residuos normalizados) y `workspace`. `PUT /v1/projects/{id}` añade `expectedUpdatedAt`, con la fecha ISO 8601, zona y microsegundos originales. `DELETE` exige ese valor en la consulta y responde 204. El propietario sale exclusivamente del JWT y los campos extra se rechazan.
- `workspace` (versión 1) contiene `schemaVersion`, `profileWindow` (9 o 19), `descriptors` (`null` o `{sequence, excluded, methodVersion: "proteinforge-descriptors-v1", result: {massDa, isoelectricPoint, instabilityIndex, aliphaticIndex, gravy, length}}`) y `alignment` (`{first, second, matrix, mode, result}`). La comparación admite hasta 5.000 residuos por entrada, matrices BLOSUM45/62/80, modo global o local, apertura 10, extensión 0,5 y extremos globales gratuitos; el resultado lleva `alignedFirst`, `alignedSecond`, `marks`, `score`, `identities`, `similarities`, `columns` y `parameters`. Los resultados pendientes o desactualizados se omiten. El servidor valida la asociación, las dimensiones y los parámetros, pero no recalcula ni certifica los valores científicos del cliente.
- Las versiones posteriores añaden `laboratory` (v2), `generatorHistory` (v3, ver [historial del generador](generator-history.md)) y las predicciones del laboratorio (v4, ver [predicción](folding.md)). La guardia monótona de SQL rechaza guardar una versión inferior a la almacenada.
- Crear, abrir y actualizar devuelven `{id, name, status, activeSequence, createdAt, updatedAt, workspace}`; el resumen de RPC añade un `preview` opcional.
- El listado (`limit` 1 a 100, 30 por omisión; `offset` 0 a 100.000; `query` y `order`) devuelve `{items: [{id, name, status, createdAt, updatedAt, preview}], nextOffset}` y no entrega instantáneas completas. La consulta SQL incluye solo prefijos de hasta 40 residuos, resultados vigentes, procedencia sin secuencia y un resumen de la comparación; los valores no calculados se conservan ausentes. El orden es por actualización e id descendentes, o por nombre en minúsculas e id ascendentes. La consulta de nombre es una subcadena literal, sin comodines. El desplazamiento puede variar con escrituras concurrentes, así que el cliente deduplica al ampliar y reinicia al actualizar o escribir.
- `409` conserva la versión y el borrador y deja al cliente la elección de copiar o recuperar. `404` para un proyecto ajeno o ausente. `503 projects-schema-unavailable` si faltan tablas o funciones: la API no descarga todos los proyectos como alternativa. `Cache-Control: no-store`.

## Almacenamiento

- `projects` guarda el nombre y la secuencia; `analyses` guarda una instantánea `type = 'workspace-v1'` (clave estable) con un índice único parcial. No hay tablas nuevas por versión de contrato: laboratorio, historial y predicciones viven dentro de la misma instantánea (`results.schemaVersion` 1 a 4).
- `pf_save_project` bloquea el proyecto, valida la versión y actualiza proyecto e instantánea en una transacción; `pf_delete_project` aplica la comparación de versión y el borrado en cascada; `pf_project_document` devuelve el JSON. Todas son `SECURITY INVOKER`, con `search_path` vacío y permisos solo para `authenticated`. La API reenvía el JWT y la clave pública, nunca `service_role`.
- `pf_save_project` acepta las versiones 1 a 4, conserva el límite de 1.900.000 bytes y valida el laboratorio (`PT422` ante un documento inválido, `PT409` ante una versión inferior). Una actualización v1 sobre un proyecto v2 falla sin modificar nombre, marca de tiempo ni instantánea.
- **Creación idempotente (RF-15).** La migración `20261102000000_project_create_idempotency.sql` añade `projects.create_request_id uuid null`, un índice único parcial `(owner_id, create_request_id) where create_request_id is not null` y `pf_save_project(..., p_create_request_id uuid default null)`. Con una pareja repetida, `insert … on conflict do nothing` no inserta y la función devuelve el documento actual del proyecto existente sin reescribir nombre ni instantánea; dos creaciones simultáneas con la misma clave devuelven el mismo proyecto. Una actualización con clave es `PT422`. Se elimina la firma de cinco argumentos para no dejar una sobrecarga ambigua en PostgREST, y las llamadas con cinco argumentos siguen funcionando por el valor por omisión. En REST, `requestId` solo se admite en `POST`; en gRPC es `CreateProjectRequest.request_id` (campo 4). Borrar el proyecto libera la clave.
- **Vistas previas.** `20261019000001_project_previews.sql` (idempotente) crea `pf_list_projects` y `pf_project_preview`, fija permisos y notifica la recarga de PostgREST; no cambia filas ni políticas.

### Restricciones de tabla

`authenticated` conserva `insert` y `update` sobre `projects` y `analyses` (migración `20261012000000`), porque las funciones `pf_*` son `SECURITY INVOKER` y escriben con esos permisos; el frontend no escribe tablas directamente. Para que una escritura directa por PostgREST no evite la validación de la API, la migración [20261102000001_table_write_constraints.sql](../supabase/migrations/20261102000001_table_write_constraints.sql) añade:

- `projects`: nombre de 1 a 120 caracteres, no vacío tras `btrim` y sin caracteres de control; `active_sequence` de hasta 100.000 residuos del alfabeto de la API; `status` solo `draft`.
- `analyses`: solo `type = 'workspace-v1'`; `results` objeto con `schemaVersion` numérico de 1 a 4 y hasta 1.900.000 bytes; `parameters` igual a `{"schemaVersion": …}` de la instantánea.
- `alignments`: ningún código la usa, por lo que se revocan `insert` y `update` a `authenticated` (conserva `select` y `delete`).

Las restricciones son `NOT VALID`: no revisan las filas existentes, de modo que aplicarlas no falla por datos antiguos, pero toda escritura nueva las cumple. Tras comprobar los datos se puede ejecutar `alter table … validate constraint …`. La migración es idempotente. La validación profunda de la instantánea (laboratorio, historial, predicción) sigue en la API y en `pf_save_project`. Los valores se comprueban contra el código en `tests/test_migrations.py` y el comportamiento en `tests/test_postgres.py`.

## Migraciones

Se aplican en orden de nombre sobre la base de Supabase (SQL Editor o CLI); cada una es idempotente y no debe repetirse la inicial sobre una base ya migrada. Los scripts de arranque de las pruebas aplican todas las migraciones en orden.

| Migración | Contenido |
|---|---|
| `20260928000000_initial_schema` | Esquema inicial, RLS, UUIDv7 provisional |
| `20261012000000_project_transactions` | Permisos, índice de instantánea, marca de tiempo monótona y funciones `pf_*` |
| `20261019000000_backfill_profiles` | Perfiles de cuentas existentes |
| `20261019000001_project_previews` | Vistas previas y búsqueda del listado |
| `20261021000000_laboratory_projects` | Laboratorio dentro de la instantánea (v2) |
| `20261027000000_generator_history` | Historial del generador (v3) y guardia monótona |
| `20261030000000_structure_predictions` | Predicciones del laboratorio (v4) |
| `20261101000000_report_storage` | Informes: tabla, bucket y cuota |
| `20261102000000_project_create_idempotency` | Creación idempotente |
| `20261102000001_table_write_constraints` | Restricciones de tabla |
| `20261110000000_project_versions` | Versiones de proyecto |
| `20261111000000_project_shares` | Enlaces de solo lectura |
| `20261113000000_project_quota` | Cuota de proyectos por cuenta |
| `20261113000001_shared_budget` | Presupuesto compartido |
| `20261113000002_mfa_policies` | MFA en RLS |
| `20261114000000_share_duration` | Duración de los enlaces |
| `20261114000001_version_changes` | Comparación de versiones |

Orden de despliegue de un cambio de contrato: migración → backend → repin y regeneración del frontend → frontend. Si falta una migración, la API responde `projects-schema-unavailable` en lugar de degradarse.

## Reproducir las pruebas

```bash
.venv/Scripts/python.exe -m unittest discover -s tests -v   # POSIX: .venv/bin/python
./scripts/test-postgres.ps1                                  # PostgreSQL temporal (PowerShell)
```

La primera batería cubre los contratos HTTP, identidad, validación, JWT, versiones, 404 y 409 con transporte simulado; las pruebas SQL se omiten si no hay una base configurada. `test-postgres.ps1` crea un clúster propio en `tmp/`, escucha solo en `127.0.0.1`, aplica el arranque y todas las migraciones, ejecuta las pruebas reales y lo apaga al terminar; admite `-PostgresBin` y `-Port`. No lee `.env` ni usa bases remotas o existentes. Con otro sistema operativo, CI muestra la alternativa: un servicio `postgres:18` y las variables `PF_TEST_DATABASE_URL` y `PF_PSQL`, con `tests/postgres_bootstrap.sql` aplicado primero (solo se aceptan bases en loopback llamadas `pf_security_test`).

Las pruebas SQL cubren RLS entre propietarios, falsificación y transferencia, hijos, `anon`, caché externa, CRUD y cascada, rollback, dos escritores con un único ganador, la idempotencia de la creación, la validación del historial y las restricciones de tabla. El arranque emula `auth.users`, los roles y `auth.uid()`; las políticas y funciones son las migraciones reales. No equivale a probar JWT, PostgREST ni Supabase remotos.

Referencias: [RLS en Supabase](https://supabase.com/docs/guides/database/postgres/row-level-security), [funciones RPC de PostgREST](https://docs.postgrest.org/en/stable/references/api/functions.html) y [errores de PostgREST](https://docs.postgrest.org/en/stable/references/errors.html).
