# Correspondencias SIFTS autenticadas

Implementadas localmente el **25 de octubre de 2026** tras la aprobación expresa del propietario de la maqueta y el suministro SIFTS. `app/sifts.py` adquiere/normaliza fuentes; `app/residue_mapping.py` sirve la misma función de dominio en REST y ReferenceService. Sin tablas, migraciones, cambio de CSP, modelos ni predicción. La integración visual aprobada sigue en desarrollo; publicación pendiente.

## Contrato aprobado e implementado localmente

`GET /v1/pdb/{pdb_id}/residue-mapping?accession=P01308&sequence_version=1` y `ReferenceService/GetResidueMapping` requieren JWT. Petición RPC: `pdb_id`, `accession`, `sequence_version`. PDB de cuatro caracteres en mayúscula, accession canónica (sin isoformas en este contrato) y versión positiva conocida. No se recibe secuencia ni se envía identidad del usuario a los proveedores. Respuesta tipada: `mapping` v1, `reference_sequence_version` y `reference_sequence_sha256`; sus nombres JSON son camelCase. Protobuf conserva `auth_seq_id` opcional: el cliente convierte ausencia a null y conserva texto exacto. No cambia el esquema interno v1 ni los números de campos anteriores.

Se consulta UniProt en cada solicitud, también con SIFTS en caché. Deben coincidir accession, versión solicitada y todos los residuos mapeados; el SHA-256 cubre la secuencia completa ASCII mayúscula, sin espacios. Filtra filas por accession exacta. Una ausencia legítima devuelve filas vacías; una fuente ausente, fallida o incoherente produce error. El navegador debe comprobar el hash completo y retirar el vínculo al editar, cambiar referencia/modelo o cerrar sesión. El hash es acuerdo sobre bytes, no firma del proveedor.

Cada operación consume una consulta del presupuesto existente de referencias (20/minuto por identidad y proceso por omisión), compartido con búsqueda/detalle UniProt REST/RPC. Plazo de 20 s para UniProt más adquisición SIFTS/PDBe, después de resolver identidad; se conserva cancelación. Sin promesa de cuota global entre instancias.

La caché vive solo en el proceso: PDB público como clave, documento normalizado con sus fechas, TTL de 300 s, máximo cuatro entradas/4 MB serializados en total. No guarda identidades ni secuencias. Documentos de más de 5.000 filas o 2 MB no se cachean; eso no recorta la respuesta. UniProt se vuelve a comprobar aunque la correspondencia esté en caché. No hay caché SQL ni resultados negativos convertidos en ausencia.

Respuesta completa REST/JSON y protobuf/JSON y binario acotados a **2.000.000 bytes**, envoltura incluida. Un resultado mayor se rechaza, sin truncamiento ni segmentación implícita. La adquisición interna de hasta10MB no aumenta el límite de transporte.

| Situación | REST | RPC | Código estable |
|---|---|---|---|
| Sin identidad | 401/503 | UNAUTHENTICATED/FAILED_PRECONDITION | Códigos de identidad existentes |
| Petición inválida | 422 | INVALID_ARGUMENT | `sifts-invalid-request` o validación de campos |
| Versión o residuos incompatibles | 409 | FAILED_PRECONDITION | `sifts-reference-version`, `sifts-reference-mismatch` |
| Fuente ausente | 404 | NOT_FOUND | `sifts-not-found` o `uniprot-not-found` |
| Cuota agotada | 429 | RESOURCE_EXHAUSTED | `uniprot-rate-limit`, `sifts-provider-rate-limit` |
| Respuesta completa demasiado grande | 413 | RESOURCE_EXHAUSTED | `sifts-response-too-large` |
| Fuente inválida, excesiva o caída | 502 | UNAVAILABLE | Códigos SIFTS/UniProt sin cuerpo del proveedor |
| Plazo agotado | 504 | DEADLINE_EXCEEDED | `sifts-timeout` o timeout UniProt |

Respuestas no-store y correlación existentes. No hay cambios de permisos SQL o persistencia privada.

## Fuentes y normalización

Solo admite un identificador PDB de cuatro caracteres en mayúscula, validado antes de cualquier petición. Construye dos URL HTTPS fijas, sin aceptar URL del usuario:

- `https://ftp.ebi.ac.uk/pub/databases/msd/sifts/xml/{id}.xml.gz`
- `https://www.ebi.ac.uk/pdbe/api/pdb/entry/residue_listing/{id}`

El XML SIFTS aporta correspondencias explícitas por residuo entre PDB y UniProt. El listado PDBe confirma entidad, cadena interna, cadena de autor, número interno, identificador de autor textual o null, inserción y nombre del residuo. Solo una identidad corroborada produce una fila. No se expanden intervalos ni se alinean índices por longitud; no se rellenan huecos. Un identificador de autor como `-2` o `custom` permanece texto y su inserción se conserva separada.

