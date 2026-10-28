# Arquitectura del backend

## Estado del 28 de octubre de 2026

Publicado `main` `18d9444` con CI y Vercel correctos; `dev` está en `acfea11`. La entrega publicada el 27 de octubre incluye el historial del generador: workspace v3 con el historial dentro del snapshot existente, guardia monótona y el código `project-requires-current-client`. No añade tablas, columnas, políticas ni Storage. [Contrato y decisiones](generator-history-contract-proposal.md).

El backend publicado funciona con o sin la migración `20261027000000_generator_history.sql`: sin ella, el recuento de búsquedas vale cero y el SQL anterior rechaza los snapshots v3, de modo que la interfaz permanece oculta. El propietario declara haber aplicado esa migración en Supabase remoto el 28 de octubre; queda registrado como su declaración, sin inspección remota por nuestra parte, y no debe repetirse. Para que la aplicación ofrezca el guardado falta además activar `VITE_GENERATOR_HISTORY=enabled` en el proyecto de Vercel del frontend, que es trabajo del propietario.

Verificación vigente del repositorio: `python -m unittest discover -s tests` con 130 pruebas correctas y 17 SQL omitidas sin base configurada, como en CI con Python 3.14. Pendientes reales: recorrido con JWT real y PostgREST, aislamiento por ID con una segunda cuenta y comprobación de la prosa del asistente. Por decisión del propietario del 28 de octubre, la inferencia estructural desde la aplicación queda aplazada hasta que exista un ejecutor gratuito cuyos términos la permitan, y el diseño de forma nueva queda fuera del alcance; el cuaderno manual de Colab es la vía oficial y el frontend importa sus resultados. Las secciones siguientes conservan el histórico anterior.

## Continuación del 26 de octubre de 2026

La publicación de proyectos/laboratorio/asistente se completó el 23 de octubre; las entradas fechadas inferiores conservan sus estados anteriores. La lectura autenticada de Groq en producción aún no se ha comprobado. El propietario aprobó la maqueta y el suministro SIFTS: implementados localmente adquisición/normalización, comprobación de referencia completa, caché pública acotada por proceso y rutas autenticadas REST/RPC. [Contrato y evidencia](sifts.md). La interfaz Biblioteca/Analizar está integrada y verificada localmente, con identidad simulada. Sin inferencia estructural desde la aplicación, migraciones nuevas o publicación de esta continuación. Backend nuevo debe publicarse primero; repinchar/regenerar frontend después.

Actualización del 23 oct 2026: el laboratorio ya está publicado y el recorrido real del 22 oct está registrado en [activación](../../frontend/docs/laboratory-activation.md); aislamiento por ID con la segunda cuenta sigue pendiente. El asistente de interpretación está implementado localmente por REST/Connect/gRPC-Web, con Groq, cuota por identidad y comprobación de cifras; [contrato, límites y publicación](assistant.md). La interfaz aprobada solo envía cifras. Inferencia estructural desde la aplicación y `DesignService` continúan sin implementar. Los apartados fechados inferiores conservan la preparación anterior y no autorizan futuras operaciones remotas.

Las API de prueba alojadas de NVIDIA se limitan a evaluación interna; una web académica abierta a usuarios reales no queda autorizada por no cobrar. Revisión completa de términos, recomendación de ensayo solo para el propietario y advertencia de endpoint ESMFold obsoleto en [inferencia desde la app](../../frontend/docs/app-inference-proposal.md). No hay integración NVIDIA implementada ni contratos aceptados.

## Persistencia del laboratorio — 21 oct 2026

Contrato aprobado e implementado localmente dentro del snapshot existente en analyses. Workspace v2 conserva un sobre del archivo portable con SHA-256, limitado a 1.000.000 bytes; el backend valida forma, fuente y borradores, sin descomprimir ZIP ni recalcular ciencia. El navegador revalida en Worker. Preview solo añade presencia/cantidad. RLS, SECURITY INVOKER y concurrencia existentes conservados; cliente antiguo v1 no puede sustituir v2. Ver [contrato y activación pendiente](project-persistence.md). El propietario confirmó aplicar la migración el 21 oct, sin inspección remota de nuestra parte; no repetirla. Código nuevo todavía local, publicación pendiente de autorización específica.

## Estado

API: health/me, CRUD proyectos, persistencia del laboratorio, UniProt, interpretación, SIFTS autenticado e historial del generador ya publicados. JWT de usuario/SECURITY INVOKER sobre projects/analyses. Migraciones del laboratorio y del historial aplicadas por el propietario; no repetirlas. Falta el recorrido con JWT real de SIFTS y del historial. RLS/concurrencia PostgreSQL 18 comprobados en la entrega previa, sin nueva ejecución SQL en esta continuación. [Contratos](api.md), [persistencia](project-persistence.md), [UniProt](uniprot.md), [carga HTTP local con proveedores simulados](local-api-load.md).

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

unittest: 130 pruebas correctas y 17 SQL omitidas sin base configurada, con transporte simulado; comprobado el 28 de octubre en local y ejecutado también por CI con Python 3.14. Cubre auditoría estática, REST, RPC, proyectos, SIFTS, asistente e historial del generador. `scripts/test-postgres.ps1` ejecuta migraciones, RLS y concurrencia en un PostgreSQL temporal real con Auth.uid emulado: propietario, anónimo, transferencia, cascada, rollback y concurrencia. No certifica JWT ni PostgREST remotos. Adaptador UniProt probado además con datos públicos reales, sin credenciales.
