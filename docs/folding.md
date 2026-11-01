# Predicción estructural — implementación local del 30 oct 2026

Sin publicar. API autenticada y desactivada salvo `FOLDING_ENABLED=enabled`. [Contrato compartido](../../frontend/docs/prediction-contract-2026-10-30.md). No instala modelos ni GPU en Vercel, no crea cuentas ni exige tarjeta. Reenvía una secuencia a un servicio público compartido; no garantiza disponibilidad ni inferencia nueva frente a caché.

`POST /v1/predictions/fold` y `proteinforge.folding.v1.FoldingService/Predict` reutilizan lógica, identidad y presupuesto. Petición: `sequence` canónica de 3–120 residuos y `consentToSendSequence: true`. El token solo valida la cuenta en Supabase; nunca viaja a ESM Atlas. Destino fijo en `app/folding_config.py`, sin URL elegible por el cliente, redirecciones, reintentos ni caché de secuencias privadas. El proveedor recibe texto plano; se solicita PDB sin compresión y se rechazan codificaciones inesperadas.

Una petición activa por proceso, máximo tres admitidas por minuto por proceso y una por identidad. Contadores en memoria, no distribuidos entre instancias ni persistentes tras reinicios. Un fallo admitido consume presupuesto. Timeout de 30 segundos para todo el intercambio, además del timeout HTTP por fase; si la tarea Python se cancela, libera su admisión local, sin prometer detener el cálculo del proveedor. Abortar la espera del navegador no demuestra que el servidor detecte inmediatamente la desconexión; el intercambio queda acotado por el deadline.

Resultado: PDB original, secuencia, SHA256 de ambos, proveedor/modelo declarados para este endpoint, fecha de recepción y URL fija. Revisión, pTM, PAE y confianza quedan `null` en REST; protobuf no los contiene y el cliente los normaliza a `null`. No interpretar B-factor como pLDDT sin información adicional. Una cadena A, secuencia exacta, backbone N/CA/C/O completo, coordenadas finitas y sin residuos/átomos ambiguos, máximo 1.000.000 bytes UTF8. La validez sintáctica no demuestra plegamiento o función.

Errores sin contenido del usuario/proveedor: 422 validación, 403 consentimiento, 401 sesión, 503 configuración, 429 cuota/ocupación, 502 proveedor/respuesta y 504 tiempo agotado. RPC conserva las categorías del contrato. Las respuestas no se almacenan en caché HTTP.

## Activación autorizada — 1 nov 2026

El propietario declara aplicada la migración nueva y configuradas `FOLDING_ENABLED=enabled` en backend y `VITE_STRUCTURE_PREDICTION=enabled` en frontend. Autoriza publicar ambos repositorios con autoría exclusiva suya. No repetir la migración. La declaración no equivale a haber inspeccionado la base o la configuración; se comprueba el despliegue posterior. El contrato preparado es `77a753c2f8673b964e1943a8845767c9dce67635`, ya fijado en frontend. Backend debe publicarse primero para que CI pueda descargarlo. La comprobación real local del 1 nov obtuvo 76 residuos con sesión del propietario, PDB y portable idénticos y límite en una repetición inmediata; producción y guardado remoto siguen pendientes hasta comprobarlos. [Evidencia local](../../frontend/docs/readiness-2026-11-01.md).

## Persistencia y preparación anterior a la publicación

RPC aplica además el plazo restante del contexto a autenticación y predicción: si caduca durante Auth, no envía la secuencia al proveedor; si caduca durante el intercambio, responde `DEADLINE_EXCEEDED` y libera la admisión. Las regresiones Connect de plazo vencido/corto fallaron antes del arreglo y pasan después. La revisión independiente confirmó por lectura el cierre; las ejecuciones las realizó coordinación.

Laboratorio portable v2 añade `predictions` por variante y mantiene la fuente/estado originales. Su envoltorio sigue v1; exige workspace v4. Pydantic comprueba hashes, PDB y asociación exacta. La migración nueva `20261030000000_structure_predictions.sql` comprueba forma, hashes y asociación en SQL, conserva RLS/SECURITY INVOKER y evita sobrescritura por clientes v1–v3. La geometría se valida en FastAPI y Worker, no se recalcula en SQL. No añade tablas, buckets ni permisos sobre datos. **Solo aplicada en PostgreSQL temporal local, no en Supabase remoto.**

Para publicar con autorización: revisar/aplicar la migración y publicar backend; después repinchar `frontend/buf.gen.yaml` al commit publicado y regenerar/verificar el cliente. El frontend usa stubs del contrato hermano local; la referencia del contrato debe apuntar al commit de esta entrega. La generación remota solo será reproducible cuando ese commit exista en el remoto. Activar las banderas requiere un paso explícito posterior y un recorrido con sesión real. La generación remota del frontend en CI no puede reproducir este contrato sin publicar y actualizar ese pin.

## Verificado

- Backend: 146 descubiertas, 127 ejecutadas correctas y 19 SQL omitidas sin base, Python 3.13.14. Las 19 SQL pasaron aparte en PostgreSQL 18 temporal; clúster detenido. `auth.uid()` emulado, sin JWT/PostgREST desplegados.
- Los tres PDB públicos guardados anteriormente (76 residuos, 601/592/605 átomos) pasan el nuevo validador sin nuevas peticiones de red.
- REST y Connect comparten cuota; pruebas de consentimiento, configuración, límites, errores, deadline/cancelación, respuestas inválidas y token ausente en proveedor. Proyectos mantienen versiones monótonas, rollback y aislamiento por dueño.
- `buf lint` correcto. Python 3.14 de CI y los tres protocolos contra servidor real no se volvieron a certificar en esta entrega.

El [informe del frontend](../../frontend/docs/prediction-delivery-2026-10-30.md) distingue navegador con respuestas grabadas de la comprobación remota pendiente.
