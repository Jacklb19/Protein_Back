# Asistente de interpretación

Implementado el 23 oct 2026 por decisión del propietario: opción B de la [propuesta](../../frontend/docs/assistant-proposal.md), con Groq y `openai/gpt-oss-120b`. El propietario aceptó expresamente que los datos de la interpretación salgan a ese proveedor. El backend está implementado y probado en local, sin publicar; la interfaz espera una maqueta aprobada.

## Qué hace

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

El mensaje de sistema obliga a usar solo los datos recibidos, copiar las cifras tal como llegan, no afirmar función, estabilidad, actividad, novedad, seguridad ni éxito experimental, no recomendar experimentos y escribir texto plano de dos a cuatro párrafos (200 palabras como máximo). Incluye un glosario breve de las medidas que usa la aplicación (pLDDT, pTM, RMSD Cα, identidad, pI, GRAVY, índice de inestabilidad, índice alifático, perfil de hidropatía y alineamiento) para que las defina correctamente.

La API además **verifica cada cifra** del texto: debe ser un valor recibido o ese valor redondeado a menos decimales, en cualquiera de las dos convenciones decimales (`8.445,71` y `8445.71`, también con espacio fino de miles). Los enteros hasta 20 se admiten como conteos de prosa y los identificadores como `1UBQ`, `E6K` o `A30` no cuentan como cifras. Si aparece una cifra no respaldada, la respuesta se descarta con 502 `assistant-unverified-figure` y no se muestra.

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
- No comprobado: producción. Requiere publicar el backend y que `GROQ_API_KEY` exista en las variables del proyecto de Vercel; la clave local no viaja con el código.
