# Historial del generador en Proyectos · propuesta de contrato

Preparada el **26 oct 2026**. **Decisiones tomadas el 27 oct 2026 por delegación** (sección 4): el propietario delegó en el coordinador de las sesiones las decisiones técnicas y de programación que no sean extremadamente importantes. **Fase backend implementada localmente el 27 oct** en backend `dev` (sección 5); **fase frontend pendiente** (sección 6). No se aplicó ninguna migración en Supabase, no hubo push, merge ni publicación, y el frontend no se tocó. Concreta la decisión 5 («historial completo del generador o solo exportación local») de la [propuesta RF-18 y persistencia](../../frontend/docs/prediction-storage-proposal.md#historial-del-generador), que el [plan vigente](../../frontend/docs/plan-desarrollo.md) deja como siguiente decisión antes de ampliar contratos.

Las citas `archivo:línea` de la sección 1 describen el estado **anterior** a la fase backend: backend `dev` en `df522e4` y frontend `dev` en `a7e244a`. Los archivos del generador y de Proyectos citados no tenían cambios locales al redactar esta propuesta; el rediseño en curso en el frontend puede desplazar líneas, no su contenido.

## 1. Estado de partida (implementado antes de esta propuesta)

### Generador RF-17

- Configuración fija en [`config.ts:1-8`](../../frontend/src/features/generator/config.ts): `version: 'proteinforge-candidates-v1'`, longitud 5–300, presupuesto 100–5.000 evaluaciones por brazo, semilla hasta `0xffffffff`, `retained: 5`, distancia mínima 0,1, `timeoutMs` 60 s y nombre de archivo `proteinforge-candidates.json`.
- Parámetros validados con Zod estricto en [`search.ts:7-13`](../../frontend/src/features/generator/search.ts): `length`, `budget`, `seed` (entero 1–2³²−1), intervalos `gravy` y `massDa` con `enabled/minimum/maximum`, al menos uno activo.
- Resultado en [`search.ts:15-17`](../../frontend/src/features/generator/search.ts): `SearchResult { schemaVersion: 1, methodVersion, parameters, guided, reference, references }`. Cada brazo (`SearchArm`) lleva `evaluations`, `matchingEvaluations`, `bestLoss`, `candidates` y `minimumPairDistance`; cada candidato, `sequence`, `descriptors` (masa, pI, inestabilidad, alifático, GRAVY, longitud), `loss` y `satisfies`.
- **Semillas:** el brazo guiado usa `parameters.seed`; el de referencia, `(seed XOR 0x9e3779b9) >>> 0`, o 1 si resulta cero ([`search.ts:80-81`](../../frontend/src/features/generator/search.ts)). Solo la semilla guiada aparece en el JSON; la de referencia se deriva de `methodVersion`. RNG xorshift32 no criptográfico ([`search.ts:20-24`](../../frontend/src/features/generator/search.ts)).
- **Orden y cantidad de candidatos:** archivo acotado de hasta 5 por brazo, ordenado por penalización y después por secuencia ASCII, con distancia de Hamming ≥ 0,1 entre retenidos ([`search.ts:52-63`](../../frontend/src/features/generator/search.ts)). La diversidad puede dejar menos de cinco. Máximo 10 candidatos por ejecución.
- **Exportación real:** `download()` serializa el `SearchResult` completo con `JSON.stringify(result, null, 2)` y lo descarga localmente; se desactiva si el resultado está desactualizado o en curso ([`GeneratorPage.tsx:25-27`](../../frontend/src/features/generator/GeneratorPage.tsx), [`GeneratorPage.tsx:40-45`](../../frontend/src/features/generator/GeneratorPage.tsx)).
- **Persistencia:** ninguna. La ejecución vive en memoria y recargar la descarta ([`GeneratorProvider.tsx:9`](../../frontend/src/features/generator/GeneratorProvider.tsx)). «Analizar» copia la secuencia del candidato al editor y **borra la procedencia** (`setSource(null)`, [`GeneratorPage.tsx:29-32`](../../frontend/src/features/generator/GeneratorPage.tsx)); si después se guarda el proyecto, solo queda la secuencia activa, sin vínculo con la ejecución, la semilla ni el brazo. Esto matiza la frase de la [propuesta RF-18](../../frontend/docs/prediction-storage-proposal.md) según la cual el generador «guarda su candidato seleccionado»: se guarda la secuencia, no su origen.
- Lo que la exportación **no** contiene: versión de la aplicación o del build (el cliente no expone ninguna; `package.json` declara `0.1.0` sin usarla en la app), fecha de la ejecución, candidato elegido ni versión de un sobre de historial. [`candidates.md:13`](../../frontend/docs/candidates.md) y [`definicion-proyecto.md:58`](../../frontend/docs/definicion-proyecto.md) registran el historial completo como pendiente; [`decisiones.md:16-17`](../../frontend/docs/decisiones.md) mantiene RF-17 implementado y RF-18 con procedencia y contratos por concretar.

### Proyectos (Workspace v2)

- Tablas `projects` y `analyses` con RLS por propietario, heredada en `analyses` a través del proyecto ([`20260928000000_initial_schema.sql:33-52`](../supabase/migrations/20260928000000_initial_schema.sql), [`:127-177`](../supabase/migrations/20260928000000_initial_schema.sql)). Un único snapshot por proyecto, `analyses.type = 'workspace-v1'` ([`20261012000000_project_transactions.sql:14`](../supabase/migrations/20261012000000_project_transactions.sql)); ese tipo es clave de almacenamiento, no versión del contenido.
- Pydantic `Workspace` con `schema_version: Literal[1, 2]` y `laboratory` presente si y solo si la versión es 2 ([`app/projects.py:123-135`](../app/projects.py)); todos los contratos usan `extra="forbid"`. El escrito completo del proyecto se rechaza si su JSON supera **2.000.000 bytes** ([`app/projects.py:153-154`](../app/projects.py)).
- Laboratorio: sobre `{schemaVersion: 1, documentJson, documentSha256}`; documento de hasta **1.000.000 bytes UTF-8** con claves exactas, hash y borradores validados sin descomprimir ni ejecutar ciencia ([`app/laboratory.py:11-55`](../app/laboratory.py), [`:58-69`](../app/laboratory.py)).
- Protobuf: `Workspace` campos 1–6, `laboratory = 6` ([`workspace.proto:13-34`](../proto/proteinforge/project/v1/workspace.proto)); vista previa con `has_laboratory = 7` y `laboratory_variants = 8` ([`project_service.proto:53-66`](../proto/proteinforge/project/v1/project_service.proto)). Lectura RPC máxima **2.000.000 bytes** ([`app/rpc/router.py:20`](../app/rpc/router.py)).
- SQL ([`20261021000000_laboratory_projects.sql`](../supabase/migrations/20261021000000_laboratory_projects.sql)): `pf_save_project` solo acepta `schemaVersion` `'1'` o `'2'` y un snapshot cuyo `jsonb::text` no supere 1.900.000 bytes (`:67-69`); `pf_validate_laboratory` exige laboratorio en toda versión distinta de 1 (`:15-24`); la **guardia contra sobrescritura** rechaza con `PT409` escribir algo distinto de `'2'` sobre un snapshot v2, bajo bloqueo y antes de modificar nombre o timestamp (`:78-87`); la vista previa compara `schemaVersion = '2'` literalmente (`:140-141`).
- La API traduce **cualquier** `PT409`, sea concurrencia o cliente antiguo, al mismo `409 project-conflict`/`ABORTED` ([`app/projects.py:274-275`](../app/projects.py)).
- Cliente: `workspaceSchema` acepta versión 1|2 y es un objeto Zod **no estricto**, que descarta claves desconocidas al leer ([`contracts.ts:18-24`](../../frontend/src/features/projects/contracts.ts)). `captureWorkspace` conserva el laboratorio leído al guardar desde el análisis ([`snapshot.ts:22-23`](../../frontend/src/features/projects/snapshot.ts)). El mapeo RPC copia campo a campo y no conserva campos protobuf desconocidos ([`rpcMapping.ts:24-30`](../../frontend/src/features/projects/rpcMapping.ts), [`:52-59`](../../frontend/src/features/projects/rpcMapping.ts)).
- `DesignService` v1alpha1 ya modela semilla, herramientas, versiones de pesos y tiempos de una ejecución externa ([`design_service.proto:44-56`](../proto/proteinforge/design/v1alpha1/design_service.proto), [`:99-112`](../proto/proteinforge/design/v1alpha1/design_service.proto)), pero es un borrador sin implementar y no se mezcla con este historial local.

### Tamaños medidos de exportaciones reales

Evidencia local ignorada en `backend/artifacts/generator-history-proposal/`: el generador real (`search.ts`, SHA-256 `0d452287…faddfc9`, el mismo al redactar) se ejecutó en Node sin red ni modelos, con la serialización exacta de `download()`. Los tamaños compactos y anidados se derivaron de esos mismos archivos sin repetir la búsqueda (`derived-sizes.json`).

| Caso | Exportación (bytes) | Compacta | Exportación como cadena JSON anidada | Candidatos |
|---|---:|---:|---:|---:|
| Predeterminado: 40 residuos, 1.000 evaluaciones, semilla 42 | 4.784 | 3.224 | 5.237 | 10 |
| Máximo: 300 residuos, 5.000 evaluaciones, semillas 1/7/42/100/4294967295 | 7.506–7.540 | 5.946–5.980 | 7.959–7.993 | 10 |

Un documento de historial de ensayo con las cinco ejecuciones máximas ocupó 40.655 bytes; su sobre, 44.513, y un escrito REST completo sin laboratorio, 44.763. El tamaño depende sobre todo de longitud × candidatos (10 × 300 residuos como máximo), no del presupuesto. Solo se midieron estos casos; no es una cota demostrada para toda entrada.

## 2. Contrato acordado

Las secciones 2.1–2.7 conservan la propuesta original con las opciones elegidas marcadas; la sección 5 resume lo que ya está implementado y sus diferencias con este texto.

### 2.1 Dónde se guarda: opciones

| | **A · Sobre dentro del snapshot (recomendada)** | B · Filas `analyses` de otro tipo | C · Tabla nueva `generator_runs` |
|---|---|---|---|
| Esquema | Campo opcional `generatorHistory` en `workspace`, igual que `laboratory` | Una fila por ejecución, `type = 'generator-run-v1'`, en la tabla existente | Tabla con RLS, grants e índices propios |
| Concurrencia | La del proyecto: `expectedUpdatedAt`, una transacción | Necesita RPC nuevas y su propia política de versión | Igual que B |
| Migración | Validación y proyección SQL; sin tablas | Funciones nuevas; RLS heredada ya existe | Completa, con pruebas RLS nuevas |
| Contrato | Un mensaje y un campo protobuf nuevos | Métodos RPC y rutas REST nuevos | Igual que B |
| Límite | Tope propio dentro del máximo del proyecto | Por fila; sin tope agregado natural | A definir por fila y propietario |
| Consultas | Abrir el proyecto; la lista solo da recuento | Listar ejecuciones sin abrir el snapshot | Listar y filtrar fuera de un proyecto |
| Coste de trabajo | Bajo; reutiliza el patrón aprobado del laboratorio | Medio | Alto |

**Elegida A** (decisión 2). Se recomendaba **A**: es coherente con la recomendación ya hecha para RF-18 y con el laboratorio, no añade tablas ni costes y hereda propietario, RLS, concurrencia y borrado en cascada. B o C solo se justifican si el propietario quiere consultar ejecuciones entre proyectos o guardar muchas más de las que caben en el tope; nadie lo ha pedido. Elegir B o C cambia la arquitectura y requiere otra propuesta.

### 2.2 Formato dentro de A: opciones

- **A1 (elegida, decisión 3): cada ejecución guarda el texto exacto exportado.** `resultJson` es la cadena que hoy produce `download()`, y su SHA-256 es el identificador. La API valida la forma sin recalcular ciencia, como hace con el laboratorio; el cliente revalida y puede volver a descargar **exactamente** el mismo archivo, con el mismo hash. Un cambio futuro del generador no obliga a cambiar el protobuf.
- A2: mensajes protobuf estructurados (`GeneratorRun`, `SearchArm`, `Candidate`). La API podría validar cada campo y la lista podría proyectar métricas, pero duplica el esquema del generador en Zod, Pydantic, protobuf y SQL, y obliga a cambiar el contrato en cada `methodVersion`. Se puede pasar de A1 a A2 más adelante con otra versión del sobre.

### 2.3 Campos

Sobre en `Workspace` (análogo a `LaboratorySnapshot`):

| Campo | Contenido |
|---|---|
| `generatorHistory.schemaVersion` | `1`: versión del sobre |
| `generatorHistory.documentJson` | Documento JSON UTF-8 compacto, **≤ 100.000 bytes** (propuesto) |
| `generatorHistory.documentSha256` | SHA-256 hexadecimal en minúsculas de los bytes exactos de `documentJson` |

Documento `proteinforge-generator-history` v1, claves exactas y sin campos extra:

| Campo | Contenido y validación |
|---|---|
| `kind` | `"proteinforge-generator-history"` |
| `schemaVersion` | `1`: versión del documento de historial |
| `runs` | Lista de 1–**10** ejecuciones (propuesto), en orden de grabación; sin identificadores repetidos |
| `runs[].id` | SHA-256 de los bytes UTF-8 de `resultJson`; identificador y prueba de integridad. Dos ejecuciones idénticas (mismos parámetros y versión) tienen el mismo id y no se duplican |
| `runs[].origin` | `"local-generator"`: única procedencia admitida en v1; excluye ejecutores externos, que pertenecen a `DesignService` |
| `runs[].recordedAt` | ISO 8601 con zona, **hora del cliente**, etiquetada como tal. La hora del servidor sigue siendo `updatedAt` del proyecto |
| `runs[].appBuild` | Commit del build que ejecutó la búsqueda, inyectado por Vite (decisión 8): 7–40 caracteres hexadecimales en minúsculas, o `null` si se desconoce. El backend ya lo valida; el cliente aún no lo produce |
| `runs[].resultJson` | Texto exacto exportado (≤ 10.000 bytes por ejecución, propuesto): `schemaVersion` del resultado (1), `methodVersion` (`proteinforge-candidates-v1`, versión del algoritmo), `parameters` con `seed`, ambos brazos con sus métricas y candidatos **en el orden emitido**, con `descriptors`, `loss` y `satisfies`, y `references` |
| `runs[].selection` | `null` o `{arm: "guided"\|"reference", index}`: candidato que la persona eligió; `index` debe existir en ese brazo |

Versiones registradas: sobre (`generatorHistory.schemaVersion`), documento (`schemaVersion`), resultado exportado (`resultJson.schemaVersion`), algoritmo y generador (`resultJson.methodVersion`), aplicación (`appBuild`) y snapshot (`workspace.schemaVersion = 3`). La semilla de referencia no se guarda aparte: se deriva de la semilla y de `methodVersion`; duplicarla crearía un dato más que validar.

Validación propuesta:

- **API (Pydantic completa; SQL como defensa en profundidad, véase la sección 5):** sobre, versiones, tamaño en bytes, hash del documento, claves exactas, número de ejecuciones, unicidad, `id = SHA-256(resultJson)`, fecha con zona, `origin`, que `resultJson` sea JSON con `schemaVersion = 1` y `methodVersion` conocido, alfabeto de 20 residuos y longitudes 5–300 de los candidatos, como mucho `retained` por brazo y `selection` dentro de rango. Sin recalcular descriptores, penalizaciones ni la búsqueda.
- **Cliente (Worker):** revalidación completa con `searchParametersSchema` y repetición determinista de la búsqueda **solo bajo demanda**, con una acción explícita por ejecución y aviso si no coincide; nunca automática al abrir, por coste (decisión 11). [`candidates.md:9`](../../frontend/docs/candidates.md) advierte de posibles variaciones de coma flotante entre plataformas: una discrepancia se mostraría como tal, sin corregir los datos guardados.
- Nada de esto certifica autoría, función ni estabilidad: el hash prueba integridad de bytes, no quién hizo el experimento.

### 2.4 Límites de tamaño

| Límite | Valor | Estado |
|---|---|---|
| Ejecución (`resultJson`) | ≤ 10.000 bytes | Implementado en backend; máximo medido 7.540 |
| Documento de historial | ≤ 100.000 bytes y ≤ 10 ejecuciones | Implementado en backend; 10 ejecuciones máximas anidadas ≈ 81 KB estimados |
| Documento del laboratorio | ≤ 1.000.000 bytes | Vigente, sin cambios |
| Snapshot en SQL (`jsonb::text`) | ≤ 1.900.000 bytes | Vigente, sin cambios |
| Escrito del proyecto (JSON REST) | ≤ 2.000.000 bytes | Vigente, sin cambios |
| Mensaje RPC | ≤ 2.000.000 bytes | Vigente, sin cambios |

Cómo encaja: el historial es un tope propio y pequeño, no comparte los 1.000.000 bytes del laboratorio. Un proyecto con laboratorio máximo e historial máximo suma unos 1,1 MB de documentos, más secuencias del análisis y la comparación, y todavía cabe en los máximos vigentes en los casos medidos. No es una garantía: `documentJson` se escapa al anidarse y `jsonb::text` añade espacios, así que un laboratorio cercano a su máximo puede, ya hoy, superar el total. Los límites totales vigentes siguen siendo la autoridad y **no se amplían**.

Política si se excede:

- **Rechazo explícito y atómico**: el servidor no escribe nada (ni nombre, ni timestamp, ni snapshot) y responde con el `422 validation` actual, que nombra el campo sin valores (decisión 6).
- **Nunca truncar ni descartar ejecuciones en silencio**, ni en cliente ni en servidor. El cliente comprueba los topes antes de enviar y pide a la persona que quite ejecuciones concretas; la exportación local sigue disponible para conservarlas fuera.
- El borrador en memoria y el último proyecto guardado se conservan tras el rechazo.

### 2.5 Compatibilidad y versiones

**Por qué hace falta `workspace.schemaVersion = 3` y no basta un campo opcional en v2.** Un cliente v2 que lea un proyecto con historial lo perdería sin avisar: Zod no estricto descarta la clave en REST ([`contracts.ts:18`](../../frontend/src/features/projects/contracts.ts)), y el mapeo RPC no conserva el campo protobuf desconocido ([`rpcMapping.ts:52-59`](../../frontend/src/features/projects/rpcMapping.ts)). Al guardar después, el snapshot v2 resultante pasaría la guardia actual y borraría el historial. En cambio, con versión 3:

| Situación | Comportamiento propuesto |
|---|---|
| Cliente v1 o v2 **abre** un proyecto v3 | Su contrato solo acepta 1\|2 y rechaza la apertura con error explícito; no puede editarlo ni guardarlo. Hay que comprobar que copiar y renombrar desde la lista tampoco reescriben el snapshot |
| Cliente v1 o v2 **escribe** sobre un proyecto v3 | La guardia **monótona** de SQL rechaza cualquier versión inferior a la guardada (`PT409`, sin escritura parcial). Generaliza la guardia actual, que solo protege v2 |
| Cliente v3 abre un proyecto v1/v2 sin historial | Lectura normal; ausencia de `generatorHistory` significa «sin historial», no un error |
| Cliente v3 guarda | Versión = máximo entre la guardada y la mínima que exige el contenido (3 con historial, 2 con laboratorio, 1 si no). Conserva laboratorio e historial leídos al guardar desde cualquier herramienta, como ya hace con el laboratorio |
| Servidor antiguo recibe v3 | Pydantic `Literal[1, 2]` y SQL `in ('1','2')` responden con error de validación. Por RPC, el servidor antiguo ignoraría el campo nuevo, pero también rechaza `schema_version = 3`, así que no se pierde nada en silencio |
| Proyectos ya guardados | No se migran filas: v1 y v2 siguen siendo válidos e idénticos |

Despliegue en el orden ya establecido: migración idempotente → backend → actualizar pin y regenerar el cliente → frontend. Mientras el backend no esté publicado, el frontend nuevo no debe ofrecer el guardado del historial.

Cambios SQL (implementados en la fase backend): aceptar `'3'`; en v3, laboratorio **opcional** (v2 lo sigue exigiendo); nueva `pf_validate_generator_history`; guardia `(guardada)::int > (nueva)::int → PT409`; vista previa con `schemaVersion >= 2` para `hasLaboratory` y recuento `generatorRuns`, sin documento. Versionado protobuf: `Workspace.generator_history = 7` y `ProjectPreview.generator_runs = 9`, sin renumerar campos ni cambiar tipos; `buf breaking` contra `main` debe pasar.

### 2.6 Laboratorio y alineamientos guardados

Ningún campo existente cambia de significado. `profileWindow`, `source`, `descriptors`, `alignment` y `laboratory` conservan contrato, validación y mensajes protobuf 1–6; `LaboratorySnapshot` y el documento `proteinforge-laboratory` v1 no se tocan; v1 y v2 mantienen sus reglas, incluida la de v2 de exigir laboratorio; `analyses.type = 'workspace-v1'` sigue siendo la clave de almacenamiento; vistas previas 1–8 y la lista sin snapshots completos no cambian. La tabla `alignments` del esquema inicial, con RLS y permisos pero sin uso en la API ni en el cliente, queda fuera de esta propuesta. Guardar el historial desde el generador debe conservar íntegro el laboratorio, el análisis y la comparación ya guardados, y viceversa.

### 2.7 Fuera de alcance

Ejecutores externos, predicción RF-18, Storage, sincronización, colaboración, gasto, publicación y migraciones remotas. La acción visible para guardar una ejecución (dónde está, a qué proyecto se vincula, cómo se quitan ejecuciones o se muestra un rechazo por tamaño) necesita **maqueta aprobada** y textos es/en en el mismo cambio antes de programarse.

## 3. Plan de pruebas necesario

La sección 5 indica qué se ejecutó en la fase backend; las pruebas que dependen del cliente quedan para la fase frontend.

- **Unitarias de contrato (Pydantic):** sobre y documento válidos; claves extra o duplicadas; versión desconocida; hash del documento o de `resultJson` incorrecto; id repetido; 0 y 11 ejecuciones; `resultJson` de 10.000 y 10.001 bytes; documento de 100.000 y 100.001 bytes; fecha sin zona; `origin` desconocido; candidato con alfabeto o longitud fuera de rango; más de cinco candidatos por brazo; `selection` fuera de rango; NaN o Infinity rechazados.
- **REST:** crear, abrir, actualizar y borrar con historial; 422 con nombre de campo y sin valores; 409 con versión antigua y con cliente antiguo; 413 o 422 por tamaño según la decisión 6; `Cache-Control: no-store` y `X-Request-Id`.
- **RPC:** las mismas operaciones por Connect JSON, Connect binario y gRPC-Web, equivalentes a REST; mensaje justo bajo 2.000.000 bytes aceptado y por encima rechazado sin ampliar `read_max_bytes`; `INVALID_ARGUMENT`/`ABORTED` con códigos estables; `buf lint`, `buf breaking` contra `main` y regeneración reproducible.
- **SQL y RLS en PostgreSQL local** (`scripts/test-postgres.ps1`, cluster temporal en 127.0.0.1): un segundo propietario y `anon` no leen ni escriben historial ajeno; cascada al borrar; guardia monótona (v1→v3, v2→v3 rechazados sin cambiar nombre ni timestamp); dos escritores simultáneos con un único ganador; rollback ante historial inválido; validación SQL equivalente a Pydantic; vista previa con recuento y sin documento; `hasLaboratory` correcto en v3; migración repetida dos veces sin cambiar filas.
- **Compatibilidad cliente antiguo/nuevo:** fixtures v1, v2 y v3; cliente v2 rechaza abrir v3 y no puede copiar ni renombrar reescribiendo el snapshot; cliente v3 abre v1/v2 sin pérdida; guardar desde análisis, laboratorio y generador conserva los otros dos; servidor antiguo rechaza v3 por REST y RPC.
- **Límites y errores:** proyecto con laboratorio cercano a 1.000.000 bytes más historial máximo; rechazo sin truncamiento y borrador conservado; `resultJson` reexportado byte a byte idéntico (mismo SHA-256 que la descarga); repetición determinista en Worker y aviso ante discrepancia.
- **Frontend (cuando exista interfaz aprobada):** pruebas del módulo afectado y E2E solo del recorrido nuevo, 360/1280 px, ambos temas, teclado, AA y es/en.

## 4. Decisiones tomadas

Tomadas el **27 oct 2026** por el coordinador de las sesiones, por delegación del propietario («si son de programacion, las tomas tu, de la mejor manera posible sin afectar nada», chat de coordinación del 26 oct 2026). Registradas tal como las comunicó el coordinador; no se consultó de nuevo al propietario.

1. Guardar el historial en Proyectos: **sí**.
2. Dónde: **A, dentro del snapshot**.
3. Formato: **A1, texto exportado exacto con SHA-256**.
4. Topes: **sí, 10 ejecuciones, 100.000 bytes por documento y 10.000 por ejecución**.
5. **Sí, `workspace.schemaVersion = 3` con guardia monótona**.
6. Rechazo por tamaño: **se mantiene el `422 validation` actual con nombre de campo**.
7. **Sí, código estable propio** `project-requires-current-client`, con el mismo `409`/`ABORTED`, para que los clientes actuales lo sigan viendo como conflicto.
8. **Sí, commit del build inyectado por Vite** (fase frontend).
9. **Sí, se guarda `selection`**.
10. **No en esta fase**: «Analizar» no enlaza la secuencia con su ejecución.
11. **Solo bajo demanda**: repetición en Worker con acción explícita por ejecución y aviso si no coincide; nunca automática al abrir (fase frontend).

Estas decisiones no autorizan publicar, hacer push o merge, aplicar migraciones remotas ni programar interfaz sin maqueta aprobada por el propietario.

## 5. Fase backend implementada — 27 oct 2026

Implementada localmente en backend `dev`, sin publicar:

- **Pydantic** ([`app/generator_history.py`](../app/generator_history.py), [`app/projects.py`](../app/projects.py)): sobre y documento v1 con claves exactas, JSON estricto (sin claves duplicadas ni NaN/Infinity), tamaños en bytes UTF-8, hash del documento, `id = SHA-256(resultJson)`, unicidad, zona horaria, `origin`, `appBuild`, `selection` dentro de rango, y forma del resultado exportado: versiones, parámetros con sus intervalos, brazos, 1–5 candidatos únicos por brazo de 20 residuos y longitud igual a la pedida, descriptores numéricos finitos. Workspace acepta 1, 2 y 3 con las reglas de la sección 2.5.
- **SQL** ([`20261027000000_generator_history.sql`](../supabase/migrations/20261027000000_generator_history.sql)): acepta '3' como número JSON; laboratorio opcional en v3; `pf_validate_generator_history` comprueba sobre, documento, ejecuciones, hashes, zona, `appBuild`, versiones, parámetros enteros, candidatos (alfabeto, longitud y descriptor `length`) y `selection`. No revisa intervalos GRAVY/masa, métricas de los brazos ni el resto de descriptores: esa parte solo la valida Pydantic, que siempre se ejecuta antes. Guardia monótona, `generatorRuns` en la vista previa y funciones nuevas privadas para `anon`.
- **Protobuf**: `GeneratorHistorySnapshot`, `Workspace.generator_history = 7` y `ProjectPreview.generator_runs = 9`. `buf lint` y `buf breaking --against .git#branch=main` correctos; código generado reproducible.
- **REST y RPC**: misma validación y mapeo; `409 project-requires-current-client`/`ABORTED` para la guardia, `project-conflict` para concurrencia.
- **Pruebas**: 11 nuevas sin base ([`tests/test_generator_history.py`](../tests/test_generator_history.py): exportaciones reales aceptadas byte a byte, 23 mutaciones inválidas, límites exactos de 100.000/10.000 bytes y 10 ejecuciones, versiones del Workspace, proyecto con laboratorio e historial máximos bajo 2.000.000 bytes, ida y vuelta REST/Connect JSON/gRPC-Web, rechazos sin tocar almacenamiento ni reflejar valores, código propio frente a conflicto en los tres protocolos, recuento en la lista y mensaje RPC sobre 2.000.000 bytes rechazado). Batería completa: 130 pruebas, 17 SQL omitidas. SQL/RLS en PostgreSQL 18 local: 17/17, cinco nuevas. Fixtures reales con procedencia en [`tests/fixtures/generator/`](../tests/fixtures/generator/README.md).

Diferencias con el texto original: ninguna en campos ni límites. Precisiones: v3 admite snapshot sin laboratorio ni historial (un proyecto v3 no vuelve a v2 al vaciar el historial); `appBuild` admite 7–40 hexadecimales; la validación SQL es un subconjunto documentado de la de Pydantic.

Sin comprobar: Python 3.14 local (CI lo usa), PostgREST/Supabase remoto y la migración en Supabase, que **no está aplicada**. Orden de activación cuando haya autorización: migración → backend → pin y regeneración → frontend.

## 6. Fase frontend pendiente

Requiere su propia ejecución en `frontend/dev` cuando termine el rediseño en curso; la interfaz necesita además maqueta aprobada por el propietario.

1. Tras publicar el backend con autorización: actualizar el pin de `buf.gen.yaml` y regenerar `src/gen` sin editarlo a mano.
2. Contratos del cliente: `workspaceSchema` con versión 1|2|3 y `generatorHistory`; validación Zod equivalente del sobre, documento y ejecución; preview con `generatorRuns`.
3. `rpcMapping`: ida y vuelta de `generatorHistory` y `generator_runs`.
4. `captureWorkspace` y equivalentes: versión = máximo entre la guardada y la mínima que exige el contenido; conservar laboratorio e historial leídos al guardar desde análisis, laboratorio o generador.
5. Errores: añadir `project-requires-current-client` a los códigos de detalle RPC con su texto es/en.
6. `appBuild`: inyectar el commit del build con Vite (`define`) y `null` si no existe.
7. Guardar una ejecución: el texto exacto que hoy produce `download()`, su SHA-256, `recordedAt` y `selection`; comprobar topes antes de enviar y, si se exceden, pedir quitar ejecuciones concretas sin truncar.
8. Reapertura y reproducción bajo demanda en Worker por ejecución, con aviso si no coincide.
9. Interfaz con maqueta aprobada: dónde se guarda, a qué proyecto se vincula, cómo se quitan ejecuciones y cómo se muestra el rechazo; textos es/en en el mismo cambio.
10. Pruebas: unitarias de contratos y mapeo, compatibilidad v1/v2/v3 en cliente (v2 rechaza abrir v3, copiar y renombrar no reescriben), reexportación byte a byte, E2E del recorrido nuevo a 360/1280 px, ambos temas, teclado y AA.
