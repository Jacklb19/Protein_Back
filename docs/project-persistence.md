# Proyectos: contrato, pruebas y activación

12 oct 2026: CRUD local en FastAPI/frontend. Sin publicación ni migraciones remotas; la última inspección remota no encontraba las tablas.

## Contrato

POST /v1/projects recibe name (1–120 caracteres sin controles), activeSequence (0–100.000 residuos normalizados) y workspace. PUT /v1/projects/{id} añade expectedUpdatedAt con fecha ISO 8601/zona/microsegundos originales. DELETE exige ese valor en query y responde 204. Propietario exclusivamente desde el JWT; campos extra rechazados.

workspace contiene schemaVersion=1, profileWindow=9/19, descriptors=null o {sequence,excluded,methodVersion:"proteinforge-descriptors-v1",result:{massDa,isoelectricPoint,instabilityIndex,aliphaticIndex,gravy,length}}, alignment={first,second,matrix,mode,result}. Comparación hasta 5.000 residuos por entrada; matrices BLOSUM45/62/80, global/local, apertura 10/extensión 0,5 y extremos globales gratuitos. Resultado con alignedFirst/alignedSecond/marks/score/identities/similarities/columns/parameters. Resultados pendientes/desactualizados se omiten; servidor valida asociación/dimensiones/parámetros, sin certificar ni recalcular valores científicos del cliente.

Creación/apertura/actualización devuelven {id,name,status,activeSequence,createdAt,updatedAt,workspace}. GET /v1/projects recibe limit 1–100 (30 por defecto), offset 0–100.000 y devuelve {items:[{id,name,status,createdAt,updatedAt}],nextOffset}. No entrega secuencias en lista. Orden por updated_at e ID descendentes; offset puede variar con escrituras concurrentes, cliente deduplica al ampliar y reinicia al actualizar.

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

1. Revisar esquema/historial Supabase y respaldar datos existentes. No repetir migración inicial sobre una base ya migrada.
2. Autorizar/aplicar pendientes en orden: 20260928000000_initial_schema.sql y 20261012000000_project_transactions.sql. Segunda: grants, índice snapshot, timestamp monotónico y RPC; notifica recarga PostgREST.
3. Publicar commits elegidos de ambos repositorios. Conservar CORS/CSP/variables existentes; no introducir service_role.
4. Dos cuentas propias: CRUD e ID de A inaccesible a B (404). Dos pestañas: segundo guardado 409 y borrador conservado.
5. Recargar: secuencia/ventana/comparación restauradas, propiedades recalculadas en Worker. Sin red: cálculo/CSV locales, guardado con error explícito.

No se promete disponibilidad remota, secuencias offline ni sincronización automática.
