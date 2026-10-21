# Proyectos: contrato, pruebas y activación

## Laboratorio aprobado e implementado localmente — 21 oct 2026

Se conserva analyses.type=workspace-v1 como clave estable de almacenamiento; results y parameters registran schemaVersion 2 si llevan laboratorio. No hay tablas nuevas, Storage ni modelos en FastAPI. Sobre JSON/SHA-256 de hasta 1.000.000 bytes; fuente Base64 se comprueba sin descomprimir, estado tiene 1–3 borradores de 3–120 residuos, posiciones únicas y selección activa válida. Los datos científicos completos se revalidan en Worker al guardar y abrir. El editor de análisis y el laboratorio tienen vínculos independientes: cambiar de herramienta no reemplaza el otro borrador; conservar laboratory en actualizaciones de análisis evita pérdida. El campo activeSequence de análisis no se fuerza a coincidir con el borrador activo del laboratorio. La guardia de versión se comprueba bajo bloqueo antes de escribir; una actualización v1 de un proyecto v2 falla sin modificar nombre/timestamp/snapshot.

Migración nueva idempotente: [20261021000000_laboratory_projects.sql](../supabase/migrations/20261021000000_laboratory_projects.sql). Solo se ejecutó en PostgreSQL temporal localhost, con 12 pruebas reales: RLS/dos propietarios/anon, copia/rename/delete, tamaños/hash, rollback y carrera de versiones, más repetición de migración. API: 43 pruebas ejecutadas, 12 SQL omitidas en la batería sin base (se ejecutan aparte). No certifica Auth/PostgREST remoto.

Activación posterior, previa autorización específica: aplicar únicamente la migración nueva en la base ya migrada; publicar backend antes de frontend para que la referencia protobuf esté disponible; comprobar cuenta real, dos propietarios y dos pestañas. No repetir migraciones iniciales. Esta entrega no autoriza ni realiza esos pasos remotos.

19 oct 2026: CRUD/gRPC-Web publicados y migraciones originales aplicadas por el propietario. La prueba del propietario confirma CRUD y correo de confirmación. La nueva sección Proyectos requiere una migración adicional, preparada y probada únicamente en PostgreSQL local.

## Contrato

POST /v1/projects recibe name (1–120 caracteres sin controles), activeSequence (0–100.000 residuos normalizados) y workspace. PUT /v1/projects/{id} añade expectedUpdatedAt con fecha ISO 8601/zona/microsegundos originales. DELETE exige ese valor en query y responde 204. Propietario exclusivamente desde el JWT; campos extra rechazados.

workspace contiene schemaVersion=1, profileWindow=9/19, descriptors=null o {sequence,excluded,methodVersion:"proteinforge-descriptors-v1",result:{massDa,isoelectricPoint,instabilityIndex,aliphaticIndex,gravy,length}}, alignment={first,second,matrix,mode,result}. Comparación hasta 5.000 residuos por entrada; matrices BLOSUM45/62/80, global/local, apertura 10/extensión 0,5 y extremos globales gratuitos. Resultado con alignedFirst/alignedSecond/marks/score/identities/similarities/columns/parameters. Resultados pendientes/desactualizados se omiten; servidor valida asociación/dimensiones/parámetros, sin certificar ni recalcular valores científicos del cliente.

Creación/apertura/actualización devuelven {id,name,status,activeSequence,createdAt,updatedAt,workspace}, más preview opcional en el resumen RPC. GET /v1/projects recibe limit 1–100 (30 por defecto), offset 0–100.000, query y order; devuelve {items:[{id,name,status,createdAt,updatedAt,preview}],nextOffset}. No entrega snapshots completos en lista. La consulta SQL incluye solo prefijos de hasta 40 residuos, resultados vigentes, procedencia sin secuencia y resumen de comparación; los valores no calculados se conservan ausentes. Orden por actualización/id descendentes o por nombre en minúsculas/id ascendentes. La consulta de nombre es substring literal, sin comodines. Offset puede variar con escrituras concurrentes; cliente deduplica al ampliar y reinicia al actualizar/escribir.

projects almacena nombre/secuencia; analyses guarda un snapshot type=workspace-v1, índice único parcial, sin nuevas tablas. RPC pf_save_project bloquea/valida versión y actualiza proyecto/snapshot en una transacción. pf_delete_project aplica comparación y cascada; pf_project_document devuelve JSON. Todas SECURITY INVOKER, search_path vacío y grants authenticated; API reenvía JWT y clave pública, nunca service_role. UUIDv7 provisional en PostgreSQL compatible con Python 3.13 local.

