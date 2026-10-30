# Backend (FastAPI)

## Probar la aplicación completa con Docker (desde la raíz del repositorio)
```powershell
copy .env.example .env        # edita .env: contraseñas y JWT_SECRET
docker compose up --build
```
- API y documentación interactiva: http://127.0.0.1:8000/docs
- Salud: http://127.0.0.1:8000/health/live y /health/ready

## Ejecutar las pruebas
Necesitan un PostgreSQL desechable (las pruebas borran y recrean el esquema):
```powershell
docker run -d --name pg-test -e POSTGRES_PASSWORD=test -p 55432:5432 postgres:16
$env:TEST_DATABASE_URL = "postgresql+psycopg://postgres:test@127.0.0.1:55432/postgres"
cd backend
python -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python -m pytest -v
```