`normalise_mapping` produce el esquema interno v1 compatible con `residueMappingSchema` del frontend: `version`, `pdbId`, `provenance` y `rows`. Cada fila conserva accession exacta, posición UniProt, residuo, segmento y dirección estructural. Mantiene copias de cadenas como alternativas, en lugar de escoger una silenciosamente. Rechaza identidades duplicadas ambiguas y anotaciones contradictorias. La ausencia explícita `Not_Observed` no se confunde con el `observed_ratio` de PDBe: las coordenadas efectivamente observadas y el modelo los determina el visor.

El listado completo debe asignar una sola entidad/cadena de autor a cada cadena interna, y una sola identidad de autor/inserción/nombre a cada posición interna. Las copias de cadenas deben coincidir en el residuo de una misma accession/posición UniProt. La ausencia anotada pertenece a la dirección estructural y debe coincidir aunque cambie la referencia. Se conservan referencias distintas y segmentos solapados coincidentes, sin convertir diferencias legítimas entre accessions en contradicciones.

La función de adquisición no certifica por sí sola la versión/hash de UniProt; esa comprobación pertenece al servicio descrito arriba. Las filas no validan la identidad de posiciones ajenas a su cobertura. El [núcleo frontend](../../frontend/docs/references.md) comprueba el digest completo antes de habilitar selección; su conexión visual está en desarrollo.

## Límites y transporte

`fetch_mapping` recibe un cliente HTTP inyectado; construye peticiones nuevas, sin heredar Authorization, API keys, cookies o autenticación del cliente. No sigue redirecciones y solicita representación HTTP sin compresión de transporte. La compresión del archivo `.xml.gz` se procesa por separado, con CRC y fin del flujo comprobados.

Límites internos por omisión: 2 MB del archivo comprimido, 10 MB del XML expandido y 10 MB del listado JSON; 100.000 residuos/filas, 750.000 elementos XML y profundidad 32. Descarga en bloques acotados, corta al superar tamaño anunciado o real y limita la expansión antes de reservar toda la salida. Rechaza gzip truncado, miembros concatenados/datos sobrantes, DTD (también con XML UTF-16), estructura XML no admitida, claves JSON repetidas, valores no finitos y datos incoherentes. Un plazo de 20 s cubre ambas solicitudes; el análisis comprueba cooperativamente el mismo plazo. La cancelación externa se propaga y los flujos se cierran.

Las categorías `SiftsError` son códigos estables, sin cuerpos de proveedor ni trazas: solicitud inválida, fuente ausente, límite del proveedor, tamaño excesivo, respuesta inválida, proveedor fallido/inaccesible y timeout. Una fuente caída o inválida no devuelve una falsa lista vacía. Traducción REST/RPC en la tabla superior.

Estos límites de **adquisición** no amplían el máximo global de **2.000.000 bytes por mensaje RPC**. El servicio comprueba el tamaño final y lo rechaza expresamente; no trunca filas. La caché es únicamente local al proceso.

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

Después de activar el servicio local: 104 pruebas totales, 92 ejecutadas correctamente y 12 SQL omitidas. Incluyen REST, Connect y gRPC-Web, identidad antes de proveedor, cuota compartida, referencia completa/versiones, caché/expiración, respuestas demasiado grandes y timeout con cierre del flujo. UniProt ahora descarga en bloques acotados antes de analizar, con peticiones sin credenciales heredadas/redirecciones y representación HTTP sin compresión. Buf lint y regeneración reproducibles; dos espacios finales emitidos por el generador Python se conservan sin editar código generado a mano.

Smoke de Connect local con **identidad simulada** y tres fuentes HTTPS reales (UniProt, SIFTS XML y PDBe): HTTP200,102filas,SV1 y hash completo99603f8f…; JSONprotobuf16.713bytes. Respuesta ignorada en `artifacts/sifts/1trz.rpc.json`. No prueba JWT del propietario ni producción. Smoke offline del frontend con coordenadas reales BinaryCIF:102recorridos posición→loci→posición,51posiciones UniProt únicas; cambio fuera de cobertura rechazado porhash. No nueva inferencia ni cuenta externa.

La batería completa del backend se ejecuta al cerrar; las pruebas PostgreSQL sin base configurada se declaran omitidas y no equivalen a verificación SQL nueva. No hay cambios de acceso a datos o migraciones en este módulo.

## Pendiente para cerrar S5

Completar e integrar la interfaz aprobada, comprobar selección/vigencia de extremo a extremo y consulta con identidad real dentro de su alcance autorizado. No hace falta repetir migraciones. Publicar requiere autorización nueva: backend con GetResidueMapping primero, después actualizar el pin de buf.gen.yaml y regenerar/confirmar frontend, al final publicar frontend. El pin remoto actual todavía no tiene el método.

Fuentes primarias: [SIFTS](https://www.ebi.ac.uk/pdbe/docs/sifts/), [PDBe API](https://www.ebi.ac.uk/pdbe/api/doc/pdb.html), [HTTPX streaming](https://www.python-httpx.org/async/#streaming-responses), [seguridad XML de Python](https://docs.python.org/3/library/xml.html#xml-security), [descompresión acotada zlib](https://docs.python.org/3/library/zlib.html#zlib.Decompress.decompress).
