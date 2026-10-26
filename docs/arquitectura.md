# Arquitectura del backend

## Continuación del 26 de octubre de 2026

La publicación de proyectos/laboratorio/asistente se completó el 23 de octubre; las entradas fechadas inferiores conservan sus estados anteriores. La lectura autenticada de Groq en producción aún no se ha comprobado. El propietario aprobó la maqueta y el suministro SIFTS: implementados localmente adquisición/normalización, comprobación de referencia completa, caché pública acotada por proceso y rutas autenticadas REST/RPC. [Contrato y evidencia](sifts.md). La interfaz Biblioteca/Analizar está integrada y verificada localmente, con identidad simulada. Sin inferencia estructural desde la aplicación, migraciones nuevas o publicación de esta continuación. Backend nuevo debe publicarse primero; repinchar/regenerar frontend después.

Actualización del 23 oct 2026: el laboratorio ya está publicado y el recorrido real del 22 oct está registrado en [activación](../../frontend/docs/laboratory-activation.md); aislamiento por ID con la segunda cuenta sigue pendiente. El asistente de interpretación está implementado localmente por REST/Connect/gRPC-Web, con Groq, cuota por identidad y comprobación de cifras; [contrato, límites y publicación](assistant.md). La interfaz aprobada solo envía cifras. Inferencia estructural desde la aplicación y `DesignService` continúan sin implementar. Los apartados fechados inferiores conservan la preparación anterior y no autorizan futuras operaciones remotas.

Las API de prueba alojadas de NVIDIA se limitan a evaluación interna; una web académica abierta a usuarios reales no queda autorizada por no cobrar. Revisión completa de términos, recomendación de ensayo solo para el propietario y advertencia de endpoint ESMFold obsoleto en [inferencia desde la app](../../frontend/docs/app-inference-proposal.md). No hay integración NVIDIA implementada ni contratos aceptados.

## Persistencia del laboratorio — 21 oct 2026

Contrato aprobado e implementado localmente dentro del snapshot existente en analyses. Workspace v2 conserva un sobre del archivo portable con SHA-256, limitado a 1.000.000 bytes; el backend valida forma, fuente y borradores, sin descomprimir ZIP ni recalcular ciencia. El navegador revalida en Worker. Preview solo añade presencia/cantidad. RLS, SECURITY INVOKER y concurrencia existentes conservados; cliente antiguo v1 no puede sustituir v2. Ver [contrato y activación pendiente](project-persistence.md). El propietario confirmó aplicar la migración el 21 oct, sin inspección remota de nuestra parte; no repetirla. Código nuevo todavía local, publicación pendiente de autorización específica.

## Estado

API: health/me, CRUD proyectos, persistencia del laboratorio, UniProt e interpretación ya publicados. JWT de usuario/SECURITY INVOKER sobre projects/analyses. Migración del laboratorio aplicada por el propietario y recorrido real registrado; no repetirla. SIFTS está integrado solo localmente y falta su recorrido con JWT real. RLS/concurrencia PostgreSQL 18 comprobados en la entrega previa, sin nueva ejecución SQL en esta continuación. [Contratos](api.md), [persistencia](project-persistence.md), [UniProt](uniprot.md), [carga HTTP local con proveedores simulados](local-api-load.md).

## Responsabilidades

- FastAPI (0.142.4, uvicorn 0.54.0, httpx 0.28.1 y python-dotenv 1.2.4 fijados en `requirements.txt`): validar entradas, identidad y propiedad de recursos; responder JSON/OpenAPI según contratos implementados. Configuración del `.env` propio con prioridad de variables del proceso, sin alterar `os.environ`. Python 3.14 es el objetivo de despliegue; la base se ha ejecutado localmente con 3.13.
- Supabase Auth/PostgreSQL/Storage: identidad, proyectos y archivos. Esquema en `supabase/migrations/`.
- Proveedores externos: UniProt para referencias y Groq para interpretación de resultados, publicados; PDBe/SIFTS autenticado implementado localmente. Presupuestos por identidad y por proceso, con caché pública SIFTS acotada. Inferencia estructural desde la aplicación pendiente; los resultados manuales de Colab se importan en el frontend. No se ejecutan modelos en la API.
- Cliente React independiente: cálculo científico/generación guiada/visualización/informes locales. Backend no implementa algoritmos del navegador.

## Datos existentes

Se conservan profiles/projects/analyses/alignments/external_cache. Migración posterior reemplaza helper UUIDv7 provisional con timestamp/bits/gen_random_uuid SQL, sin exigir Python 3.14 local; profiles.id coincide con Auth. Snapshot workspace-v1 en analyses incluye fuente opcional exacta y resultados asociados. Cambios en migraciones nuevas; no modifican por sí solos la base remota.

Guardar candidatos, parámetros de búsqueda, consentimiento y predicciones necesita diseño adicional. No hay tablas, campos o endpoints nuevos aprobados para esas capacidades todavía.

## Seguridad e integración

Secretos solo en variables del servidor. JWT y autorización por recurso; CORS a orígenes configurados. RLS de tablas de usuario y acceso exclusivo del servidor a caché externa. TOTP opcional con aal2 cuando se active, aún pendiente.

La predicción envía secuencia completa tras consentimiento. El asistente recibe solo descriptores/anotación/parámetros. Retención, cuotas y términos se verifican al integrar cada proveedor. No registrar secuencias privadas o secretos en logs.

Concurrencia optimista PUT/DELETE: timestamp con microsegundos, bloqueo y conflicto 409. PDF local implementado en frontend; subida por URL firmada/registro todavía previstos, sin endpoints activos. Consultar límites al integrar archivos/proveedores.

## Despliegue y operación

Vercel independiente, Supabase Free y costo cero sin tarjeta. Próximo alcance: dos o tres diseños computacionales para demo, sin GPU permanente ni entrenamiento propio. Probar Colab interactivo o equipo local separado de FastAPI; automatización posterior con ejecutor/cola si se acuerda, sin nuevos endpoints/migraciones todavía. No alojar pesos en la función Vercel; identificar resultados de respaldo como tales.

Acuerdo de producto/modelos/hardware/alojamiento y precios en el [laboratorio del frontend](https://github.com/Jacklb19/ProteinForge/blob/dev/docs/design-lab-roadmap.md). Railway coordina tareas pero no ofrece GPU; Render publica CPU/RAM sin GPU cotizada para este caso. Precios consultados no autorizan gasto ni acreditan rendimiento.

La petición posterior autoriza push de dev de ambos repositorios solo con autoría exclusiva del propietario, sin coautores. main conserva versión estable; sin merge, contratación ni migraciones autorizadas. Activar persistencia y verificar sesión real sigue pendiente.

## Pruebas

unittest: 26 pruebas HTTP/contratos/RLS estático pasan con transporte simulado; seis SQL omitidas sin base configurada. scripts/test-postgres.ps1 ejecutó las seis con PostgreSQL 18 temporal real y Auth.uid emulado: propietario/anónimo/transferencia/cascada/rollback/concurrencia. No certifica JWT/PostgREST remoto. Entorno local Python 3.13; objetivo 3.14 aún sin ejecutar. Adaptador UniProt probado adicionalmente con datos públicos reales, sin credenciales.
