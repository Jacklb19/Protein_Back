# Carga sostenida de la API local

Medición del procesamiento HTTP local para RNF-04 (100 peticiones por minuto durante 5 minutos). Se hace en dos modos: con identidad y almacenamiento simulados, y con un PostgreSQL local real con todas las migraciones y las políticas RLS. No certifica la latencia de Supabase, de Vercel ni de los proveedores científicos, ni la capacidad máxima.

## Qué se mide

`scripts/audit_local_api.py` inicia la aplicación real con configuración explícita y Uvicorn sobre `127.0.0.1` (puerto 5296 o 5335–5339). El cliente HTTP también es real, con conexiones de loopback, sin proxy ni redirecciones. Tras una consulta de calentamiento excluida, alterna cinco casos: salud REST, estado Connect, identidad REST, listado de proyectos REST y listado Connect. Cada respuesta debe ser 200, llevar correlación y `Cache-Control: no-store`. No se desactiva la autorización: se usa un token ficticio validado por una respuesta simulada de identidad. Todo el transporte hacia proveedores es `MockTransport` y acepta exclusivamente la identidad y el listado de una URL `.invalid`: no hay Groq, UniProt, SIFTS, inferencia ni carga contra servicios compartidos.

| Pieza | Modo simulado | Modo PostgreSQL |
|---|---|---|
| Aplicación FastAPI, Uvicorn y cliente de loopback | Reales | Reales |
| Listado de proyectos | Respuesta simulada (lista vacía) | `pf_list_projects` real con el rol `authenticated` y RLS, sobre un PostgreSQL 18 desechable en `127.0.0.1` |
| Supabase Auth | Simulada | Simulada (usuario fijo que existe en `auth.users`) |
| PostgREST | No interviene | No está instalado: se emula con una sesión `psql` persistente que hace lo mismo por llamada (transacción, `set local role authenticated`, `set local request.jwt.claim.sub` y la RPC) |

En el modo PostgreSQL, el script aplica `tests/postgres_bootstrap.sql` y todas las migraciones en orden, siembra con `pf_save_project` 51 usuarios y 1.012 proyectos (12 del usuario del ensayo y 20 por cada uno de los otros 50, con secuencias de 300 residuos) y comprueba antes de empezar que, con el rol `authenticated`, la tabla `projects` solo devuelve las 12 filas propias. Durante la carga, cada listado debe traer exactamente esas 12 filas.

## Resultado

Misma carga en ambos modos: 100 solicitudes por minuto durante 300 s (500 solicitudes, 100 por caso), sin errores de HTTP, transporte, correlación, caché ni recuento.

| Medida | PostgreSQL local | Simulado |
|---|---:|---:|
| Frecuencia media | 100,0 por minuto | 100,0 por minuto |
| Latencia p50 / p95 / p99 | 5,0 / 9,2 / 11,0 ms | 3,8 / 5,4 / 6,4 ms |
| Latencia máxima | 70,6 ms | 8,5 ms |
| Ida y vuelta a la base por listado (p50 / p95) | 2,4 / 3,1 ms | no aplica |

Los listados con base real tardan unos 4 ms más en la mediana que con almacenamiento simulado; la ida y vuelta a la base explica unos 2,4 ms y el resto no se atribuye (puede deberse a la emulación con `psql` o a la validación de 12 elementos con vista previa).

## Límites

- Una sola ejecución de cada modo, sin intervalo de variación, en un equipo con otras tareas abiertas y sin controlar la temperatura ni la energía.
- La carga es secuencial (una solicitud cada 600 ms, una sola conexión a la base): no mide concurrencia, agotamiento del pool, memoria ni capacidad máxima.
- No prueba Supabase Auth, PostgREST ni la red hacia Supabase o Vercel, ni escrituras, la apertura de un proyecto completo, SIFTS, el asistente o la predicción. 1.012 proyectos es un volumen pequeño.
- Ejecutado con Python 3.13, no con el 3.14 objetivo.

## Repetir

```bash
.venv/Scripts/python.exe -m scripts.audit_local_api --port 5296
```

El modo simulado tarda cinco minutos y guarda un informe en `artifacts/quality/local-api-load.json` (ignorado por Git). Solo admite los puertos 5296 y 5335–5339, una duración de 1 a 300 s y una frecuencia de 1 a 100 por minuto, y no acepta una URL remota; `--seconds 3 --rate 100` comprueba el arranque y el cierre sin repetir el ensayo. Para el modo PostgreSQL, con PostgreSQL 18 instalado, en PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\audit-local-api-postgres.ps1 --port 5296
```

Crea el clúster desechable, aplica el arranque y las migraciones, siembra los datos, mide cinco minutos y lo detiene. `-PostgresBin` y `-Port` cambian la instalación y el puerto del clúster, y `--owned-projects`, `--foreign-users` y `--foreign-projects` el volumen sembrado. Solo acepta una base `pf_load_audit` en loopback. Cualquier error, o un recuento distinto del esperado, hace salir con código 1.
