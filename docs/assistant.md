# Asistente de interpretación

## Alcance

Recibe resultados que el navegador ya calculó (descriptores, alineamiento, métricas del laboratorio o candidatos) como pares etiqueta/valor/unidad, tal como los muestra la interfaz, y devuelve una explicación breve en español o inglés. No calcula ciencia, no genera secuencias, no predice estructuras y no conversa: cada solicitud es independiente. La interfaz que lo usa está descrita en el [documento del frontend](https://github.com/Jacklb19/ProteinForge/blob/main/docs/assistant.md).

## Contrato

`POST /v1/assistant/interpret` (REST) y `proteinforge.assistant.v1.AssistantService/Interpret` (Connect, gRPC-Web y gRPC), con la misma función de dominio y validación.

| Campo | Regla |
|---|---|
| `subject` | `analysis`, `alignment`, `laboratory` o `candidates` (`SUBJECT_*` en protobuf; `UNSPECIFIED` se rechaza) |
| `language` | `es` o `en` |
| `facts` | 1 a 80; `label` hasta 120 caracteres, `value` hasta 200 y `unit` opcional hasta 40, sin caracteres de control |
| `sequence` | Opcional, hasta 2.000 residuos del alfabeto estándar más X; si se omite, no se envía secuencia (la interfaz nunca la envía) |

Respuesta: `text` (hasta 4.000 caracteres), `model`, `provider` (`groq`) y `createdAt` / `create_time`, con `Cache-Control: no-store`.

## Proveedor y límites

- Destino fijo `https://api.groq.com/openai/v1/chat/completions` con `GROQ_API_KEY` del servidor. El token del usuario nunca se reenvía y la clave no aparece en `repr`, respuestas ni logs.
- Modelo `ASSISTANT_MODEL` (por omisión `openai/gpt-oss-120b`), temperatura 0,2, `reasoning_effort: low`, `include_reasoning: false` y hasta 2.048 tokens de completado, razonamiento incluido. Cambiar a un modelo sin razonamiento exige retirar esos dos parámetros.
- Presupuesto por identidad: `ASSISTANT_REQUESTS_PER_MINUTE` (6 por omisión; 0 lo desactiva) y, para todas las cuentas juntas, `ASSISTANT_REQUESTS_PER_DAY` (0 = sin tope). Es por proceso salvo con `SHARED_BUDGET_ENABLED`. Una solicitud inválida no lo consume, ni una lectura servida desde la caché.
- Tiempo máximo del proveedor: 30 s. Una respuesta truncada (`finish_reason` distinto de `stop`), vacía, mayor de 4.000 caracteres o mayor de 200.000 bytes se rechaza.

## Reglas del texto

El mensaje de sistema pide usar solo los datos recibidos, copiar las cifras tal como llegan, no afirmar función, estabilidad, actividad, novedad, seguridad ni éxito experimental y no recomendar experimentos. Abre con una frase sin cifras y sigue con tres o cuatro párrafos de dos o tres frases, hasta dos cifras por frase y 180 palabras en total, sin Markdown. Las cifras se atribuyen a sus medidas y no se extrapolan a solubilidad, agregación, termoestabilidad, expresión ni plegamiento. Incluye un glosario de pLDDT, pTM, RMSD Cα, identidad, pI, GRAVY, índice de inestabilidad, índice alifático, perfil de hidropatía y alineamiento. El prompt es uno solo, en inglés, con el idioma como parámetro. Reglas adicionales:

- Ninguna cota, rango o valor de referencia fuera del glosario.
- Nada de «todos», «ninguno» ni una parte de los residuos a partir de una media: la confianza local media se describe solo como un promedio de toda la cadena en una banda de la escala.
- Ninguna comparación con variantes, estructuras, referencias o resultados que no estén en los datos.
- No afirmar que la proteína se pliega, se pliega bien, es estable o tiene función: pTM y pLDDT describen la confianza del predictor en su propio modelo.
- Explicar solo las medidas presentes en los datos, sin dar valor a las ausentes, ni siquiera cero.

Estas reglas son instrucciones al proveedor; el verificador automático no garantiza la corrección científica de toda la prosa.

## Verificación de cifras

La API **verifica las cifras** del texto antes de devolverlo:

- Admite los valores recibidos y su redondeo a menos decimales en cualquiera de las dos convenciones decimales (`8.445,71` y `8445.71`, también con espacio fino de miles), conservando el signo (`+`, `-` y `−`). Admite también enteros de 0 a 20 y las cotas del glosario 40, 50, 70, 90 y 100. Los identificadores como `1UBQ`, `E6K` o `A30` no cuentan como cifras. Un hecho numérico muy largo no provoca `Decimal.InvalidOperation`.
- `supported_proportions` comprueba una lista conservadora de expresiones de cantidad en español e inglés (mitad, mayoría o minoría, tercio, cuarta parte y equivalentes): solo se admiten si están presentes en los valores de los hechos recibidos, de modo que no se pueden deducir porcentajes, cambios ni distribuciones de confianza a partir de una media. Se apartan las frases exactas que no son proporciones («at most», el ordinal «the third variant»).
- Una cifra fuera de lo admitido se descarta con 502 `assistant-unverified-figure` y no se muestra.
- El verificador comprueba valores, no a qué medida se atribuyen ni toda la semántica; no lee números escritos en palabras ni juicios cualitativos. Puede rechazar prosa correcta y no garantiza la ausencia de alucinaciones; no se debilita para obtener una respuesta positiva.
- El aviso de rechazo del registro lleva solo el tema, el idioma y los recuentos por tipo (por ejemplo `subject=laboratory language=es figure-percent=1 verbal-mayoria=1`), nunca las cifras, la prosa ni la secuencia.

## Caché de lecturas verificadas

`app/assistant_cache.py` guarda en memoria del proceso las lecturas que **pasaron el verificador**, para responder sin llamar a Groq a una solicitud idéntica.

- **Clave:** SHA-256 de un JSON canónico con la versión del prompt (`PROMPT_VERSION`, la huella del prompt, los temas, los idiomas y los parámetros de generación), el modelo, la identidad del solicitante, el tema, el idioma, los hechos y la secuencia. Cualquier cambio del prompt retira las lecturas anteriores. Solo se conserva el hash.
- **Aislamiento por usuario:** la identidad forma parte de la clave, de modo que una lectura solo vuelve a quien la pidió.
- **Qué se guarda:** solo la lectura aceptada. Los rechazos del verificador, los 429, los errores del proveedor y los tiempos agotados nunca se guardan.
- **Límites:** `ASSISTANT_CACHE_TTL_SECONDS` (300 s por omisión, desde que se guarda y sin renovarse con los aciertos) y `ASSISTANT_CACHE_MAX_ENTRIES` (256), con desalojo LRU; con 0 en cualquiera de los dos la caché queda desactivada. Es por proceso: varias instancias no comparten entradas y un reinicio la vacía.
- **Presupuesto:** un acierto no consume `ASSISTANT_REQUESTS_PER_MINUTE`; un fallo de caché sí. La comprobación de configuración (503) sigue siendo lo primero.
- La respuesta conserva `Cache-Control: no-store`. Dos solicitudes idénticas simultáneas pueden llamar ambas al proveedor, y un acierto devuelve la fecha original.

## Errores

| HTTP | Detalle | RPC |
|---|---|---|
| 401 | Sin token o token inválido | `UNAUTHENTICATED` |
| 422 | Campos fuera de límites; nombra campos, no valores | `INVALID_ARGUMENT` |
| 429 | `assistant-rate-limit`: presupuesto propio o del proveedor | `RESOURCE_EXHAUSTED` |
| 502 | `assistant-provider-error`, `-unreachable`, `-invalid-response`, `-incomplete`, `-unverified-figure` | `UNAVAILABLE` |
| 503 | `assistant-not-configured`: falta `GROQ_API_KEY` | `FAILED_PRECONDITION` |
| 504 | `assistant-timeout` | `DEADLINE_EXCEEDED` |

## Evidencia de calidad de las lecturas

- Pruebas del backend sin proveedor real: validación sin llamar al proveedor, clave del servidor y ausencia del token del usuario en la petición saliente, presupuesto, fallos del proveedor sin filtrar su texto, verificación de cifras y proporciones, la caché, la presencia de las reglas del prompt (`PromptRuleTests`), la reproducción con las variantes públicas de 1UBQ (`PublicVariantReplayTests`) y la equivalencia entre REST, Connect y gRPC-Web. `buf lint` y `buf breaking` contra `main` sin roturas.
- Medición con Groq sobre datos públicos (insulina P01308 y las variantes 1 a 3 de 1UBQ, sin secuencia, es y en): con el prompt anterior a las dos últimas reglas, 7 de 13 respuestas útiles fueron aceptadas y los 6 rechazos fueron legítimos (deducían «la mayoría de los residuos» de una media). Con las reglas actuales, 16 de 16 fueron aceptadas, todas las cifras coincidían con sus hechos y ninguna afirmaba plegamiento, estabilidad ni función. Quedan imprecisiones cualitativas sin cifra que el verificador no puede ver (por ejemplo, llamar «proporción» al índice alifático). Es una muestra pequeña con un solo modelo y no garantiza que no vuelvan los rechazos.
- Una lectura real del laboratorio llegó a afirmar que 42 de 76 era «menos de la mitad»; la comprobación de proporciones verbales la rechaza.

## Diagnóstico sin llamadas a Groq

`scripts/diagnose_assistant.py` comprueba la presencia de configuración local o analiza el cuerpo guardado de un error. No abre conexiones, no ejecuta inferencia ni necesita credenciales; devuelve solo códigos conocidos e instrucciones fijas, omite texto del proveedor y limita la lectura a 2.000.000 bytes.

```bash
.venv/Scripts/python.exe -m scripts.diagnose_assistant --local-config
.venv/Scripts/python.exe -m scripts.diagnose_assistant --response artifacts/assistant-error.bin --protocol grpc-web
```

La primera orden solo muestra `assistant_configured` e `identity_configured` del entorno local; no prueba Vercel ni la validez de la clave. La segunda lee únicamente el cuerpo de una respuesta fallida, guardado fuera de Git; no deben guardarse HAR, solicitudes ni cabeceras de autenticación. `--protocol` admite `connect` y `rest`, y `--request-id` una referencia UUID opcional. `assistant-not-configured` conserva HTTP 503 en REST y `FAILED_PRECONDITION` en Connect y gRPC-Web, sin llamar al proveedor. Una respuesta desconocida no demuestra éxito ni configuración correcta.

## Cambios de contrato

Si cambia el contrato, el orden es: backend, repin y regeneración del frontend, frontend. `GROQ_API_KEY` se configura en el proyecto de Vercel del backend; el `.env` local no la despliega.
