# Carga sostenida de la API con PostgreSQL local — 1 nov 2026

Medición de RNF-04 ejecutada el **1 de noviembre de 2026** entre las 21:41 y las 21:51 (hora de Colombia). Repite el ensayo de [carga HTTP local](local-api-load.md), pero el listado de proyectos se resuelve en un PostgreSQL 18 real y local con todas las migraciones del repositorio y sus políticas RLS. No hubo base remota, Supabase remoto, migraciones remotas ni carga contra servicios compartidos.

## Qué es real y qué no

| Pieza | En esta medición |
|---|---|
| Aplicación FastAPI, Uvicorn, cliente HTTP de loopback | Reales, igual que en la medición del 25 oct |
| PostgreSQL 18.0 en `127.0.0.1:55441`, base desechable `pf_load_audit` | Real, creado con `initdb` en `tmp/` y detenido al terminar |
| Migraciones `supabase/migrations/*.sql` y políticas RLS | Aplicadas en orden sobre `tests/postgres_bootstrap.sql`, el mismo arranque que usan las pruebas SQL |
| Función `pf_list_projects` y consulta del listado | Reales, ejecutadas con el rol `authenticated` en cada solicitud |
| Supabase Auth (verificación del JWT) | Simulada: devuelve un usuario fijo que sí existe en `auth.users` |
| PostgREST | No está instalado. Se emula con una sesión `psql` persistente que hace lo mismo por llamada: transacción, `set local role authenticated`, `set local request.jwt.claim.sub` y llamada a la RPC |
| Groq, SIFTS, UniProt, predicción | No intervienen en las rutas medidas; cualquier otro destino hace fallar el ensayo |

Datos sembrados antes de medir con `pf_save_project`: 51 usuarios y 1.012 proyectos, 12 del usuario del ensayo y 20 por cada uno de los otros 50; secuencias de 300 residuos. Antes de empezar, el script comprueba que `select count(*) from public.projects` con el rol `authenticated` devuelve solo 12: las políticas, sin el filtro propio de la función, ocultan las filas ajenas. Durante la carga, cada respuesta de listado REST y Connect debía traer exactamente 12 elementos (menos que la página de 30, para que una fila ajena filtrada se notara).

## Resultados

Misma carga que el 25 oct: 100 solicitudes por minuto durante 300 s, alternando salud REST, estado Connect, identidad REST, listado REST y listado Connect. Justo después se repitió el modo simulado en el mismo equipo y puerto para tener una comparación del mismo día.

| Medida | PostgreSQL local | Simulado, mismo día | Simulado, 25 oct |
|---|---:|---:|---:|
| Solicitudes completadas | 500 (100 por caso) | 500 | 500 |
| Duración medida | 300,001 s | 300,003 s | 300,006 s |
| Frecuencia media | 100,0/min | 99,999/min | 99,998/min |
| Errores HTTP, transporte, correlación, caché o recuento | 0 | 0 | 0 |
| Latencia p50 | 5,029 ms | 3,814 ms | 7,279 ms |
| Latencia p95 | 9,201 ms | 5,381 ms | 16,262 ms |
| Latencia p99 | 10,991 ms | 6,419 ms | no registrada |
| Latencia máxima | 70,621 ms | 8,469 ms | 159,684 ms |
| Mayor retraso del planificador | 17,290 ms | 17,108 ms | 1.324,825 ms |
| CPU del proceso Python | 3,531 s | 2,062 s | 4,562 s |

Por caso, p50/p95 en milisegundos:

| Caso | PostgreSQL local | Simulado, mismo día |
|---|---:|---:|
| Salud REST | 4,096 / 5,146 | 3,380 / 4,122 |
| Estado Connect | 4,416 / 6,074 | 3,592 / 4,878 |
| Identidad REST | 4,799 / 6,816 | 4,063 / 5,365 |
| Listado REST | 8,180 / 10,647 | 4,299 / 5,476 |
| Listado Connect | 8,054 / 9,855 | 3,845 / 5,600 |

