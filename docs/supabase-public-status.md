# Estado público de Supabase comprobado

Lectura autorizada expresamente por el usuario el 12 oct 2026, ejecutada a las 18:20:42 UTC. Solo GET settings y consultas de esquema con select=id/limit=0 sobre las cinco tablas previstas; sin filas, cuentas nuevas, correos ni cambios remotos. URL/clave pública de los .env de ambos repositorios coinciden; tipo anon/publishable verificado antes de enviar. No se muestran valores ni se usa service_role.

| Comprobación | Resultado |
|---|---|
| Auth público | HTTP 200 |
| Google | Habilitado |
| Correo/contraseña | Habilitado |
| Registro | Permitido |
| Confirmación de correo | Requerida (autoconfirm desactivado) |
| profiles/projects/analyses/alignments/external_cache | HTTP 404, PGRST205 en las cinco |

PGRST205 indica que la API pública/PostgREST no encuentra esas relaciones en su esquema expuesto/caché. No demuestra que no haya tablas en otros esquemas ni autoriza aplicar una migración inicial sobre datos existentes. Revisar historial/esquema y aplicar pendientes solo con autorización según [persistencia](project-persistence.md).

Google habilitado no certifica credenciales/redirecciones ni acceso completo. Settings público no confirma proveedor SMTP ni entrega/recuperación reales. No se creó una cuenta ni se solicitó un correo para probarlos. JWT de usuario válido, dos propietarios y CRUD/409 remotos pendientes. Sin modificación de configuración ni publicación.

Recibo reducido en tmp/supabase-public-status.json y script temporal de lectura ignorados. La evidencia de PostgreSQL local y los contratos no cambiaron; esta comprobación documental no requiere repetir pruebas de código.
