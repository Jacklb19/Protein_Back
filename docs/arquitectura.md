# Arquitectura del backend

## Estado

API local: health/me, CRUD proyectos y UniProt autenticado, CORS/correlación/problemas RFC 9457. JWT de usuario/SECURITY INVOKER sobre projects/analyses. Nueva migración transacciones/versiones/grants/índices sin nuevas tablas. RLS/concurrencia PostgreSQL 18 comprobados; migración/publicación y sesión Supabase real pendientes. [Contratos](api.md), [persistencia](project-persistence.md), [UniProt](uniprot.md).

## Responsabilidades

- FastAPI (0.142.4, uvicorn 0.54.0, httpx 0.28.1 y python-dotenv 1.2.4 fijados en `requirements.txt`): validar entradas, identidad y propiedad de recursos; responder JSON/OpenAPI según contratos implementados. Configuración del `.env` propio con prioridad de variables del proceso, sin alterar `os.environ`. Python 3.14 es el objetivo de despliegue; la base se ha ejecutado localmente con 3.13.
- Supabase Auth/PostgreSQL/Storage: identidad, proyectos y archivos. Esquema en `supabase/migrations/`.
- Proveedores externos: UniProt y PDBe/SIFTS para anotación/mapeo, Groq para interpretación y ESM Atlas/ESMFold para predicción de un candidato seleccionado.
- Cliente React independiente: cálculo científico/generación guiada/visualización/informes locales. Backend no implementa algoritmos del navegador.

## Datos existentes

Se conservan profiles/projects/analyses/alignments/external_cache. Migración posterior reemplaza helper UUIDv7 provisional con timestamp/bits/gen_random_uuid SQL, sin exigir Python 3.14 local; profiles.id coincide con Auth. Snapshot workspace-v1 en analyses incluye fuente opcional exacta y resultados asociados. Cambios en migraciones nuevas; no modifican por sí solos la base remota.

Guardar candidatos, parámetros de búsqueda, consentimiento y predicciones necesita diseño adicional. No hay tablas, campos o endpoints nuevos aprobados para esas capacidades todavía.

## Seguridad e integración

Secretos solo en variables del servidor. JWT y autorización por recurso; CORS a orígenes configurados. RLS de tablas de usuario y acceso exclusivo del servidor a caché externa. TOTP opcional con aal2 cuando se active, aún pendiente.

La predicción envía secuencia completa tras consentimiento. El asistente recibe solo descriptores/anotación/parámetros. Retención, cuotas y términos se verifican al integrar cada proveedor. No registrar secuencias privadas o secretos en logs.

Concurrencia optimista PUT/DELETE: timestamp con microsegundos, bloqueo y conflicto 409. PDF local implementado en frontend; subida por URL firmada/registro todavía previstos, sin endpoints activos. Consultar límites al integrar archivos/proveedores.

## Despliegue y operación

Vercel independiente del frontend, Supabase Free y costo cero sin tarjeta. Sin GPU local permanente. La predicción la ejecuta un proveedor externo; caché y errores mantienen la aplicación usable si ese proveedor no responde. El resultado guardado de respaldo debe identificarse como tal.

La entrega autónoma actual no autoriza cambios remotos ni publicación. main conserva versión estable; commits locales en dev. Configuración FastAPI/Vercel existente; activar migraciones y verificar sesión real requiere acción posterior del usuario.

## Pruebas

unittest: 26 pruebas HTTP/contratos/RLS estático pasan con transporte simulado; seis SQL omitidas sin base configurada. scripts/test-postgres.ps1 ejecutó las seis con PostgreSQL 18 temporal real y Auth.uid emulado: propietario/anónimo/transferencia/cascada/rollback/concurrencia. No certifica JWT/PostgREST remoto. Entorno local Python 3.13; objetivo 3.14 aún sin ejecutar. Adaptador UniProt probado adicionalmente con datos públicos reales, sin credenciales.
