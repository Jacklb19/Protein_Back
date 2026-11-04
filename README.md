# ProteinForge — backend

**Publicación autorizada, 1 nov 2026:** el propietario confirma que aplicó `20261030000000_structure_predictions.sql` y configuró las banderas de predicción en ambos proyectos Vercel. Son declaraciones suyas, pendientes de comprobación remota posterior. Autoriza push de dev y merge/push a main de ambos repositorios para esta entrega, con autoría exclusiva de Jacklb19. La publicación sigue el orden backend→frontend y comprueba CI/despliegues; no se declara terminada antes de observarlos. Las referencias a «solo local» y producción sin predicción inferiores describen el estado anterior.

Repositorio [Jacklb19/Protein_Back](https://github.com/Jacklb19/Protein_Back), independiente del [frontend](https://github.com/Jacklb19/ProteinForge). Contiene FastAPI, proyectos personales y migraciones Supabase. Publicados: identidad, CRUD de proyectos, guardado del laboratorio, UniProt acotado, asistente de interpretación, el [servicio SIFTS autenticado](docs/sifts.md) —comprobado en producción con P01308/1TRZ— y el [historial del generador](docs/generator-history-contract-proposal.md) sobre workspace v3. Publicado: `main` `18d9444`, con CI y Vercel correctos. La continuación local de `dev` se describe abajo; todavía no está publicada. El propietario declara haber aplicado la migración `20261027000000` el 28 de octubre, sin inspección por nuestra parte. Siguen pendientes el recorrido con JWT real, el aislamiento por ID con una segunda cuenta y la comprobación de la prosa del asistente. La producción sigue sin predicción estructural; su implementación local posterior está descrita abajo.

## Continuación local

**Continuación local, 30 oct 2026:** [predicción estructural](docs/folding.md) implementada por REST/RPC, con consentimiento, validación y opt-in desactivado; guardado sobre laboratorio v2/workspace v4 y migración solo local. 146 pruebas descubiertas: 127 ejecutadas correctas y 19 SQL omitidas sin base; las 19 SQL pasaron aparte en PostgreSQL 18 temporal. `cbff97e` era la referencia local al iniciar esta entrega; la revisión preparada se consulta con `git log -1`. Nada de esta continuación está publicado; la descripción inicial corresponde a producción y sus versiones históricas.

## Organización del workspace

```text
ProteinForge/       # carpeta local, sin Git
├── frontend/       # React/Vite y cálculos locales
└── backend/        # este repositorio
```

Comandos desde `backend/`. `dev` es desarrollo y `main` la base verificada. No es necesario tener el frontend al lado. La definición general vive en el repositorio frontend; este repositorio mantiene sus responsabilidades y contratos de API.

## Responsabilidad

FastAPI gestiona identidad, autorización, CRUD de proyectos, UniProt acotado e interpretación, por REST y por gRPC (gRPC-Web para el navegador, Connect y gRPC nativo; ver [gRPC](docs/api.md#grpc)). JWT de propietario, RLS y RPC SECURITY INVOKER. SIFTS añade correspondencias explícitas, versión/hash completos, caché pública acotada y cuota compartida de referencias; predicción y Storage siguen sin integrar. Candidatos, descriptores, alineamiento e informes en navegador. Cada repositorio conserva su despliegue independiente. [Contratos](docs/api.md), [persistencia](docs/project-persistence.md), [UniProt](docs/uniprot.md).

## Desarrollo local

Dependencias fijadas en `requirements.txt` (FastAPI 0.142.4, uvicorn 0.54.0, httpx 0.28.1, python-dotenv 1.2.4, connectrpc 0.12.1, protobuf-py 0.6.0, pyqwest 0.12.0). Generación de código y Hypercorn para gRPC nativo en `requirements-dev.txt`. Python 3.14 es el objetivo; la base se ha ejecutado con 3.13 local.

Para Vercel, `.python-version` fija 3.14 y la detección FastAPI usa `app/main.py:app`. Configurar `SUPABASE_URL`, `SUPABASE_ANON_KEY` y `ALLOWED_ORIGINS` en las variables del proyecto. Build/instalación/salida del framework sin overrides; no iniciar Uvicorn como comando de build. Ver [FastAPI](https://vercel.com/docs/frameworks/backend/fastapi) y [Python](https://vercel.com/docs/functions/runtimes/python/python-version). Este ajuste no verifica un despliegue remoto.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m uvicorn app.main:app --reload
```

`GET /v1/health` responde sin configuración. `GET /v1/me` requiere `SUPABASE_URL`, `SUPABASE_ANON_KEY` y un token Bearer de Supabase Auth; sin configuración devuelve 503. La aplicación carga automáticamente el `.env` de la raíz de este repositorio. Las variables del proceso tienen prioridad; no se modifica el entorno ni se buscan archivos en carpetas superiores. La configuración explícita de pruebas no lee archivos locales. CORS se limita a `ALLOWED_ORIGINS`. `UNIPROT_REQUESTS_PER_MINUTE` acota por identidad las consultas que el proceso reenvía a UniProt (20 por omisión; 0 las deja sin presupuesto). `/docs`, `/redoc` y `/openapi.json` solo se sirven con `API_DOCS_ENABLED=enabled` (para desarrollo local; vacío en producción). `GROQ_API_KEY` activa el asistente de interpretación; `ASSISTANT_MODEL` y `ASSISTANT_REQUESTS_PER_MINUTE` (6 por omisión) lo ajustan. Ver [asistente](docs/assistant.md).

El frontend ya puede comprobar `/v1/health` y `/v1/me` desde Ajustes mediante `VITE_API_BASE_URL`. Configurar en `ALLOWED_ORIGINS` el origen exacto usado en el navegador, incluido el puerto (`localhost` y `127.0.0.1` son orígenes diferentes). No crear proyectos ni aplicar migraciones como parte de esa comprobación.

## Verificación

```bash
python -m unittest discover -s tests -v
```

Cubre auditoría estática, API REST y RPC, proyectos y adaptadores con transporte simulado. `./scripts/test-postgres.ps1` ejecuta migraciones, RLS y concurrencia en PostgreSQL temporal real. Ver [contrato, evidencia y activación](docs/project-persistence.md). Sin base configurada, las pruebas SQL se identifican como omitidas en unittest; no equivalen a verificación de base nueva. CI ejecuta esa batería.

## Configuración

`.env.example` contiene nombres sin valores. Nunca versionar `.env` ni la clave `service_role`. No configurar ni desplegar recursos remotos sin autorización.

Antes del primer despliegue: verificar runtime Python/Vercel, configurar los orígenes del frontend, probar autorización y RLS con PostgreSQL local. El consentimiento de predicción debe advertir que se enviará la secuencia a un proveedor externo.

Consultar [arquitectura](docs/arquitectura.md) y [contratos](docs/api.md). Una prueba exitosa de un servicio de predicción público no garantiza su disponibilidad.

Próximas etapas: verificar el enlace SIFTS y el guardado del historial con sesión real y dos cuentas, por ejemplo con Supabase local, y comprobar la prosa del asistente con lecturas reales. El modelado 3D sigue siendo manual en Colab y sus resultados se importan en el frontend; la inferencia desde la app está aplazada hasta que exista un ejecutor gratuito cuyos términos la permitan, y no hay ejecutor, pesos ni endpoints de diseño implementados. Cada publicación necesita autorización nueva y conserva el orden migración → backend → repin/regeneración → frontend. Sin migraciones nuevas ni servicios de pago.
