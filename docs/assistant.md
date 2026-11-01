# Asistente de interpretación

## Diagnóstico offline de los rechazos del 1 nov 2026

Sin llamadas a Groq, sin `.env` ni datos privados. Se reconstruyeron los hechos que el Laboratorio envía para las variantes públicas 1–3 de 1UBQ sin editar (fixture `ubiquitin.json` y formato de `laboratoryFacts`) y se pasaron por el verificador respuestas plausibles escritas a mano.

| Variante | Confianza media | RMSD Cα | pTM | Identidad | Longitud / protegidas |
|---|---|---|---|---|---|
| 1 | 93,39 | 0,822 Å | 0,863 | 42 de 76 | 76 / 0 |
| 2 | 93,47 | 0,751 Å | 0,861 | 41 de 76 | 76 / 0 |
| 3 | 92,92 | 1,211 Å | 0,858 | 40 de 76 | 76 / 0 |

**Medido (offline):**

- Una respuesta fiel en español, con las cifras copiadas o redondeadas, escalas 0–100 y 0–1, cotas 50/70/90, «0 posiciones protegidas» y «76 residuos», se acepta en las tres variantes. Rangos con guion (`70-90`), `93,39/100` y `0,822Å` también.
- Se rechazan, en las tres: umbrales ajenos al glosario (pTM `0,5`, RMSD `1,0`/`1,5` Å), recuentos derivados (`34 posiciones cambian`), porcentajes (`55 %`, `86 %`) y «la mayoría de los residuos» a partir de una media. Son rechazos correctos según la regla: ninguna de esas cifras o proporciones viene en los datos.
- Falsos positivos encontrados: en inglés, «at most» y el ordinal «the third variant» se tomaban como proporciones. Corregido: solo se apartan esas frases exactas; «a third of», «most residues», «at most half» y «the most similar» siguen rechazándose. En español no se encontró ninguna clase de falso positivo plausible; quedan casos raros como «en su mayoría, estas cifras…» o «variantes 1,2 y 3» sin espacio, que se siguen rechazando.
- La única salida real conservada con estas mismas cifras (26 oct, variante 1) decía «la mayoría de los residuos» y «más de la mitad»: hoy se rechaza.

**Supuesto, no demostrado:** los tres rechazos del 1 nov fueron probablemente infracciones reales de las reglas (proporción verbal desde la media o umbral de memoria como pTM 0,5), no un fallo del verificador. No se puede confirmar: el texto descartado no se guardó y no se sabe si la interfaz estaba en español o inglés. Veredicto: **sin determinar**, con indicios de rechazo legítimo.

**Diagnóstico nuevo en logs.** El aviso de rechazo añade ahora tema, idioma y recuentos por tipo, por ejemplo `subject=laboratory language=es figure-percent=1 verbal-mayoria=1`. Tipos de cifra: `percent` (seguida de %), `near-known` (a una unidad de su último decimal de un valor conocido: suele ser un redondeo o truncado mal hecho, pero también puede ser un umbral que coincide por azar), `integer` y `decimal`. Las proporciones verbales salen con su término de la lista fija. Nunca se registran las cifras, la prosa ni la secuencia. Sin cambios de contrato ni de código de error.

### Lectura crítica de salidas aceptadas

Respuestas escritas a mano que el verificador **acepta** en las tres variantes; veredicto sobre su contenido:

| Salida aceptada | Veredicto |
|---|---|
| Copia las cuatro cifras, explica escalas y dice que la media no indica cuántos residuos superan 90 | Correcta |
| «Todos los residuos tienen confianza muy alta» desde la media | Incorrecta: distribución inferida; «todos» no está en la lista verbal |
| «Se plegará correctamente y será estable» | Incorrecta: afirma plegamiento y estabilidad; no hay cifra que el verificador pueda detectar |
| «Un pLDDT superior a 90 en el 93 % de la cadena» | Incorrecta: el 93 coincide con el redondeo de la media y se acepta |
| «Una pTM superior a 0,8 indica alta calidad» (variantes 1 y 2) | Incorrecta: umbral de memoria; 0,8 coincide con el redondeo del RMSD. En la variante 3 se rechaza |
| «Mejor RMSD que las otras dos variantes» | Sin apoyo: solo se envía una variante |
| «Prácticamente idéntica a 1UBQ» por el RMSD | Exagerada: juicio cualitativo no medido |

