# Referencias UniProt

`GET /v1/uniprot/search?query=...` y `GET /v1/uniprot/{accession}` (y los métodos equivalentes de `ReferenceService`), con JWT obligatorio. Búsqueda: 2–200 caracteres, diez referencias y truncated si hay más; acotar consulta en lugar de lotes. Accession canónico de 6/10 caracteres. Se validan nombre/organismo/revisión/longitud, secuencia/longitud exacta, versiones y PDB con método/resolución/cadenas; timestamp UTC. JSON camelCase. 404 inexistente, 422 consulta inválida, 429 cuota, 502 respuesta inválida/proveedor, 504 timeout.

Presupuesto propio: cada identidad autenticada dispone de `UNIPROT_REQUESTS_PER_MINUTE` consultas por minuto (20 por omisión, 0 desactiva el presupuesto), compartidas entre búsqueda y detalle, REST y gRPC-Web. Al agotarlo, la API responde 429 `uniprot-rate-limit` (`RESOURCE_EXHAUSTED`) sin llamar al proveedor. El recuento vive en el proceso: varias instancias multiplican el presupuesto, así que acota lo que una instancia reenvía y no es una cuota global.

Destino fijo rest.uniprot.org, query codificada, sin URLs del cliente. JWT/clave solo a Supabase para identidad; UniProt recibe Accept:application/json. No se envía el editor ni se busca automáticamente por secuencia. Timeout 12 s, respuesta limitada a 8 MB; sin reintentos/lotes ni caché persistente/service_role.

Las pruebas con transporte simulado cubren los límites, el aislamiento del JWT y los datos inconsistentes; el adaptador se probó además contra la [API pública](https://rest.uniprot.org/uniprotkb/P01308.json) con la ficha de la insulina (P01308, 110 residuos, PDB 1TRZ). Eso no garantiza la disponibilidad futura del proveedor.

workspace.source opcional: origin catalog/uniprot, accession, secuencia exacta, versiones conocidas y retrievedAt ISO con zona si existe. ProjectWrite valida asociación a activeSequence. Compatibilidad con snapshots sin source; editar retira procedencia en cliente. No certifica una cabecera FASTA arbitraria.

Mol* y RCSB pertenecen al frontend. La cobertura es una anotación, no un mapeo SIFTS ni un recuento de coordenadas; la correspondencia con las estructuras está en [SIFTS](sifts.md).