409 conserva versión/borrador; opciones de copia o recuperación explícita. 404 para ajeno/ausente. 503 projects-schema-unavailable para tablas/RPC pendientes. Cache-Control:no-store. Sin guardado automático ni sincronización offline; localStorage guarda solo último ID por usuario. Timeout puede dejar confirmación incierta: actualizar lista antes de repetir creación. Eliminar conserva borrador en memoria.

Referencias: [RLS Supabase](https://supabase.com/docs/guides/database/postgres/row-level-security), [RPC PostgREST](https://docs.postgrest.org/en/stable/references/api/functions.html), [errores PostgREST](https://docs.postgrest.org/en/stable/references/errors.html).

## Reproducir

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -v
./scripts/test-postgres.ps1
```

Primera batería: contratos HTTP/identidad/validación/JWT/versiones/404/409 con transporte simulado; seis tests SQL se omiten sin base configurada. Segundo comando: cluster propio tmp/pg-UUID, solo 127.0.0.1:55439, bootstrap y ambas migraciones, seis tests reales y apagado en finally. Opciones -PostgresBin/-Port. Cluster/log ignorados conservados para diagnóstico; no lee .env ni usa bases remotas/existentes.

Comprobado: PostgreSQL 18.0 y Python 3.13, RLS entre propietarios/falsificación/transferencia/hijos/anon/caché, CRUD/cascada, rollback y dos escritores con un ganador. Bootstrap emula auth.users/roles/auth.uid; políticas/RPC son migraciones reales. No equivale a JWT/PostgREST/Supabase remoto. Python 3.14 y PostgreSQL 15/16 no ejecutados.

## Pasos externos del usuario

### Entrega de Proyectos — migración adicional

Las migraciones 20260928, 20261012 y backfill 20261019 ya fueron aplicadas por el propietario. **No volver a ejecutar la inicial.** Para esta entrega, revisar y aplicar únicamente [20261019000001_project_previews.sql](../supabase/migrations/20261019000001_project_previews.sql) desde el SQL Editor de su Supabase. Es idempotente: crea/reemplaza dos funciones, fija grants y notifica recarga de PostgREST; no cambia filas ni políticas RLS de tablas.

Comprobado en PostgreSQL 18 temporal: nueve pruebas, incluidas separación de propietarios/anónimo, consulta antes de paginar, prefijo acotado, substring literal `%_`, omisión de resultados desactualizados, conservación de conteos/ceros, repetición de la migración y concurrencia original. Auth.uid se emula; esto no certifica PostgREST remoto. Si falta la migración, la API nueva responde `projects-schema-unavailable` en lugar de descargar todos los proyectos como alternativa.

Orden previsto: aplicar esa migración → publicar backend `dev` revisado → publicar frontend `dev` con su referencia de contrato. La publicación no está ejecutada por esta entrega; main/producción requieren decisión expresa del propietario. Después, comprobar Proyectos con su cuenta y el aislamiento entre dos cuentas propias. Copiar no reemplaza el borrador activo ni su puntero. Renombrar/borrar conservan la versión exacta y `ABORTED`/409.

### Procedimiento original (histórico)

1. Revisar esquema/historial Supabase y respaldar datos existentes. No repetir migración inicial sobre una base ya migrada.
2. Autorizar/aplicar pendientes en orden: 20260928000000_initial_schema.sql y 20261012000000_project_transactions.sql. Segunda: grants, índice snapshot, timestamp monotónico y RPC; notifica recarga PostgREST.
3. Publicar commits elegidos de ambos repositorios. Conservar CORS/CSP/variables existentes; no introducir service_role.
4. Dos cuentas propias: CRUD e ID de A inaccesible a B (404). Dos pestañas: segundo guardado 409 y borrador conservado.
5. Recargar: secuencia/ventana/comparación restauradas, propiedades recalculadas en Worker. Sin red: cálculo/CSV locales, guardado con error explícito.

No se promete disponibilidad remota, secuencias offline ni sincronización automática.

Lectura pública expresamente autorizada el 12 oct, 18:20 UTC: las cinco tablas previstas continúan devolviendo 404/PGRST205. Google/correo y registro habilitados; confirmación requerida. No se verificó SMTP, sesión real ni configuración privada. [Resultado y alcance](supabase-public-status.md). No se aplicaron migraciones.