El verificador comprueba valores, no a qué medida se atribuyen ni la semántica. Estos casos dependen del prompt.

### Cambios de prompt aplicados (decisión de coordinación, 1 nov)

El prompt es uno solo, en inglés, con el idioma como parámetro: las reglas valen igual para es y en.

1. «what these values suggest» pasa a «what each measure reports for these values», que invita menos a inferir.
2. Ninguna cota, rango o valor de referencia fuera del glosario; si el glosario no da ninguna para una medida, no se da.
3. Nada de «todos», «ninguno» o una parte de los residuos a partir de una media.
4. Ninguna comparación con variantes, estructuras, referencias o resultados que no estén en los datos.
5. No afirmar que la proteína se pliega, se pliega bien, es estable o tiene función: pTM y pLDDT describen la confianza del predictor en su propio modelo.

No se añade cota de pTM: sería una cifra científica nueva y no nos corresponde. Opción aplazada: añadir «todos/all» a la lista verbal. Sin salidas reales que la comprueben podría crear falsos positivos nuevos.

Estas reglas son instrucciones al modelo. El verificador sigue sin detectar estas inferencias cuando no llevan cifras.

**Medición real posterior, 1 nov** ([informe](assistant-real-measurement-2026-11-01.md)). Con Groq y hechos públicos idénticos a los de la interfaz, el prompt anterior dio 7 de 13 aceptadas, sin contar tres 429 de cuota. Los 6 rechazos fueron legítimos (`verbal-mayoria`/`verbal-most`: «alta confianza a la mayoría de los residuos» deducido de la media) y no hubo falso positivo del verificador. Se añadieron dos reglas:

6. Describir la confianza local media solo como un promedio de toda la cadena en una banda de la escala, sin atribuirla a residuos, regiones o partes. El glosario de pLDDT lo repite.
7. Explicar solo las medidas presentes en los hechos, sin dar valor a las ausentes, ni siquiera cero; una lectura de insulina había inventado «zero fixed positions».

Con ellas se aceptaron 16 de 16. En las 23 aceptadas, todas las cifras coinciden con sus hechos y ninguna afirma plegamiento, estabilidad ni función. Quedan imprecisiones cualitativas sin cifra. Ajuste local, sin publicar.

Pruebas: `PromptRuleTests` (las reglas nuevas presentes en el prompt para es y en, sin cota de pTM ni cambio de `SCALE_FIGURES`), `PublicVariantReplayTests` (respuestas fieles aceptadas; umbrales, recuentos, porcentajes y proporciones rechazados; ordinal y «at most» en inglés aceptados con proporciones vecinas aún rechazadas) y una prueba de que el log contiene solo tipos y recuentos.

## Corrección local del 29 oct 2026: signos y decimales largos

La [revisión local](review-continuation-2026-10-29.md) demostró y corrigió un cambio de signo que el verificador aceptaba: `GRAVY -0,532` podía reaparecer como `0,532`. Ahora conserva `+`, `-` y `−`, limita la excepción de enteros a 0–20 y evita que un hecho numérico muy largo provoque `Decimal.InvalidOperation`. Tres regresiones fallaron antes del arreglo y pasan después. Sin cambios de contrato ni publicación. Esta guarda no certifica la semántica completa de las lecturas reales.

Publicado el 23 oct 2026 por decisión del propietario: opción B de la [propuesta](../../frontend/docs/assistant-proposal.md), con Groq y `openai/gpt-oss-120b`. El propietario aceptó expresamente que los datos de la interpretación salgan a ese proveedor. La entrega posterior del 26 oct también está publicada, con CI correcta. La [interfaz A revisada y aprobada](../../frontend/docs/assistant-ui-proposal.md) aparece al pie de Analizar y Laboratorio y envía solo cifras, nunca la secuencia; [uso y diagnóstico actual](../../frontend/docs/assistant.md).

