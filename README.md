# ProteinForge — backend

Repositorio [Jacklb19/Protein_Back](https://github.com/Jacklb19/Protein_Back), independiente del [frontend](https://github.com/Jacklb19/ProteinForge). Contiene FastAPI, proyectos personales y migraciones Supabase. Proyectos, guardado del laboratorio y asistente de interpretación publicados en la entrega del 23 de octubre; la lectura autenticada del asistente en producción todavía necesita comprobación. El [adaptador SIFTS interno](docs/sifts.md) se añade localmente el 25 de octubre, sin ruta pública ni cambio de contratos. Inferencia estructural no integrada.

## Organización

```text
ProteinForge/       # carpeta local, sin Git
├── frontend/       # React/Vite y cálculos locales
└── backend/        # este repositorio
```

Comandos desde `backend/`. `dev` es desarrollo y `main` la base verificada. No es necesario tener el frontend al lado. La definición general vive en el repositorio frontend; este repositorio mantiene sus responsabilidades y contratos de API.

## Responsabilidad

FastAPI gestiona identidad, autorización, CRUD de proyectos, UniProt acotado e interpretación, por REST y por gRPC (gRPC-Web para el navegador, Connect y gRPC nativo; ver [gRPC](docs/api.md#grpc)). JWT de propietario, RLS y RPC SECURITY INVOKER. SIFTS tiene adquisición/normalización interna, todavía sin suministro al navegador; predicción y Storage siguen sin integrar. Candidatos, descriptores, alineamiento e informes en navegador. Cada repositorio conserva su despliegue independiente. [Contratos](docs/api.md), [persistencia](docs/project-persistence.md), [UniProt](docs/uniprot.md).

## Desarrollo local

Dependencias fijadas en `requirements.txt` (FastAPI 0.142.4, uvicorn 0.54.0, httpx 0.28.1, python-dotenv 1.2.4, connectrpc 0.12.1, protobuf-py 0.6.0, pyqwest 0.12.0). Generación de código y Hypercorn para gRPC nativo en `requirements-dev.txt`. Python 3.14 es el objetivo; la base se ha ejecutado con 3.13 local.

Para Vercel, `.python-version` fija 3.14 y la detección FastAPI usa `app/main.py:app`. Configurar `SUPABASE_URL`, `SUPABASE_ANON_KEY` y `ALLOWED_ORIGINS` en las variables del proyecto. Build/instalación/salida del framework sin overrides; no iniciar Uvicorn como comando de build. Ver [FastAPI](https://vercel.com/docs/frameworks/backend/fastapi) y [Python](https://vercel.com/docs/functions/runtimes/python/python-version). Este ajuste no verifica un despliegue remoto.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m uvicorn app.main:app --reload
```

`GET /v1/health` responde sin configuración. `GET /v1/me` requiere `SUPABASE_URL`, `SUPABASE_ANON_KEY` y un token Bearer de Supabase Auth; sin configuración devuelve 503. La aplicación carga automáticamente el `.env` de la raíz de este repositorio. Las variables del proceso tienen prioridad; no se modifica el entorno ni se buscan archivos en carpetas superiores. La configuración explícita de pruebas no lee archivos locales. CORS se limita a `ALLOWED_ORIGINS`. `UNIPROT_REQUESTS_PER_MINUTE` acota por identidad las consultas que el proceso reenvía a UniProt (20 por omisión; 0 las deja sin presupuesto). `GROQ_API_KEY` activa el asistente de interpretación; `ASSISTANT_MODEL` y `ASSISTANT_REQUESTS_PER_MINUTE` (6 por omisión) lo ajustan. Ver [asistente](docs/assistant.md).

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

Próximas etapas: acordar suministro autenticado SIFTS y su enlace con el visor, y completar una nueva prueba gratuita de modelado para el [laboratorio didáctico](https://github.com/Jacklb19/ProteinForge/blob/dev/docs/design-lab-roadmap.md). Sin ejecutor, pesos ni endpoints de diseño implementados. Las autorizaciones de publicaciones anteriores no autorizan esta entrega, nuevas migraciones o servicios de pago.
