"""Canchas: lectura para cualquier usuario autenticado; alta, cambios y baja solo administrador."""
import uuid
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pymongo.errors import PyMongoError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import tiempo
from app.bitacora import asegurar_colecciones
from app.db import get_db
from app.deps import get_current_user, require_rol
from app.models import ESTADOS_OCUPAN_TURNO, Cancha, Reserva
from app.mongo import get_mdb
from app.schemas import CanchaIn, CanchaOut, CanchaUpdate, FichaCancha, Pagina

router = APIRouter(prefix="/canchas", tags=["canchas"])
solo_admin = Depends(require_rol("administrador"))


def _cancha_o_404(db: Session, cancha_id: uuid.UUID) -> Cancha:
    c = db.get(Cancha, cancha_id)
    if c is None:
        raise HTTPException(404, "Cancha no encontrada")
    return c


@router.get("", response_model=Pagina[CanchaOut], dependencies=[Depends(get_current_user)])
def listar(q: str | None = Query(None, max_length=80), deporte: str | None = Query(None, pattern="^(padel|futbol7|tenis)$"),
           incluir_inactivas: bool = False, pagina: int = Query(1, ge=1), tamano: int = Query(20, ge=1, le=100),
           db: Session = Depends(get_db)):
    filtros = []
    if q:
        filtros.append(Cancha.nombre.icontains(q, autoescape=True))
    if deporte:
        filtros.append(Cancha.deporte == deporte)
    if not incluir_inactivas:
        filtros.append(Cancha.activa.is_(True))
    total = db.scalar(select(func.count()).select_from(Cancha).where(*filtros))
    filas = db.scalars(select(Cancha).where(*filtros).order_by(Cancha.deporte, Cancha.nombre)
                       .offset((pagina - 1) * tamano).limit(tamano)).all()
    return Pagina(items=filas, total=total, pagina=pagina, tamano=tamano)


@router.post("", response_model=CanchaOut, status_code=201, dependencies=[solo_admin])
def crear(datos: CanchaIn, db: Session = Depends(get_db)):
    c = Cancha(**datos.model_dump())
    db.add(c)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Ya existe una cancha con ese nombre") from None
    return c


@router.get("/{cancha_id}", response_model=CanchaOut, dependencies=[Depends(get_current_user)])
def detalle(cancha_id: uuid.UUID, db: Session = Depends(get_db)):
    return _cancha_o_404(db, cancha_id)


@router.patch("/{cancha_id}", response_model=CanchaOut, dependencies=[solo_admin])
def actualizar(cancha_id: uuid.UUID, datos: CanchaUpdate, db: Session = Depends(get_db)):
    c = _cancha_o_404(db, cancha_id)
    for campo, valor in datos.model_dump(exclude_unset=True).items():
        if valor is None:
            raise HTTPException(422, f"'{campo}' no puede ser nulo")
        setattr(c, campo, valor)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Ya existe una cancha con ese nombre") from None
    return c


@router.delete("/{cancha_id}", status_code=204, dependencies=[solo_admin])
def dar_de_baja(cancha_id: uuid.UUID, db: Session = Depends(get_db)):
    """Baja lógica (la cuenta de la app no tiene DELETE). No se permite si hay turnos futuros."""
    c = _cancha_o_404(db, cancha_id)
    futuras = db.scalar(select(func.count()).select_from(Reserva).where(
        Reserva.cancha_id == c.id, Reserva.estado.in_(ESTADOS_OCUPAN_TURNO), func.upper(Reserva.periodo) > tiempo.ahora()))
    if futuras:
        raise HTTPException(409, f"La cancha tiene {futuras} reserva(s) vigente(s); cancélalas primero")
    c.activa = False
    db.commit()


@router.get("/{cancha_id}/disponibilidad", dependencies=[Depends(get_current_user)])
def disponibilidad(cancha_id: uuid.UUID, fecha: date, db: Session = Depends(get_db)):
    """Turnos ocupados de un día (hora local). Lo no listado está libre."""
    _cancha_o_404(db, cancha_id)
    z = tiempo.zona()
    desde = datetime.combine(fecha, time.min, tzinfo=z)
    hasta = desde + timedelta(days=1)
    filas = db.scalars(select(Reserva).where(
        Reserva.cancha_id == cancha_id, Reserva.estado.in_(ESTADOS_OCUPAN_TURNO),
        func.lower(Reserva.periodo) < hasta, func.upper(Reserva.periodo) > desde).order_by(func.lower(Reserva.periodo))).all()
    return {"cancha_id": cancha_id, "fecha": fecha, "ocupados": [
        {"inicio": r.periodo.lower, "fin": r.periodo.upper, "estado": r.estado} for r in filas]}


# ---- ficha flexible (MongoDB)
@router.get("/{cancha_id}/ficha", dependencies=[solo_admin])
def ver_ficha(cancha_id: uuid.UUID, db: Session = Depends(get_db), mdb=Depends(get_mdb)):
    _cancha_o_404(db, cancha_id)
    try:
        doc = mdb["ficha_cancha"].find_one({"cancha_id": str(cancha_id)}, {"_id": 0})
    except PyMongoError:
        raise HTTPException(503, "La ficha no está disponible por el momento (MongoDB)") from None
    return doc or {"cancha_id": str(cancha_id)}


@router.put("/{cancha_id}/ficha", dependencies=[solo_admin])
def guardar_ficha(cancha_id: uuid.UUID, ficha: FichaCancha, db: Session = Depends(get_db), mdb=Depends(get_mdb)):
    _cancha_o_404(db, cancha_id)
    doc = {"cancha_id": str(cancha_id), **ficha.model_dump(exclude_none=True)}
    try:
        asegurar_colecciones(mdb)
        mdb["ficha_cancha"].replace_one({"cancha_id": str(cancha_id)}, doc, upsert=True)
    except PyMongoError:
        raise HTTPException(503, "No se pudo guardar la ficha (MongoDB no disponible)") from None
    return doc
