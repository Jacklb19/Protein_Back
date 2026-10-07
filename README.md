# ProteinForge — backend

Repositorio [Jacklb19/Protein_Back](https://github.com/Jacklb19/Protein_Back), independiente del [frontend](https://github.com/Jacklb19/ProteinForge). Esta base contiene documentación, migración Supabase y prueba estática de RLS. **La API FastAPI todavía no está implementada ni desplegada.**

## Organización

```text
ProteinForge/       # carpeta local, sin Git
├── frontend/       # React/Vite y cálculos locales
└── backend/        # este repositorio
```

Comandos desde `backend/`. `dev` es desarrollo y `main` la base verificada. No es necesario tener el frontend al lado para ejecutar la prueba de migraciones. La definición general vive en el repositorio frontend; este repositorio mantiene sus responsabilidades y contratos de API.

## Responsabilidad prevista

FastAPI/Python 3.14 gestionará identidad/autorización, proyectos, consultas UniProt y PDBe, interpretación y predicción estructural externa. Supabase proporciona Auth, PostgreSQL y Storage. La búsqueda de candidatos, descriptores y alineamiento permanecen en el navegador. Cada repositorio tendrá su propio proyecto y URL de Vercel.

Versiones exactas, dependencias y configuración de ejecución de la API se fijarán al implementar su base. No se añadieron dependencias ni un servidor de ejemplo sin alcance funcional.

## Verificación disponible

La prueba utiliza únicamente la biblioteca estándar de Python:

```bash
python -m unittest discover -s tests -v
```

Comprueba existencia de migraciones y activación explícita de RLS por cada tabla creada. Es una auditoría de archivos SQL, no una ejecución de migraciones ni una prueba de políticas contra PostgreSQL. La integración de base de datos local sigue pendiente.

La migración se trasladó del frontend conservando su contenido. No se aplicó a una base remota. CI ejecuta esta comprobación de manera independiente.

## Configuración

`.env.example` contiene nombres sin valores para la API futura. Nunca versionar `.env` ni la clave `service_role`. No configurar ni desplegar recursos remotos durante la organización del proyecto.

Antes del primer despliegue: implementar API y validación, fijar dependencias, verificar runtime Python/Vercel, configurar CORS para el frontend, validar JWT y probar autorización/RLS. El consentimiento de predicción debe advertir que se enviará la secuencia a un proveedor externo.

Consultar [arquitectura](docs/arquitectura.md) y [contratos previstos](docs/api.md). El servicio de predicción público es compartido; una prueba exitosa no garantiza su disponibilidad para la presentación.