Ida y vuelta a la base por cada una de las 200 llamadas al listado, medida dentro del proceso (incluye `psql`, la transacción, el cambio de rol, RLS y la serialización JSON): p50 2,421 ms, p95 3,077 ms, p99 3,946 ms y máximo 8,596 ms.

## Lectura

- Con base real y RLS no hubo errores y la frecuencia se sostuvo: 500 respuestas 200, todas con correlación y `Cache-Control: no-store`, y los 200 listados con exactamente las 12 filas propias.
- El listado tarda unos 4 ms más en la mediana que con almacenamiento simulado; la ida y vuelta a la base explica unos 2,4 ms. El resto no se atribuye: puede deberse a la emulación con `psql` o a la validación de 12 elementos con vista previa, que en el modo simulado no existe porque la lista llega vacía.
- Solo tres solicitudes superaron 12 ms: un listado REST a los 1,8 s (21,6 ms, primer listado tras el calentamiento, que solo llama a salud), una identidad a los 124,2 s (70,6 ms, ruta que no toca la base) y un listado REST a los 151,8 s (12,8 ms). No se diagnosticó la causa de esos picos.
- Las cifras del 25 oct eran más altas en ambos modos; ese día había otra verificación de frontend en el equipo. Por eso la comparación válida es la del mismo día.

## Límites

- Una sola ejecución de cada modo; sin repeticiones no hay intervalo de variación.
- La carga es secuencial, una solicitud cada 600 ms y una sola conexión a la base. No mide concurrencia, agotamiento del pool, capacidad máxima ni RAM.
- No prueba Supabase Auth, la verificación del JWT, PostgREST ni la red hasta Supabase o Vercel. El tiempo de un despliegue real será distinto.
- No cubre escrituras (`pf_save_project`, `pf_delete_project`), apertura de un proyecto completo, SIFTS, el asistente ni la predicción.
- 1.012 proyectos es un volumen pequeño. No se midió el plan de consulta con decenas de miles de filas.
- Entorno: Windows 11, Python 3.13.14 (no el 3.14 objetivo), 16 procesadores lógicos, PostgreSQL 18.0. Carga del procesador medida antes y entre ejecuciones: 7 %, 25 % y 7 %. Había otras sesiones de trabajo abiertas en el equipo, incluido otro clúster PostgreSQL de pruebas en el puerto 55439, sin carga intensa observada. No había batería E2E en el puerto 5190. No se controlaron temperatura ni energía.

## Repetir

Desde `backend`, con PostgreSQL 18 instalado en la ruta por defecto:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\audit-local-api-postgres.ps1 --port 5296
.venv/Scripts/python.exe -m scripts.audit_local_api --port 5296
```

El primero crea el clúster desechable, aplica arranque y migraciones, siembra los datos, mide cinco minutos y detiene el clúster; el segundo es el modo simulado. `-PostgresBin` y `-Port` cambian la instalación y el puerto del clúster; `--owned-projects`, `--foreign-users` y `--foreign-projects` cambian el volumen sembrado. El script solo acepta una base `pf_load_audit` en loopback, puertos de API 5296 o 5335–5339, hasta 300 s y hasta 100 solicitudes por minuto. Cualquier error, o un recuento distinto del esperado, hace salir con código 1.

Los resultados completos quedan en `artifacts/quality/local-api-load-postgres.json` y `artifacts/quality/local-api-load.json` (carpeta ignorada). La ejecución simulada de hoy reemplazó el archivo de observaciones del 25 oct; de aquella medición solo quedan las cifras publicadas en su documento, sin p99. Antes de esta medición hubo dos ensayos de humo de 3 s por modo para verificar el recorrido; sus cifras no se mezclan aquí. Al terminar, los puertos 5296 y 55441 quedaron sin escucha.
