# Gestión de canchas deportivas — Kubernetes (desarrollo y producción)

Práctica integradora individual · Infraestructura de Servicios · Instituto Tecnológico de Morelia
Alumno: Andres (22121298)

## Estructura
| Carpeta | Contenido |
|---|---|
| `frontend/` | Interfaz React |
| `backend/` | API FastAPI, migraciones y semillas |
| `pruebas/` | Pruebas funcionales y de carga |
| `infra/base`, `infra/desarrollo`, `infra/produccion` | Manifiestos Kustomize (base + overlays por ambiente) |
| `observabilidad/` | Métricas, paneles y alertas |
| `red/` | NetworkPolicy y reglas de firewall |
| `respaldos/` | Scripts y CronJobs de respaldo y restauración |
| `docs/` | Expediente técnico (D01–D10) y diagramas |

## Estado
- [x] Paso 1: modelo de datos (`docs/D04-modelo-de-datos.md`)
- [ ] VM base Ubuntu Server 24.04.5 LTS
- [ ] API FastAPI, frontend, Dockerfiles
- [ ] Clúster de desarrollo (k3s)
- [ ] Clúster de producción (k3s, 1 control plane + 2 workers)

> No subir a Git: contraseñas, kubeconfig ni dumps con datos sensibles.
