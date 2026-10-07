# Arquitectura del backend

## Estado

Repositorio preparado, migración inicial trasladada desde el frontend y auditoría estática de RLS. API, autenticación, proveedores y despliegue aún pendientes. La definición general está en el repositorio frontend, en `docs/definicion-proyecto.md`; no mantener una segunda copia que pueda divergir.

## Responsabilidades

- FastAPI/Python 3.14 previsto: validar entradas, identidad JWT y propiedad de recursos; responder JSON/OpenAPI según contratos implementados.
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

El usuario autoriza cambios remotos y publicación. `main` es versión verificada; desarrollo en `dev`. Configuración FastAPI/Vercel, URLs efectivas y versiones exactas de dependencias quedan para la tarea de base API.

## Pruebas

`python -m unittest discover -s tests -v`: auditoría estática de RLS heredada. No prueba semántica SQL ni autorización real. Al implementar la API, añadir casos de identidad/propiedad, entradas y errores; probar políticas con PostgreSQL local antes de declarar seguridad de datos verificada.
