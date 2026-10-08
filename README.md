# ProteinForge — backend

Repositorio [Jacklb19/Protein_Back](https://github.com/Jacklb19/Protein_Back), independiente del [frontend](https://github.com/Jacklb19/ProteinForge). Contiene la base de la API FastAPI, la migración Supabase y sus pruebas. Proyectos, proveedores externos y despliegue siguen pendientes.

## Organización

```text
ProteinForge/       # carpeta local, sin Git
├── frontend/       # React/Vite y cálculos locales
└── backend/        # este repositorio
```

Comandos desde `backend/`. `dev` es desarrollo y `main` la base verificada. No es necesario tener el frontend al lado. La definición general vive en el repositorio frontend; este repositorio mantiene sus responsabilidades y contratos de API.

## Responsabilidad

FastAPI gestiona identidad, autorización y, más adelante, proyectos, consultas UniProt y PDBe, interpretación y predicción estructural externa. Supabase proporciona Auth, PostgreSQL y Storage. La búsqueda de candidatos, descriptores y alineamiento permanecen en el navegador. Cada repositorio tiene su propio proyecto y URL de Vercel.

## Desarrollo local

Dependencias fijadas en `requirements.txt` (FastAPI 0.142.4, uvicorn 0.54.0, httpx 0.28.1). Python 3.14 es el objetivo; la base se ha ejecutado con 3.13 local.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m uvicorn app.main:app --reload
```

`GET /v1/health` responde sin configuración. `GET /v1/me` requiere `SUPABASE_URL` y `SUPABASE_ANON_KEY` en `.env` (o en el entorno) y un token Bearer de Supabase Auth; sin configuración devuelve 503. CORS se limita a `ALLOWED_ORIGINS`.

## Verificación

```bash
python -m unittest discover -s tests -v
```

Cubre la auditoría estática de RLS y la API con un transporte simulado de Supabase: salud, CORS, token ausente o inválido, identidad resuelta, identidad sin configurar y fallos del proveedor convertidos en 502/504 sin filtrar detalles. No ejecuta migraciones ni prueba políticas contra PostgreSQL; la integración de base de datos local sigue pendiente. CI instala las dependencias fijadas y ejecuta la misma batería.

## Configuración

`.env.example` contiene nombres sin valores. Nunca versionar `.env` ni la clave `service_role`. No configurar ni desplegar recursos remotos sin autorización.

Antes del primer despliegue: verificar runtime Python/Vercel, configurar los orígenes del frontend, probar autorización y RLS con PostgreSQL local. El consentimiento de predicción debe advertir que se enviará la secuencia a un proveedor externo.

Consultar [arquitectura](docs/arquitectura.md) y [contratos](docs/api.md). El servicio de predicción público es compartido; una prueba exitosa no garantiza su disponibilidad para la presentación.