## Comprobación real y proporciones verbales — 26 oct

Con sesión del propietario, producción devolvió dos lecturas: insulina P01308 en Analizar y variante 1 pública de 1UBQ en Laboratorio. Variables y redespliegue actualizados por el propietario; esta comprobación no publicó código ni modificó secretos. El incidente anterior no se reproduce, pero no hay respuesta histórica para atribuir su causa. Presencia de la clave, uso real del servicio y validez científica de la prosa son comprobaciones distintas.

La segunda lectura dijo que 42 de 76 era «menos de la mitad». Una prueba local real posterior con instrucciones reforzadas mantuvo cifras válidas pero dedujo mal la proporción de sustituciones. El verificador anterior aceptaba esa salida: reproducción offline `numeric_check=true`. La comprobación local nueva la rechaza: `verbal_proportion_check=false`, sin otra solicitud al proveedor.

Además del prompt, `supported_proportions` comprueba una lista conservadora de expresiones en español/inglés (mitad, mayoría/minoría, tercio/cuarta parte y equivalentes). Solo admite expresiones presentes en valores de hechos recibidos; no calcula porcentajes ni cambios a partir de identidad, ni distribuciones de confianza a partir de una media. Usa el mismo rechazo `assistant-unverified-figure` (REST 502/RPC UNAVAILABLE), sin alterar contratos o presupuestos. No comprueba toda la semántica, puede rechazar prosa correcta y no garantiza ausencia de alucinaciones; no debilitarlo para obtener una respuesta positiva.

Evidencia local ignorada: `artifacts/assistant-prompt-audit-2026-10-26-instruction-only-failed.json`, `assistant-verbal-guard-replay-2026-10-26.json` y `assistant-prompt-audit-2026-10-26.json`. Este último es una segunda llamada real local con cifras públicas, sin secuencia: el verificador la descartó en 1,576 s. No se conservó su texto descartado ni se atribuye el rechazo a una frase específica. Ninguna lectura nueva con la guardia se certificó como correcta en producción. Trece pruebas del asistente más seis del diagnóstico pasan (19), incluyendo los casos es/en observados, texto cualitativo suministrado y rechazo equivalente por RPC. Sin SQL, cambios remotos o publicación nueva.

El cliente local del asistente espera ahora 40 s: identidad puede emplear 5 s y la llamada a Groq 30 s. El resto del cliente API conserva 10 s. El timeout HTTP del proveedor sigue siendo el existente de 30 s; no se amplía ni se presenta como plazo total estricto de una tarea.

## Alcance de la interpretación

Recibe resultados que el navegador ya calculó (descriptores, alineamiento, métricas del laboratorio o candidatos) como pares etiqueta/valor/unidad, tal como los muestra la interfaz, y devuelve una explicación breve en español o inglés. No calcula ciencia, no genera secuencias, no predice estructuras y no conversa: cada solicitud es independiente.

## Contrato

`POST /v1/assistant/interpret` (REST) y `proteinforge.assistant.v1.AssistantService/Interpret` (Connect, gRPC-Web y gRPC), con la misma función de dominio y validación.

| Campo | Regla |
|---|---|
| `subject` | `analysis`, `alignment`, `laboratory` o `candidates` (`SUBJECT_*` en protobuf; `UNSPECIFIED` se rechaza) |
| `language` | `es` o `en` |
| `facts` | 1–80; `label` hasta 120 caracteres, `value` hasta 200 y `unit` opcional hasta 40, sin caracteres de control |
| `sequence` | Opcional, hasta 2.000 residuos del alfabeto estándar más X; si se omite, no se envía secuencia |

Respuesta: `text` (hasta 4.000 caracteres), `model`, `provider` (`groq`) y `createdAt`/`create_time`. `Cache-Control: no-store`.

## Proveedor y límites

