# Adquisición interna de correspondencias SIFTS

Implementada localmente el **25 de octubre de 2026** en `app/sifts.py`. Prepara el suministro de correspondencias al núcleo del frontend. **No está montada en FastAPI ni ReferenceService**: no añade endpoint, protobuf, tabla, migración o cambio de CSP. No es predicción ni ejecuta modelos.

## Fuentes y normalización

Solo admite un identificador PDB de cuatro caracteres en mayúscula, validado antes de cualquier petición. Construye dos URL HTTPS fijas, sin aceptar URL del usuario:

- `https://ftp.ebi.ac.uk/pub/databases/msd/sifts/xml/{id}.xml.gz`
- `https://www.ebi.ac.uk/pdbe/api/pdb/entry/residue_listing/{id}`

El XML SIFTS aporta correspondencias explícitas por residuo entre PDB y UniProt. El listado PDBe confirma entidad, cadena interna, cadena de autor, número interno, identificador de autor textual o null, inserción y nombre del residuo. Solo una identidad corroborada produce una fila. No se expanden intervalos ni se alinean índices por longitud; no se rellenan huecos. Un identificador de autor como `-2` o `custom` permanece texto y su inserción se conserva separada.

`normalise_mapping` produce el esquema interno v1 compatible con `residueMappingSchema` del frontend: `version`, `pdbId`, `provenance` y `rows`. Cada fila conserva accession exacta, posición UniProt, residuo, segmento y dirección estructural. Mantiene copias de cadenas como alternativas, en lugar de escoger una silenciosamente. Rechaza identidades duplicadas ambiguas y anotaciones contradictorias. La ausencia explícita `Not_Observed` no se confunde con el `observed_ratio` de PDBe: las coordenadas efectivamente observadas y el modelo los determina el visor.

El listado completo debe asignar una sola entidad/cadena de autor a cada cadena interna, y una sola identidad de autor/inserción/nombre a cada posición interna. Las copias de cadenas deben coincidir en el residuo de una misma accession/posición UniProt. La ausencia anotada pertenece a la dirección estructural y debe coincidir aunque cambie la referencia. Se conservan referencias distintas y segmentos solapados coincidentes, sin convertir diferencias legítimas entre accessions en contradicciones.

El módulo no obtiene ni certifica la versión/hash de la secuencia UniProt completa. El futuro servicio tendrá que comprobar esa referencia y la procedencia exacta antes de vincular el mapeo al editor. Las filas de una referencia no validan por sí mismas la identidad de posiciones ajenas a su cobertura. El [núcleo frontend](../../frontend/docs/references.md) y el puente de selección todavía no están activados en Biblioteca.

## Límites y transporte

`fetch_mapping` recibe un cliente HTTP inyectado; construye peticiones nuevas, sin heredar Authorization, API keys, cookies o autenticación del cliente. No sigue redirecciones y solicita representación HTTP sin compresión de transporte. La compresión del archivo `.xml.gz` se procesa por separado, con CRC y fin del flujo comprobados.

Límites internos por omisión: 2 MB del archivo comprimido, 10 MB del XML expandido y 10 MB del listado JSON; 100.000 residuos/filas, 750.000 elementos XML y profundidad 32. Descarga en bloques acotados, corta al superar tamaño anunciado o real y limita la expansión antes de reservar toda la salida. Rechaza gzip truncado, miembros concatenados/datos sobrantes, DTD (también con XML UTF-16), estructura XML no admitida, claves JSON repetidas, valores no finitos y datos incoherentes. Un plazo de 20 s cubre ambas solicitudes; el análisis comprueba cooperativamente el mismo plazo. La cancelación externa se propaga y los flujos se cierran.

Las categorías `SiftsError` son códigos estables, sin cuerpos de proveedor ni trazas: solicitud inválida, fuente ausente, límite del proveedor, tamaño excesivo, respuesta inválida, proveedor fallido/inaccesible y timeout. Una fuente caída o inválida no devuelve una falsa lista vacía. Todavía no se decide su traducción REST/RPC.

Estos límites de **adquisición** no amplían el máximo global de **2.000.000 bytes por mensaje RPC**. El futuro contrato deberá comprobar el tamaño final y rechazarlo expresamente o acordar segmentación; no truncar filas silenciosamente. No hay caché persistente, presupuesto por usuario o autorización nueva activos porque la función aún no es una ruta pública.

## Comprobación y repetición

22 pruebas con transporte simulado: reconciliación de los [ocho residuos públicos reducidos](../tests/fixtures/sifts/README.md), cadenas repetidas, identificadores de autor/inserciones/isoformas, ausencias, datos contradictorios, segmentos solapados, estructura de XML, límites, entidades/DTD, integridad gzip, credenciales por defecto, redirecciones, JSON inválido, plazo y cancelación. No usan cuentas ni proveedores reales.

Ejecutar desde backend:

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -p test_sifts.py -v
.venv/Scripts/python.exe -m scripts.sifts_smoke 1TRZ --xml tests/fixtures/sifts/1trz.xml --listing tests/fixtures/sifts/1trz.json
```

La comprobación manual pública (dos GET, sin credenciales ni secuencias privadas) se ejecuta deliberadamente con:

```powershell
.venv/Scripts/python.exe -m scripts.sifts_smoke 1TRZ
```

La utilidad usa certificados/nombre de host verificados con el almacén de confianza del sistema; no carga `.env`. Guarda el documento en `artifacts/sifts/1trz.mapping.json`, ignorado, e imprime solo resumen, tamaño y hash. No es una prueba de carga.

El 25 de octubre se obtuvieron **102 correspondencias** para 1TRZ/P01308, cadenas A/B/C/D, documento SIFTS de 2026-10-18. El JSON de esa lectura pasó el esquema v1 real del frontend. P01308:90 conserva las alternativas A/C; el ensayo del enlace con un subconjunto **sintético** de direcciones dio ambigüedad sin cadena y selección única al elegir A. No acredita un visor conectado al backend ni una nueva observación de coordenadas. Las pruebas previas del frontend sí analizaron su BinaryCIF público real.

Las validaciones adicionales se comprobaron después contra el XML/listado completos ya descargados: conservan las 102 filas sin nuevas solicitudes. Batería backend de este cierre: 93 pruebas totales, 81 ejecutadas correctamente y 12 SQL omitidas sin base configurada.

La batería completa del backend se ejecuta al cerrar; las pruebas PostgreSQL sin base configurada se declaran omitidas y no equivalen a verificación SQL nueva. No hay cambios de acceso a datos o migraciones en este módulo.

## Pendiente para activar S5

Revisar contrato autenticado REST/RPC, límites y cuota por identidad; comprobar versión/hash UniProt; acordar la composición visual/selector de cadena; conectar el origen y la selección sin aceptar respuestas antiguas. Después, pruebas de recorrido y una consulta autenticada real. No hace falta repetir migraciones ya aplicadas por el propietario. La publicación sigue necesitando autorización.

Fuentes primarias: [SIFTS](https://www.ebi.ac.uk/pdbe/docs/sifts/), [PDBe API](https://www.ebi.ac.uk/pdbe/api/doc/pdb.html), [HTTPX streaming](https://www.python-httpx.org/async/#streaming-responses), [seguridad XML de Python](https://docs.python.org/3/library/xml.html#xml-security), [descompresión acotada zlib](https://docs.python.org/3/library/zlib.html#zlib.Decompress.decompress).
