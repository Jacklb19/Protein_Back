# Carga sostenida de la API local

Medición ejecutada el **25 de octubre de 2026**, revisada al continuar el 26. Cubre el procesamiento HTTP local de RNF-04 con identidad y almacenamiento simulados; no certifica latencia de Supabase, SQL/RLS, Vercel o proveedores científicos.

`scripts/audit_local_api.py` inicia la aplicación real con configuración explícita y Uvicorn sobre127.0.0.1:5335. El cliente HTTP también es real, con conexiones de loopback, sin proxy ni redirecciones. El transporte de cualquier proveedor es MockTransport: acepta exclusivamente identidad/listado vacío de una URL `.invalid` y rechaza cualquier otro destino/operación. Sin proyectos del propietario, escrituras, Groq, UniProt, SIFTS, inferencia o carga externa.

Tras una consulta de calentamiento excluida, alterna cinco casos: saludREST, estadoConnect, identidadREST, listado de proyectosREST y listadoConnect. Cada respuesta debe ser200, tener correlación y `Cache-Control: no-store`. No se desactiva la autorización: se usa un token ficticio validado por la respuesta simulada de identidad. El SQL y la verificación remota del JWT no se ejecutan.

| Medida | Resultado |
|---|---:|
| Solicitudes completadas | 500,100por caso |
| Duración medida | 300,006s |
| Frecuencia media | 99,998solicitudes/minuto |
| ErroresHTTP/transporte/correlación/caché | 0 |
| Latencia p50/p95 | 7,279/16,262ms |
| Latencia máxima | 159,684ms |
| Mayor retraso del planificador | 1.324,825ms |
| CPU del proceso, servidor más cliente | 4,562s |

Entorno: Windows11, Python3.13.14,16procesadores lógicos. Había trabajo de verificación de frontend en el equipo; no se controlaron temperatura, energía ni aplicaciones ajenas. El retraso del planificador impide afirmar que todas las solicitudes salieron exactamente cada600ms; la tasa indicada es la media real sobre los cinco minutos. No se midió pico de RAM ni capacidad máxima. Tampoco se generaliza el resultado al runtime objetivoPython3.14 o al despliegue remoto.

Las500observaciones y el resumen están en `artifacts/quality/local-api-load.json`, ignorado. Revisados recuentos, cinco grupos de100 y ausencia de registros fallidos. El servidor quedó cerrado;5335sin escucha al revisar. Hubo antes un humo de tres segundos/cinco consultas para verificar el propio recorrido; sus cifras no se mezclan con la medición sostenida.

Repetir deliberadamente desde backend:

```powershell
.venv/Scripts/python.exe -m scripts.audit_local_api
```

El comando predeterminado tarda cinco minutos y guarda un nuevo informe local. Solo admite puertos5335–5339, duración1–300s y frecuencia1–100/minuto; no acepta una URL remota. Para comprobar arranque/cierre sin repetir el ensayo: `--seconds 3 --rate 100`. Cualquier fallo hace salir con código1. Esto acredita el carril local indicado; el rendimiento con proveedores reales sigue pendiente de una medición autorizada y acotada, sin dirigir carga a servicios compartidos.
