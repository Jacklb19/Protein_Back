# Referencias UniProt

GET /v1/uniprot/search?query=... y GET /v1/uniprot/{accession}, JWT obligatorio según arquitectura. Búsqueda: 2–200 caracteres, diez referencias y truncated si hay más; acotar consulta en lugar de lotes. Accession canónico de 6/10 caracteres. Se validan nombre/organismo/revisión/longitud, secuencia/longitud exacta, versiones y PDB con método/resolución/cadenas; timestamp UTC. JSON camelCase. 404 inexistente, 422 consulta inválida, 429 cuota, 502 respuesta inválida/proveedor, 504 timeout.

Destino fijo rest.uniprot.org, query codificada, sin URLs del cliente. JWT/clave solo a Supabase para identidad; UniProt recibe Accept:application/json. No se envía el editor ni se busca automáticamente por secuencia. Timeout 12 s, respuesta limitada a 8 MB; sin reintentos/lotes ni caché persistente/service_role.

[API pública](https://rest.uniprot.org/uniprotkb/P01308.json) comprobada el 12 oct 2026: búsqueda 200, Insulin/Homo sapiens, P01308/110; detalle versión de entrada 284/secuencia 1 y PDB 1TRZ, X-ray, 1.60 A, A/C=90–110 y B/D=25–54. Prueba puntual de proveedor/parser, no sesión JWT/API publicada ni disponibilidad garantizada. Tres pruebas simuladas cubren límites, aislamiento JWT y datos inconsistentes.

workspace.source opcional: origin catalog/uniprot, accession, secuencia exacta, versiones conocidas y retrievedAt ISO con zona si existe. ProjectWrite valida asociación a activeSequence. Compatibilidad con snapshots sin source; editar retira procedencia en cliente. No certifica una cabecera FASTA arbitraria.

Mol*/RCSB pertenecen al frontend. Cobertura es anotación, no mapeo SIFTS ni recuento de coordenadas. Predicción/asistente separados y pendientes.
