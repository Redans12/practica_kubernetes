"""Reservas, lista de espera, luz, check-in e historial."""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pymongo.errors import PyMongoError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user, require_rol
from app.models import ListaEspera, Reserva, ReservaJugador, Usuario
from app.mongo import get_mdb
from app.schemas import (
    CancelacionOut, EsperaIn, EsperaOut, JugadorIn, LuzOut, Pagina, ReservaIn, ReservaOut,
)
from app.servicios import reservas as srv

router = APIRouter(tags=["reservas"])


def _visible(db: Session, reserva_id: uuid.UUID, usuario: Usuario) -> Reserva:
    """Devuelve la reserva si el usuario puede verla (dueño, jugador invitado o administrador);
    si no, 404 para no revelar que existe. Que pueda verla no significa que pueda modificarla."""
    r = db.get(Reserva, reserva_id)
    if r is None:
        raise HTTPException(404, "Reserva no encontrada")
    if usuario.rol == "administrador" or r.usuario_id == usuario.id:
        return r
    if db.get(ReservaJugador, (r.id, usuario.id)):
        return r
    raise HTTPException(404, "Reserva no encontrada")


@router.post("/reservas", response_model=ReservaOut, status_code=201)
def crear(datos: ReservaIn, usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    r = srv.crear_reserva(db, usuario, datos.cancha_id, datos.inicio, datos.horas, datos.es_partido_abierto,
                          [(m.material_id, m.cantidad) for m in datos.materiales])
    return ReservaOut.desde(r)


@router.get("/reservas", response_model=Pagina[ReservaOut])
def listar(estado: str | None = Query(None, pattern="^(borrador|confirmada|en_juego|completada|liberada_espera|reasignada|expirada)$"),
           cancha_id: uuid.UUID | None = None, desde: datetime | None = None, hasta: datetime | None = None,
           pagina: int = Query(1, ge=1), tamano: int = Query(20, ge=1, le=100),
           usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    """Un jugador ve las suyas (y los partidos a los que lo invitaron); el administrador ve todas."""
    filtros = []
    if usuario.rol != "administrador":
        invitado = select(ReservaJugador.reserva_id).where(ReservaJugador.usuario_id == usuario.id)
        filtros.append(or_(Reserva.usuario_id == usuario.id, Reserva.id.in_(invitado)))
    if estado:
        filtros.append(Reserva.estado == estado)
    if cancha_id:
        filtros.append(Reserva.cancha_id == cancha_id)
    if desde:
        filtros.append(func.upper(Reserva.periodo) > desde)
    if hasta:
        filtros.append(func.lower(Reserva.periodo) < hasta)
    total = db.scalar(select(func.count()).select_from(Reserva).where(*filtros))
    filas = db.scalars(select(Reserva).where(*filtros).order_by(func.lower(Reserva.periodo).desc())
                       .offset((pagina - 1) * tamano).limit(tamano)).all()
    return Pagina(items=[ReservaOut.desde(r) for r in filas], total=total, pagina=pagina, tamano=tamano)


@router.get("/reservas/{reserva_id}", response_model=ReservaOut)
def detalle(reserva_id: uuid.UUID, usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    return ReservaOut.desde(_visible(db, reserva_id, usuario))


@router.post("/reservas/{reserva_id}/pagar", response_model=ReservaOut)
def pagar(reserva_id: uuid.UUID, usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    """Paga el depósito (pasarela simulada) y confirma el borrador."""
    r = _visible(db, reserva_id, usuario)
    if r.usuario_id != usuario.id:
        raise HTTPException(403, "Solo quien creó la reserva puede pagar el depósito")
    return ReservaOut.desde(srv.pagar_deposito(db, r))


@router.post("/reservas/{reserva_id}/cancelar", response_model=CancelacionOut)
def cancelar(reserva_id: uuid.UUID, usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    r = _visible(db, reserva_id, usuario)
    res = srv.cancelar(db, r, usuario)
    return CancelacionOut(reserva=ReservaOut.desde(res["reserva"]), penalizacion=res["penalizacion"],
                          reembolso=res["reembolso"], reasignada_a_reserva=res.get("reasignada_a_reserva"))


@router.post("/reservas/{reserva_id}/jugadores", status_code=204)
def invitar(reserva_id: uuid.UUID, datos: JugadorIn, usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    r = _visible(db, reserva_id, usuario)
    if r.usuario_id != usuario.id:
        raise HTTPException(403, "Solo quien creó el partido puede invitar jugadores")
    jugador = db.scalar(select(Usuario).where(Usuario.email == datos.email.lower(), Usuario.activo.is_(True)))
    if jugador is None:
        raise HTTPException(404, "No existe un jugador con ese correo")
    srv.invitar_jugador(db, r, jugador)


@router.post("/reservas/{reserva_id}/asistencia", status_code=204)
def confirmar_asistencia(reserva_id: uuid.UUID, usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    r = _visible(db, reserva_id, usuario)
    srv.confirmar_asistencia(db, r, usuario)


@router.post("/reservas/{reserva_id}/token-luz", response_model=LuzOut)
def token_luz(reserva_id: uuid.UUID, usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    r = _visible(db, reserva_id, usuario)
    return srv.token_luz(db, r, usuario)


@router.post("/reservas/{reserva_id}/checkin", response_model=ReservaOut)
def checkin(reserva_id: uuid.UUID, admin: Usuario = Depends(require_rol("administrador")), db: Session = Depends(get_db)):
    r = db.get(Reserva, reserva_id)
    if r is None:
        raise HTTPException(404, "Reserva no encontrada")
    return ReservaOut.desde(srv.checkin(db, r, admin))


@router.get("/reservas/{reserva_id}/historial")
def historial(reserva_id: uuid.UUID, usuario: Usuario = Depends(get_current_user),
              db: Session = Depends(get_db), mdb=Depends(get_mdb)):
    """Pestaña 'Historial': eventos de la bitácora en MongoDB, del más reciente al más antiguo."""
    _visible(db, reserva_id, usuario)
    try:
        docs = list(mdb["bitacora_reserva"].find({"reserva_id": str(reserva_id)}, {"_id": 0}).sort([("ts", -1), ("_id", -1)]).limit(200))
    except PyMongoError:
        raise HTTPException(503, "El historial no está disponible por el momento (MongoDB)") from None
    return {"reserva_id": str(reserva_id), "eventos": docs}


# ---- lista de espera
@router.post("/lista-espera", response_model=EsperaOut, status_code=201)
def anotarse(datos: EsperaIn, usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    f = srv.anotar_en_espera(db, usuario, datos.cancha_id, datos.inicio, datos.horas)
    return EsperaOut(id=f.id, cancha_id=f.cancha_id, inicio=f.periodo.lower, fin=f.periodo.upper)


@router.get("/lista-espera", response_model=list[EsperaOut])
def mis_esperas(usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    filas = db.scalars(select(ListaEspera).where(ListaEspera.usuario_id == usuario.id).order_by(ListaEspera.creado_en)).all()
    return [EsperaOut(id=f.id, cancha_id=f.cancha_id, inicio=f.periodo.lower, fin=f.periodo.upper) for f in filas]


@router.delete("/lista-espera/{espera_id}", status_code=204)
def salir_de_espera(espera_id: uuid.UUID, usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    f = db.get(ListaEspera, espera_id)
    if f is None or f.usuario_id != usuario.id:
        raise HTTPException(404, "No estás en esa lista de espera")
    db.delete(f)
    db.commit()
