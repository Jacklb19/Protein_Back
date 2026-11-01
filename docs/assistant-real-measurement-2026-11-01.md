# Medición real del asistente con Groq — 1 nov 2026

Mide cómo responde el proveedor real (Groq, `openai/gpt-oss-120b`) al prompt endurecido de los commits locales `9d27dda` y `2ea64f0`, y revisa las salidas aceptadas frente a sus cifras. Solo datos públicos. Entrega local: sin push, despliegue ni cambios remotos. Contexto y diagnóstico offline en [assistant.md](assistant.md#diagnóstico-offline-de-los-rechazos-del-1-nov-2026).

## Método

- Clave `GROQ_API_KEY` presente en `backend/.env` local; se comprobó sin imprimirla (`assistant_enabled=True`).
- Script desechable fuera del repositorio que llama a `app.assistant.interpret`: el mismo camino de dominio que REST y RPC (proveedor, verificador de cifras y proporciones, y log de `refusal()`). No pasa por Auth ni por la pasarela, y no se tocó código de autorización. El presupuesto por identidad se desactivó solo en el contexto del script. Un envoltorio de `complete` guardó también el texto descartado, para poder leer los rechazos.
- Hechos idénticos a los de la interfaz y **sin secuencia**, como en la app:
  - Analizar, insulina P01308 (ejemplo público, 110 residuos). Se generaron con el propio `analysisFacts` del frontend, ejecutado en una prueba desechable fuera del repositorio, en es (`es-CO`) y en (`en-US`): longitud, masa 11.980,91 Da, pI 5,22, inestabilidad 40,33, índice alifático 102,91, GRAVY 0,193, composición 45/22/10/9/18/6 y ventana 9.
  - Laboratorio, variantes públicas 1–3 de 1UBQ sin editar, con etiquetas del catálogo i18n: 93,39 / 0,822 Å / 0,863 / 42 de 76; 93,47 / 0,751 Å / 0,861 / 41 de 76; 92,92 / 1,211 Å / 0,858 / 40 de 76; 76 residuos y 0 protegidas.
- Ocho casos (cuatro temas × es/en), dos rondas: tanda A con el prompt de `2ea64f0` y tanda B con el prompt ajustado aquí. Son 32 llamadas en total; la tanda B era necesaria para medir el ajuste. Coste cero (plan gratuito).

## Tanda A: prompt de `2ea64f0`

16 llamadas a 4 s de intervalo: **7 aceptadas, 6 `assistant-unverified-figure`, 3 `assistant-rate-limit`** (429 de Groq por cuota del plan gratuito, no del presupuesto propio).

| # | Caso | Idioma | Resultado | Log de `refusal()` |
|---|---|---|---|---|
| A1 | Insulina | es | Aceptada | — |
| A2 | Variante 1 | es | Rechazada | `verbal-mayoria=1` |
| A3 | Variante 2 | es | Rechazada | `verbal-mayoria=1` |
| A4 | Variante 3 | es | Rechazada | `verbal-mayoria=1` |
| A5 | Insulina | en | Aceptada | — |
| A6 | Variante 1 | en | Aceptada | — |
| A7 | Variante 2 | en | Aceptada | — |
| A8 | Variante 3 | en | Aceptada | — |
| A9 | Insulina | es | Aceptada | — |
| A10 | Variante 1 | es | Rechazada | `verbal-mayoria=1` |
| A11 | Variante 2 | es | 429 | — |
| A12 | Variante 3 | es | Rechazada | `verbal-mayoria=1` |
| A13 | Insulina | en | Aceptada | — |
| A14 | Variante 1 | en | 429 | — |
| A15 | Variante 2 | en | Rechazada | `verbal-most=1` |
| A16 | Variante 3 | en | 429 | — |

**Los seis rechazos son legítimos.** Los cinco en español dicen, casi con las mismas palabras, que la confianza media «indica que el predictor asigna una alta confianza a la mayoría de los residuos». El inglés dice «very high confidence to most local structural regions». Ambos deducen una distribución a partir de una media, que el prompt ya prohibía. Ningún rechazo se debió a una cifra (`figure-*`): **no hay falso positivo del verificador** y no se cambió. Esto encaja con el supuesto del diagnóstico offline: los tres rechazos de la interfaz del 1 nov fueron probablemente este mismo patrón.

## Ajuste del prompt

Dos patrones se repitieron, así que se ajustó el prompt, no el verificador:

1. Media → mayoría de residuos (6 de 6 rechazos). Regla nueva: describir la confianza local media solo como un promedio de toda la cadena que cae en una banda de la escala, sin atribuirla a residuos, regiones o partes de la cadena. El glosario de pLDDT añade: «una media es un promedio de la cadena, no un recuento de residuos confiados».
2. Medidas ausentes. A5 (insulina, en) dijo «No protected positions were specified, so the design draft contains zero fixed positions»: el análisis no envía ese hecho, lo tomó del glosario y escribió el valor en palabras, que el verificador no lee. Regla nueva: explicar solo las medidas presentes; las del glosario que falten se omiten, sin darles valor, ni siquiera cero o ninguno.

No se añade ninguna cifra ni cota nueva y no cambian `SCALE_FIGURES`, el contrato ni los códigos de error. La prueba `test_prompt_keeps_a_mean_whole_and_skips_absent_measures` fija las frases en es y en.

## Tanda B: prompt ajustado

16 llamadas a 10 s de intervalo: **16 aceptadas**, ningún rechazo ni 429. Ninguna salida de insulina menciona posiciones protegidas, y ninguna atribuye confianza a «la mayoría» ni a parte de los residuos.

## Lectura crítica de las salidas aceptadas

Criterios: cada cantidad coincide con los hechos (valor, unidad y medida a la que se atribuye); no hay proporciones deducidas; no hay afirmaciones de plegamiento, estabilidad o función de la proteína. «Precisión global del pliegue modelado» describe el modelo del predictor según el glosario y no cuenta como afirmación de plegamiento.

| # | Caso | Cantidades | Proporciones | Plegamiento / estabilidad / función | Veredicto |
|---|---|---|---|---|---|
| A1 | Insulina es | Todas exactas; inestabilidad 40,33 frente a la cota 40 del glosario | Ninguna | Ninguna | Correcta; «índice alifático… proporción de cadenas laterales» es impreciso (es volumen relativo) |
| A5 | Insulina en | Exactas | Ninguna | Ninguna | **Incorrecta**: inventa «zero fixed positions», un hecho no enviado |
| A6 | Variante 1 en | Exactas; cita las bandas 70–90 y >90 como definición, sin aplicarlas a residuos | Ninguna | Ninguna | Correcta |
| A7 | Variante 2 en | Exactas | Ninguna | Ninguna | Casi correcta: «matching the reference length» no está en los datos |
| A8 | Variante 3 en | Exactas; «just over one angstrom» es una paráfrasis cierta de 1,211 | Ninguna | Ninguna | Correcta con ambigüedad: «very high confidence for individual residues» aplicado a la media |
| A9 | Insulina es | Exactas | Ninguna | Niega inferir estabilidad | Correcta; «proporción» alifática y «en bloques» (es ventana deslizante) imprecisos |
| A13 | Insulina en | Exactas | Ninguna | Ninguna | Correcta |
| B1 | Insulina es | Exactas; cota 40 bien atribuida | Ninguna | Ninguna | Correcta |
| B2 | Variante 1 es | Exactas | «confianza promedio… a lo largo de toda la cadena» | Ninguna | Correcta |
| B3 | Variante 2 es | Exactas | Ninguna | Ninguna | Correcta |
| B4 | Variante 3 es | Exactas | Ninguna | Ninguna | Imprecisa: atribuye el pLDDT a la «estructura global» (es confianza local) y dice «alta» en vez de «muy alta» |
| B5 | Insulina en | Exactas | Ninguna | Ninguna | Correcta |
| B6 | Variante 1 en | Exactas; banda «very high» correcta para 93,39 | Ninguna | Ninguna | Correcta con matices: «close geometric similarity» es un juicio cualitativo y «when aligned» no describe la identidad posicional |
| B7 | Variante 2 en | Exactas | Ninguna | Ninguna | Correcta |
| B8 | Variante 3 en | Exactas; banda correcta para 92,92 | Ninguna | Ninguna | Correcta |
| B9 | Insulina es | Exactas | Ninguna | Niega inferir función | Correcta; «proporción» alifática imprecisa |
| B10 | Variante 1 es | Exactas; «banda de muy alta confianza» correcta | Ninguna | Ninguna | Correcta |
| B11 | Variante 2 es | Exactas | Ninguna | Ninguna | Correcta |
| B12 | Variante 3 es | Exactas | Ninguna | Ninguna | Correcta |
| B13 | Insulina en | Exactas | Ninguna | Niega inferir función | Correcta |
| B14 | Variante 1 en | Texto idéntico a B6 (temperatura 0,2) | Ninguna | Ninguna | Igual que B6 |
| B15 | Variante 2 en | Exactas | Ninguna | Ninguna | Correcta |
| B16 | Variante 3 en | Exactas | Ninguna | Ninguna | Correcta |

Resumen: en las 23 aceptadas, **todas las cifras coinciden** con los hechos y con la medida correcta, y ninguna afirma que la proteína se pliegue, sea estable o tenga función. Hay una incorrecta (A5, hecho inventado en palabras, solo con el prompt anterior) y varias imprecisiones cualitativas sin cifra que el verificador no puede ver: proporción frente a volumen alifático, pLDDT llamado global y juicios como «close similarity».

## Límites

- Muestra pequeña: 32 llamadas, temperatura 0,2 y un solo modelo. Que la tanda B tuviera 16 de 16 no garantiza que no vuelvan los rechazos ni las inferencias sin cifras.
- Se midió el camino de dominio local, no la web publicada ni el flujo con sesión real. El ajuste de prompt está **sin publicar**; la interfaz pública sigue con el prompt anterior.
- El verificador no lee números escritos en palabras («zero», «cero») ni juicios cualitativos. No se amplió: «por encima de cero» es una frase legítima del glosario, y prohibir «cero» crearía falsos positivos. Queda como opción a decidir con más salidas reales.
- Los 429 con 4 s de intervalo son cuota del plan gratuito de Groq; con 10 s no aparecieron. El presupuesto propio de la app (6/min por identidad) es más estricto.

## Evidencia y pruebas

Salidas completas (públicas, sin secuencia ni clave) en `backend/artifacts/assistant-real-2026-11-01/`, ignorado por Git: `before-prompt.json`, `after-prompt.json` e `insulin-facts.json`. Se comprobó que no contienen la clave.

`.venv/Scripts/python -m unittest discover -s tests`: **153 descubiertas, 134 correctas y 19 SQL omitidas** sin base.
