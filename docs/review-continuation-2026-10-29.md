# Revisión local del backend — 29 oct 2026

Alcance autorizado: revisar el backend y continuar el desarrollo con pruebas necesarias, en paralelo a la revisión de GPU y rendimiento del frontend. Trabajo sobre `dev`, HEAD inicial `cbff97e`, sin commits, push, cambios de servicios o migraciones remotas. No se modificaron contratos, rutas, dependencias o SQL.

## Fallos demostrados y corregidos

**El verificador del asistente perdía el signo.** Con el hecho `GRAVY = -0,532`, una respuesta simulada que decía `GRAVY = 0,532` devolvía 200: la expresión que extraía cifras ignoraba el menos. Esto puede invertir el sentido de un descriptor aunque las cifras parezcan coincidir.

En `app/assistant.py`, se conserva el signo `+`, `-` y el menos tipográfico `−`; este último se normaliza al leer el decimal. La excepción de enteros pequeños se limita a conteos de **0 a 20**: no permite números negativos arbitrarios. La respuesta inventada ahora devuelve 502 `assistant-unverified-figure`; siguen aceptándose el valor negativo suministrado y sus redondeos compatibles. No se afirma que esto valide toda la semántica del texto.

**Un hecho numérico largo podía romper la verificación.** Una entrada de cien nueves seguida de `.12`, válida dentro del límite del campo, provocaba `decimal.InvalidOperation` al cuantizarla mientras se comprobaba otra cifra. Ahora esa cuantización no representable se descarta sin interrumpir el verificador; una igualdad exacta se conserva. No se amplían los valores aceptados ni se altera el contrato.

Se añadieron tres regresiones en `tests/test_assistant.py`. Antes de corregir producción: **dos fallos y un error** en la batería del asistente; después: **16/16 correctas**. Una regresión atraviesa la ruta REST con Auth y Groq simulados; las otras fijan signos/redondeos y el decimal largo.

## Verificación ejecutada

Python local **3.13.14**. El Python de `.venv` depende de la instalación de Microsoft Store y no arrancó dentro del sandbox; las ejecuciones locales con escalación fueron autorizadas automáticamente. No se instaló ni cambió el intérprete. El aviso previo de deprecación Starlette/httpx sigue presente.

| Ejecución | Resultado observado |
|---|---|
| Batería inicial, sin base configurada | `Ran 130 tests in 2.563s`, `OK (skipped=17)` |
| Regresiones antes del arreglo | `Ran 16 tests in 0.673s`, 2 fallos y 1 error |
| Asistente después del arreglo | `Ran 16 tests in 0.659s`, `OK` |
| Batería completa después del arreglo | `Ran 133 tests in 2.157s`, `OK (skipped=17)` |
| `scripts/test-postgres.ps1`, PostgreSQL 18 temporal | `Ran 17 tests in 21.778s`, `OK`; servidor detenido al terminar |
| `git diff --check` | Correcto |

**Corrección de recuento:** las 130 iniciales eran **130 descubiertas: 113 ejecutadas y 17 omitidas**, no 130 correctas más 17 omitidas. Tras las tres regresiones son **133 descubiertas: 116 ejecutadas y 17 omitidas** en la batería sin base. Las 17 SQL se ejecutaron aparte correctamente; entre ambas tandas se cubren **133 pruebas únicas**, sin sumar otra vez las 16 del asistente.

El runner SQL creó un clúster nuevo en `tmp/pg-c8425ff22a294a67bcac6110843cbd4c`, aplicó las seis migraciones del repositorio y ejecutó propiedad/aislamiento, anónimo, transferencia, cascada, rollback, persistencia de laboratorio/historial, guardias monótonas, vistas previas e idempotencia, y carreras con un único ganador. Conserva el clúster y logs ignorados para diagnóstico; el proceso quedó detenido. No abrió una base existente ni leyó credenciales remotas.

## Revisión y límites

Se inspeccionaron identidad contra Supabase, rutas REST/RPC, presupuesto compartido de referencias por llamante, adquisición/caché SIFTS, persistencia y validación de proyectos/laboratorio/historial, tratamiento de errores, configuración y migraciones. No apareció otro fallo que justificase modificar contratos o SQL en esta pasada.

Las 17 SQL prueban PostgreSQL real con `auth.uid()` emulado. **No prueban Supabase Auth → PostgREST → RLS desplegados.** Auth, Groq y proveedores de las unitarias son dobles de transporte; no se realizó una interpretación real ni una prueba con JWT del propietario. El aislamiento por ID con dos cuentas reales y la lectura científica de salidas auténticas continúan pendientes de su recorrido correspondiente.

La coordinación del frontend tiene el carril de navegador y mediciones. Al terminar estas pruebas se liberó CPU y no se iniciaron navegadores ni otra batería durante sus medidas. La coordinación registra por separado sus comprobaciones públicas de producción; no se confunden con esta evidencia local.