- Destino fijo `https://api.groq.com/openai/v1/chat/completions` con `GROQ_API_KEY` del servidor. El token del usuario nunca se reenvía y la clave no aparece en `repr`, respuestas ni logs.
- Modelo `ASSISTANT_MODEL` (por omisión `openai/gpt-oss-120b`), temperatura 0,2, `reasoning_effort: low`, `include_reasoning: false` y hasta 2.048 tokens de completado, razonamiento incluido. Cambiar a un modelo sin razonamiento exige retirar esos dos parámetros.
- Presupuesto por identidad: `ASSISTANT_REQUESTS_PER_MINUTE` (6 por omisión; 0 lo desactiva), por proceso y no global, igual que el de UniProt. Una solicitud inválida no lo consume.
- Tiempo máximo 30 s. Una respuesta truncada (`finish_reason` distinto de `stop`), vacía, mayor de 4.000 caracteres o mayor de 200.000 bytes se rechaza.

## Reglas del texto

El mensaje de sistema pide usar solo los datos recibidos, copiar las cifras tal como llegan, no afirmar función, estabilidad, actividad, novedad, seguridad ni éxito experimental y no recomendar experimentos. Abre con una frase sin cifras y sigue con tres o cuatro párrafos de dos o tres frases, hasta dos cifras por frase y 180 palabras en total, sin Markdown. Las cifras se atribuyen a sus medidas y no se extrapolan a solubilidad, agregación, termoestabilidad, expresión ni plegamiento. Incluye un glosario de pLDDT, pTM, RMSD Cα, identidad, pI, GRAVY, índice de inestabilidad, índice alifático, perfil de hidropatía y alineamiento. Estas reglas de redacción son instrucciones al proveedor; la comprobación posterior automática valida cifras, tamaño y finalización, no garantiza la corrección científica de toda la prosa.

La API además **verifica las cifras** del texto: admite valores recibidos y su redondeo a menos decimales en cualquiera de las dos convenciones decimales (`8.445,71` y `8445.71`, también con espacio fino de miles), conservando el signo en la corrección local del 29 oct. Admite también enteros de 0 a 20 y las cotas del glosario 40, 50, 70, 90 y 100; los identificadores como `1UBQ`, `E6K` o `A30` no cuentan como cifras. El verificador no distingue si una cota o un entero permitido se usa como escala o como resultado: ese límite impide prometer ausencia de alucinaciones. Si aparece una cifra fuera de lo admitido, se descarta con 502 `assistant-unverified-figure` y no se muestra.

## Errores

| HTTP | Detalle | RPC |
|---|---|---|
| 401 | Sin token o token inválido | `UNAUTHENTICATED` |
| 422 | Campos fuera de límites; nombra campos, no valores | `INVALID_ARGUMENT` |
| 429 | `assistant-rate-limit`: presupuesto propio o del proveedor | `RESOURCE_EXHAUSTED` |
| 502 | `assistant-provider-error`, `-unreachable`, `-invalid-response`, `-incomplete`, `-unverified-figure` | `UNAVAILABLE` |
| 503 | `assistant-not-configured`: falta `GROQ_API_KEY` | `FAILED_PRECONDITION` |
| 504 | `assistant-timeout` | `DEADLINE_EXCEEDED` |

## Evidencia

