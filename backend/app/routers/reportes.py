"""Reporte de ocupación e ingresos por cancha (solo administrador)."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_rol

router = APIRouter(prefix="/reportes", tags=["reportes"], dependencies=[Depends(require_rol("administrador"))])

SQL = text("""
SELECT c.id, c.nombre, c.deporte,
       count(r.id) FILTER (WHERE r.estado IN ('confirmada','en_juego','completada'))                       AS reservas,
       coalesce(sum(extract(epoch FROM upper(r.periodo) - lower(r.periodo)) / 3600)
                FILTER (WHERE r.estado IN ('confirmada','en_juego','completada')), 0)                     AS horas,
       count(r.id) FILTER (WHERE r.estado = 'liberada_espera' OR r.estado = 'reasignada')                 AS liberadas,
       coalesce(sum(r.penalizacion), 0)                                                                    AS penalizaciones,
       (SELECT coalesce(sum(p.monto), 0) FROM pago p JOIN reserva r2 ON r2.id = p.reserva_id
         WHERE r2.cancha_id = c.id AND r2.periodo && tstzrange(:desde, :hasta, '[)') AND p.estado = 'aprobado') AS ingresos
FROM cancha c
LEFT JOIN reserva r ON r.cancha_id = c.id AND r.periodo && tstzrange(:desde, :hasta, '[)')
GROUP BY c.id, c.nombre, c.deporte
ORDER BY horas DESC, c.nombre
""")


@router.get("/ocupacion")
def ocupacion(desde: datetime, hasta: datetime, db: Session = Depends(get_db)):
    if hasta <= desde:
        raise HTTPException(422, "'hasta' debe ser posterior a 'desde'")
    filas = db.execute(SQL, {"desde": desde, "hasta": hasta}).mappings().all()
    return {"desde": desde, "hasta": hasta, "canchas": [
        {"cancha_id": f["id"], "nombre": f["nombre"], "deporte": f["deporte"], "reservas": f["reservas"],
         "horas": float(f["horas"]), "liberadas": f["liberadas"], "penalizaciones": f["penalizaciones"],
         "ingresos": f["ingresos"]} for f in filas]}
