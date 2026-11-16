# Predicción estructural

Predice la estructura de una secuencia con el servicio público de ESM Atlas (`esmfold_v1`). La API es autenticada y está desactivada salvo `FOLDING_ENABLED=enabled`. No instala modelos ni GPU en Vercel y reenvía la secuencia a un servicio público compartido: no garantiza disponibilidad ni que la inferencia sea nueva frente a una caché del proveedor. El [contrato compartido con el frontend](https://github.com/Jacklb19/ProteinForge/blob/main/docs/prediccion.md) describe el recorrido completo.

## Endpoint

`POST /v1/predictions/fold` y `proteinforge.folding.v1.FoldingService/Predict` reutilizan la misma lógica, identidad y presupuesto. La petición lleva `sequence` canónica de 3 a 120 residuos y `consentToSendSequence: true`.

- El token solo valida la cuenta en Supabase y nunca viaja a ESM Atlas. El destino es fijo (`app/folding_config.py`); el cliente no elige URL y no hay redirecciones, reintentos ni caché de secuencias privadas. El proveedor recibe texto plano, se solicita el PDB sin compresión y se rechazan codificaciones inesperadas.
- **Presupuesto:** una petición activa por proceso, como máximo tres admitidas por minuto por proceso y una por identidad. Los contadores están en memoria (no son distribuidos ni persistentes) salvo con `SHARED_BUDGET_ENABLED`, que cuenta la ventana por cuenta y la común en PostgreSQL. Un fallo admitido consume presupuesto.
- **Plazo:** 30 s para todo el intercambio, además del tiempo máximo HTTP por fase. Si la tarea se cancela, libera su admisión local, sin prometer detener el cálculo del proveedor ni detectar de inmediato la desconexión del navegador. En RPC se aplica además el plazo restante del cliente (`ctx.timeout_ms`) a la autenticación y a la predicción: si caduca durante la autenticación, la secuencia no se envía al proveedor, y si caduca durante el intercambio responde `DEADLINE_EXCEEDED` y libera la admisión.

## Resultado y validación

El resultado incluye el PDB original, la secuencia, el SHA-256 de ambos, el proveedor y el modelo declarados para este endpoint, la fecha de recepción y la URL fija. La revisión del modelo, el pTM, el PAE y la confianza son `null` en REST; protobuf no los contiene y el cliente los normaliza a `null`. No debe interpretarse el factor B como pLDDT sin más información.

La respuesta debe tener una sola cadena A, la secuencia exacta, el esqueleto N, CA, C y O completo en cada residuo, coordenadas finitas, ningún residuo ni átomo ambiguo y un tamaño máximo de 1.000.000 bytes UTF-8. La validez sintáctica no demuestra plegamiento ni función.

Errores sin contenido del usuario ni del proveedor: 422 validación, 403 falta de consentimiento, 401 sesión, 503 configuración, 429 cuota u ocupación, 502 proveedor o respuesta inválida y 504 tiempo agotado. RPC conserva las categorías. Las respuestas no se almacenan en caché HTTP.

## Persistencia

El laboratorio portable v2 añade `predictions` por variante y mantiene la fuente y el estado originales; su sobre sigue siendo v1 y exige workspace v4. Pydantic comprueba los hashes, el PDB y la asociación exacta. La migración `20261030000000_structure_predictions.sql` comprueba la forma, los hashes y la asociación en SQL, conserva RLS y `SECURITY INVOKER` y evita la sobrescritura por clientes v1 a v3. La geometría se valida en FastAPI y en el Worker del frontend, no en SQL. No añade tablas, buckets ni permisos sobre datos.

## Activación

1. Aplicar la migración y publicar el backend.
2. Fijar `frontend/buf.gen.yaml` al commit publicado y regenerar el cliente.
3. Publicar el frontend y definir `FOLDING_ENABLED=enabled` (backend) y `VITE_STRUCTURE_PREDICTION=enabled` (frontend).

## Pruebas

REST y Connect comparten cuota. Hay pruebas de consentimiento, configuración, límites, errores, plazo y cancelación, respuestas inválidas y ausencia del token en la petición al proveedor. Los proyectos mantienen versiones monótonas, rollback y aislamiento por propietario, y las pruebas SQL se ejecutan en PostgreSQL 18.