- 70 pruebas del backend (12 SQL omitidas sin base): validación sin llamar al proveedor, clave del servidor y ausencia del token del usuario en la petición saliente, presupuesto, fallos del proveedor sin filtrar su texto, verificación de cifras (redondeos, convenciones, identificadores y cifras inventadas) y equivalencia REST, Connect y gRPC-Web. Buf lint correcto y `buf breaking` contra `main` sin roturas.
- Tres llamadas reales locales a Groq el 23 oct con datos públicos de la variante 1 de 1UBQ (sin secuencia): las tres respondieron y pasaron la verificación de cifras (la primera no pudo mostrarse por la codificación de la consola de Windows; las otras tardaron 1,2–1,3 s). Sin glosario, el modelo definió mal el pTM como similitud con estructuras conocidas; con el glosario lo explica como confianza del modelo en el plegamiento global.
- Producción, 26 oct: el backend está publicado, pero una lectura autenticada de cifras públicas mostró un error genérico. Sin respuesta RPC capturada, no se conoce su causa; configuración de clave y retención no verificadas. No atribuir éxito ni clave ausente por esa captura.
- Diagnóstico local posterior: `assistant-not-configured` conserva HTTP 503 en REST y `FAILED_PRECONDITION`/9 en Connect y gRPC-Web, sin llamar al proveedor. El cliente omitía ese detalle y lo convertía en error desconocido; corrección local en frontend, sin cambiar contrato ni desactivar el verificador. El fallo real de producción sigue sin diagnóstico concluyente; [evidencia necesaria](../../frontend/docs/assistant.md#diagnóstico-local-del-26-oct-2026). Las pruebas usan identidad y proveedor simulados, sin consumo remoto.

## Diagnóstico local sin llamadas a Groq — 26 oct

`scripts/diagnose_assistant.py` comprueba presencia de configuración local o analiza el cuerpo guardado de un error. No abre conexiones, no ejecuta inferencia ni necesita credenciales. Devuelve solo códigos conocidos e instrucciones fijas; omite texto del proveedor, datos de respuesta y campos adicionales. Limita la lectura a 2.000.000 bytes y rechaza tramas incompletas o comprimidas.

Desde la raíz del backend:

```powershell
.\.venv\Scripts\python.exe -m scripts.diagnose_assistant --local-config
.\.venv\Scripts\python.exe -m scripts.diagnose_assistant --response artifacts/assistant-error.bin --protocol grpc-web
```

La primera orden solo muestra `assistant_configured` e `identity_configured` del entorno **local**; no prueba Vercel ni la validez de la clave. La segunda lee únicamente el cuerpo de una respuesta fallida, guardado en `artifacts/`, fuera de Git. No guardar HAR, solicitudes ni cabeceras de autenticación. Para otros transportes elegir `--protocol connect` o `--protocol rest`; `--request-id` admite opcionalmente una referencia UUID. Una respuesta desconocida no demuestra éxito ni configuración correcta. `assistant-provider-error` aún requiere conocer el estado HTTP del proveedor en los logs; `assistant-rate-limit` por sí solo no distingue cuota propia de cuota de Groq.

El navegador publicado se abrió sin sesión. El propietario indicó que iniciará sesión al llegar a casa; no se realizó una nueva lectura autenticada ni se confirmó la causa del incidente. El fallo sigue abierto. La corrección anterior de clasificación y esta herramienta permanecen locales y no requieren cambios de contrato ni migraciones.

Verificación: 16/16 pruebas del asistente y diagnóstico correctas, con identidad/proveedor simulados; las seis del diagnóstico se repitieron tras endurecer el lector de tramas y pasan. Incluyen rechazo de cuerpos truncados, duplicados y excesivos, omisión de datos privados, presencia local sin llamada al proveedor y referencia inválida sin reflejar su valor. La ejecución real de `--local-config` informó ambos indicadores `true`; esto acredita presencia local, no validez de la clave ni configuración remota. Advertencia previa de Starlette/httpx conservada. Sin cambios del código servido ni pruebas nuevas de producción, SQL o CI.

## Publicación de futuras correcciones

Revisión posterior del 1 nov 2026: se solicitaron lecturas de las tres variantes públicas de 1UBQ desde la API local con sesión real. Las tres se descartaron con `502 assistant-unverified-figure`; no se mostró prosa que pudiera certificar. No se leyó el texto descartado, por lo que no se distingue una cantidad inventada de un posible falso positivo. No se debilitaron las reglas ni se insistió buscando una salida favorable. [Casos, cifras y límites](../../frontend/docs/readiness-2026-11-01.md). El diagnóstico offline y la lectura crítica están en [Diagnóstico offline de los rechazos del 1 nov 2026](#diagnóstico-offline-de-los-rechazos-del-1-nov-2026); la causa exacta de estos tres sigue sin determinar.

La entrega del 26 oct ya se publicó: backend `main` `a3edaf1d7a43ada2b2bbf9113ff9ccef7f123a57`; frontend fija ese commit y su generación remota no produjo diferencias. No repetir publicación ni migraciones. Una entrega futura necesita su propia autorización; si cambia el contrato, mantener el orden backend→pin/regeneración→frontend. La regresión local añadida aquí solo cambia pruebas y documentación del backend.
