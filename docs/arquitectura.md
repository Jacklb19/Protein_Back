# Arquitectura del backend

## Estado

API local en app: health/me y CRUD de proyectos, identidad Supabase, CORS limitado, correlación y problemas RFC 9457. Persistencia mediante JWT de usuario y RPC SECURITY INVOKER sobre projects/analyses; nueva migración de transacciones/versiones/grants/índices sin nuevas tablas. RLS/concurrencia en PostgreSQL 18 temporal comprobados. Activación remota y publicación pendientes. Ver [contratos](api.md) y [persistencia](project-persistence.md). Definición general en frontend.

## Responsabilidades

- FastAPI (0.142.4, uvicorn 0.54.0, httpx 0.28.1 y python-dotenv 1.2.4 fijados en `requirements.txt`): validar entradas, identidad y propiedad de recursos; responder JSON/OpenAPI según contratos implementados. Configuración del `.env` propio con prioridad de variables del proceso, sin alterar `os.environ`. Python 3.14 es el objetivo de despliegue; la base se ha ejecutado localmente con 3.13.
- Supabase Auth/PostgreSQL/Storage: identidad, proyectos y archivos. Esquema en `supabase/migrations/`.
- Proveedores externos: UniProt y PDBe/SIFTS para anotación/mapeo, Groq para interpretación y ESM Atlas/ESMFold para predicción de un candidato seleccionado.
- Cliente React independiente: cálculo científico, futura generación guiada, visualización y PDF/CSV. El backend no implementa algoritmos del navegador.

## Datos existentes

La migración inicial conserva `profiles`, `projects`, `analyses`, `alignments`, `external_cache`, sus RLS, índices, trigger y UUIDv7 provisional. La API futura generará UUIDv7 con Python 3.14 para entidades propias; `profiles.id` coincide con Supabase Auth. No modificar la migración inicial como si fuera la configuración remota vigente; cambios de esquema posteriores van en migraciones nuevas.

Guardar candidatos, parámetros de búsqueda, consentimiento y predicciones necesita diseño adicional. No hay tablas, campos o endpoints nuevos aprobados para esas capacidades todavía.

## Seguridad e integración

Secretos solo en variables del servidor. JWT y autorización por recurso; CORS a orígenes configurados. RLS de tablas de usuario y acceso exclusivo del servidor a caché externa. TOTP opcional con aal2 cuando se active, aún pendiente.

La predicción envía secuencia completa tras consentimiento. El asistente recibe solo descriptores/anotación/parámetros. Retención, cuotas y términos se verifican al integrar cada proveedor. No registrar secuencias privadas o secretos en logs.

Concurrencia optimista para actualización de proyectos: fecha conocida y conflicto 409. PDF generado en navegador, subida por URL firmada y registro vía API. Los archivos grandes no deben atravesar funciones que excedan sus límites; consultar documentación oficial al implementar.

## Despliegue y operación

Vercel independiente del frontend, Supabase Free y costo cero sin tarjeta. Sin GPU local permanente. La predicción la ejecuta un proveedor externo; caché y errores mantienen la aplicación usable si ese proveedor no responde. El resultado guardado de respaldo debe identificarse como tal.

La entrega autónoma actual no autoriza cambios remotos ni publicación. main conserva versión estable; commits locales en dev. Configuración FastAPI/Vercel existente; activar migraciones y verificar sesión real requiere acción posterior del usuario.

## Pruebas

`python -m unittest discover -s tests -v`: auditoría estática de RLS y pruebas de la API con un transporte simulado de Supabase (`tests/test_api.py`): salud, CORS, token ausente/ inválido, identidad resuelta, identidad sin configurar y fallos del proveedor convertidos en 502/504 sin filtrar detalles. No prueba semántica SQL ni políticas reales; probar con PostgreSQL local antes de declarar seguridad de datos verificada.
