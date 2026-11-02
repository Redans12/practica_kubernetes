"""Reglas de negocio de las reservas.

Regla 1  Cancelación progresiva        -> cancelar()
Regla 2  Quórum en partidos abiertos   -> revisar_quorum()
Regla 3  Luz solo en la ventana ±10min -> token_luz()
Regla 4  Un turno, una reserva         -> restricción EXCLUDE de PostgreSQL (aquí solo se traduce el error)
Cada operación pública hace commit al final: o queda todo (reserva + pago + outbox) o no queda nada.
"""
import logging
import uuid
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

import jwt
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import tiempo
from app.bitacora import emitir
from app.config import get_settings
from app.models import (
    ESTADOS_OCUPAN_TURNO, Cancha, ListaEspera, Material, Pago, Reserva, ReservaJugador,
    ReservaMaterial, Usuario,
)
from app.servicios.errores import ReglaDeNegocio

log = logging.getLogger("canchas.reservas")

CUPO = {"padel": 4, "tenis": 2, "futbol7": 14}   # jugadores de un partido abierto completo
HORAS_MAX = 3
ANTICIPACION_MAX_DIAS = 60
ANTICIPACION_PARTIDO_ABIERTO_H = 3               # debe quedar tiempo para juntar al grupo antes del corte de 2 h
VENTANA_LUZ_MIN = 10                             # regla 3
CORTE_QUORUM_H = 2                               # regla 2
BLOQUEO_ESTELAR_DIAS = 7                         # regla 1
TOLERANCIA_NO_SHOW_MIN = 15
PUNTOS_POR_PARTIDO = 10

# Máquina de estados (D04, sección 4): cualquier otra transición se rechaza.
TRANSICIONES = {
    ("borrador", "confirmada"), ("borrador", "expirada"),
    ("confirmada", "en_juego"), ("confirmada", "liberada_espera"),
    ("en_juego", "completada"), ("liberada_espera", "reasignada"),
}


def _dinero(valor: Decimal) -> Decimal:
    return Decimal(valor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def inicio_fin(reserva: Reserva) -> tuple[datetime, datetime]:
    return reserva.periodo.lower, reserva.periodo.upper


def cambiar_estado(db: Session, reserva: Reserva, nuevo: str, *, actor_id=None, detalle: dict | None = None) -> None:
    if (reserva.estado, nuevo) not in TRANSICIONES:
        raise ReglaDeNegocio(f"Transición no permitida: {reserva.estado} → {nuevo}")
    anterior = reserva.estado
    reserva.estado = nuevo
    reserva.expira_en = None
    emitir(db, "cambio_estado", reserva, usuario_id=actor_id, de_estado=anterior, a_estado=nuevo, detalle=detalle)


# ---------------------------------------------------------------- crear
def crear_reserva(db: Session, usuario: Usuario, cancha_id: uuid.UUID, inicio: datetime, horas: int,
                  abierto: bool, materiales: list[tuple[uuid.UUID, int]]) -> Reserva:
    now = tiempo.ahora()
    s = get_settings()
    local = inicio.astimezone(tiempo.zona())
    if local.minute or local.second or local.microsecond:
        raise ReglaDeNegocio("El turno debe comenzar en punto (por ejemplo 18:00)", 422)
    if inicio <= now:
        raise ReglaDeNegocio("El turno debe ser en el futuro", 422)
    if inicio > now + timedelta(days=ANTICIPACION_MAX_DIAS):
        raise ReglaDeNegocio(f"Solo se puede reservar con {ANTICIPACION_MAX_DIAS} días de anticipación como máximo", 422)
    if abierto and inicio < now + timedelta(hours=ANTICIPACION_PARTIDO_ABIERTO_H):
        raise ReglaDeNegocio(
            f"Un partido abierto se crea con al menos {ANTICIPACION_PARTIDO_ABIERTO_H} horas de anticipación", 422)

    cancha = db.get(Cancha, cancha_id)
    if cancha is None or not cancha.activa:
        raise ReglaDeNegocio("Cancha no encontrada", 404)

    fin = inicio + timedelta(hours=horas)
    if usuario.bloqueado_hasta and usuario.bloqueado_hasta > now and tiempo.es_horario_estelar(inicio):
        raise ReglaDeNegocio(
            "Tienes bloqueado el horario estelar por una cancelación tardía hasta "
            f"{usuario.bloqueado_hasta.astimezone(tiempo.zona()):%d/%m/%Y %H:%M}", 403)

    periodo = Range(inicio, fin, bounds="[)")
    reserva = Reserva(
        usuario_id=usuario.id, cancha_id=cancha.id, periodo=periodo, estado="borrador",
        es_partido_abierto=abierto, deposito=cancha.deposito,
        expira_en=now + timedelta(minutes=s.borrador_minutos),
    )
    db.add(reserva)
    try:
        db.flush()   # aquí PostgreSQL aplica la regla 4
    except IntegrityError as e:
        db.rollback()
        if "reserva_sin_traslape" in str(e.orig):
            raise ReglaDeNegocio("Ese horario ya está ocupado. Puedes anotarte en la lista de espera.", 409) from None
        raise

    _agregar_materiales(db, reserva, periodo, materiales)
    if abierto:
        db.add(ReservaJugador(reserva_id=reserva.id, usuario_id=usuario.id, confirmado=False))
    emitir(db, "cambio_estado", reserva, usuario_id=usuario.id, de_estado=None, a_estado="borrador",
           detalle={"horas": horas, "partido_abierto": abierto})
    db.commit()
    return reserva


def _agregar_materiales(db: Session, reserva: Reserva, periodo: Range, materiales: list[tuple[uuid.UUID, int]]) -> None:
    if not materiales:
        return
    ids = [m for m, _ in materiales]
    if len(set(ids)) != len(ids):
        raise ReglaDeNegocio("Material repetido en la solicitud", 422)
    # Se bloquean las filas de material en orden fijo: dos reservas simultáneas no pueden pasarse del inventario.
    filas = {m.id: m for m in db.scalars(
        select(Material).where(Material.id.in_(ids)).order_by(Material.id).with_for_update())}
    for material_id, cantidad in materiales:
        material = filas.get(material_id)
        if material is None or not material.activo:
            raise ReglaDeNegocio("Material no encontrado", 404)
        usado = db.scalar(
            select(func.coalesce(func.sum(ReservaMaterial.cantidad), 0))
            .join(Reserva, Reserva.id == ReservaMaterial.reserva_id)
            .where(ReservaMaterial.material_id == material_id, Reserva.estado.in_(ESTADOS_OCUPAN_TURNO),
                   Reserva.periodo.overlaps(periodo), Reserva.id != reserva.id)
        )
        disponible = material.cantidad_total - int(usado)
        if cantidad > disponible:
            raise ReglaDeNegocio(f"No hay suficiente '{material.nombre}' en ese horario (disponibles: {max(disponible, 0)})")
        db.add(ReservaMaterial(reserva_id=reserva.id, material_id=material_id, cantidad=cantidad))


# ---------------------------------------------------------------- pagar / confirmar
def pagar_deposito(db: Session, reserva: Reserva) -> Reserva:
    if reserva.estado != "borrador":
        raise ReglaDeNegocio(f"Solo se puede pagar un borrador (estado actual: {reserva.estado})")
    if reserva.expira_en is not None and reserva.expira_en <= tiempo.ahora():
        cambiar_estado(db, reserva, "expirada", detalle={"motivo": "borrador_vencido"})
        db.commit()
        raise ReglaDeNegocio("El borrador expiró y el horario fue liberado", 410)
    if reserva.deposito > 0:
        pago = Pago(reserva_id=reserva.id, tipo="deposito", monto=reserva.deposito, estado="aprobado")
        db.add(pago)
        emitir(db, "pago", reserva, detalle={"tipo": "deposito", "monto": str(reserva.deposito), "pasarela": "simulada"})
    cambiar_estado(db, reserva, "confirmada", detalle={"regla": 2, "pago": "deposito"})
    organizador = db.get(ReservaJugador, (reserva.id, reserva.usuario_id))
    if organizador:
        organizador.confirmado = True
    db.commit()
    return reserva


# ---------------------------------------------------------------- jugadores (partido abierto)
def invitar_jugador(db: Session, reserva: Reserva, jugador: Usuario) -> None:
    if not reserva.es_partido_abierto:
        raise ReglaDeNegocio("Solo los partidos abiertos tienen jugadores")
    if reserva.estado not in ("borrador", "confirmada"):
        raise ReglaDeNegocio("Ya no se pueden agregar jugadores a esta reserva")
    cancha = db.get(Cancha, reserva.cancha_id)
    actuales = db.scalar(select(func.count()).select_from(ReservaJugador).where(ReservaJugador.reserva_id == reserva.id))
    if actuales >= CUPO[cancha.deporte]:
        raise ReglaDeNegocio(f"El cupo de {cancha.deporte} ({CUPO[cancha.deporte]} jugadores) ya está completo")
    db.add(ReservaJugador(reserva_id=reserva.id, usuario_id=jugador.id, confirmado=False))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ReglaDeNegocio("Ese jugador ya está en la reserva") from None


def confirmar_asistencia(db: Session, reserva: Reserva, usuario: Usuario) -> None:
    """El jugador invitado confirma su asistencia y su pago (la pasarela es simulada)."""
    if reserva.estado not in ("borrador", "confirmada"):
        raise ReglaDeNegocio("La reserva ya no admite confirmaciones")
    fila = db.get(ReservaJugador, (reserva.id, usuario.id))
    if fila is None:
        raise ReglaDeNegocio("No estás invitado a este partido", 403)
    fila.confirmado = True
    db.commit()


def quorum_completo(db: Session, reserva: Reserva) -> bool:
    """Regla 2: cupo completo del deporte y 100 % de los jugadores confirmados."""
    cancha = db.get(Cancha, reserva.cancha_id)
    filas = db.scalars(select(ReservaJugador).where(ReservaJugador.reserva_id == reserva.id)).all()
    return len(filas) == CUPO[cancha.deporte] and all(f.confirmado for f in filas)


# ---------------------------------------------------------------- cancelar (regla 1)
def porcentaje_penalizacion(horas_anticipacion: float) -> Decimal:
    if horas_anticipacion >= 4:
        return Decimal("0")
    if horas_anticipacion >= 1:
        return Decimal("0.5")
    return Decimal("1")


def _aplicar_penalizacion(db: Session, reserva: Reserva, pct: Decimal, *, motivo: str, horas: float) -> Decimal:
    penal = _dinero(reserva.deposito * pct)
    reserva.penalizacion = penal
    detalle = {"regla": 1, "motivo": motivo, "horas_anticipacion": round(horas, 2),
               "porcentaje": int(pct * 100), "penalizacion": str(penal)}
    if penal > 0:
        db.add(Pago(reserva_id=reserva.id, tipo="penalizacion", monto=penal, estado="aprobado"))
        emitir(db, "penalizacion", reserva, detalle=detalle)
    if pct == Decimal("1"):
        dueño = db.get(Usuario, reserva.usuario_id)
        dueño.bloqueado_hasta = tiempo.ahora() + timedelta(days=BLOQUEO_ESTELAR_DIAS)
        detalle["bloqueado_hasta"] = dueño.bloqueado_hasta.isoformat()
    return penal


def cancelar(db: Session, reserva: Reserva, actor: Usuario) -> dict:
    if actor.id != reserva.usuario_id and actor.rol != "administrador":
        raise ReglaDeNegocio("Solo quien creó la reserva puede cancelarla", 403)
    now = tiempo.ahora()
    inicio, _ = inicio_fin(reserva)

    if reserva.estado == "borrador":
        cambiar_estado(db, reserva, "expirada", actor_id=actor.id, detalle={"motivo": "cancelado_en_borrador"})
        db.commit()
        return {"reserva": reserva, "penalizacion": Decimal("0.00"), "reembolso": Decimal("0.00")}
    if reserva.estado != "confirmada":
        raise ReglaDeNegocio(f"No se puede cancelar una reserva en estado {reserva.estado}")

    horas = (inicio - now).total_seconds() / 3600
    if horas <= 0:
        raise ReglaDeNegocio("El turno ya comenzó; no se puede cancelar")
    por_admin = actor.id != reserva.usuario_id
    pct = Decimal("0") if por_admin else porcentaje_penalizacion(horas)
    motivo = "cancelada_por_administrador" if por_admin else "cancelada_por_jugador"
    penal = _aplicar_penalizacion(db, reserva, pct, motivo=motivo, horas=horas)
    cambiar_estado(db, reserva, "liberada_espera", actor_id=actor.id,
                   detalle={"regla": 1, "motivo": motivo, "horas_anticipacion": round(horas, 2), "penalizacion": str(penal)})
    nueva = reasignar_si_hay_espera(db, reserva)
    db.commit()
    return {"reserva": reserva, "penalizacion": penal, "reembolso": _dinero(reserva.deposito - penal),
            "reasignada_a_reserva": nueva.id if nueva else None}


# ---------------------------------------------------------------- lista de espera y reasignación (regla 4)
def anotar_en_espera(db: Session, usuario: Usuario, cancha_id: uuid.UUID, inicio: datetime, horas: int) -> ListaEspera:
    cancha = db.get(Cancha, cancha_id)
    if cancha is None or not cancha.activa:
        raise ReglaDeNegocio("Cancha no encontrada", 404)
    periodo = Range(inicio, inicio + timedelta(hours=horas), bounds="[)")
    ocupado = db.scalar(select(func.count()).select_from(Reserva).where(
        Reserva.cancha_id == cancha_id, Reserva.estado.in_(ESTADOS_OCUPAN_TURNO), Reserva.periodo.overlaps(periodo)))
    if not ocupado:
        raise ReglaDeNegocio("Ese horario está libre: reserva directamente")
    ya = db.scalar(select(func.count()).select_from(ListaEspera).where(
        ListaEspera.cancha_id == cancha_id, ListaEspera.usuario_id == usuario.id, ListaEspera.periodo == periodo))
    if ya:
        raise ReglaDeNegocio("Ya estás en la lista de espera de ese horario")
    fila = ListaEspera(cancha_id=cancha_id, usuario_id=usuario.id, periodo=periodo)
    db.add(fila)
    db.commit()
    return fila


def reasignar_si_hay_espera(db: Session, original: Reserva) -> Reserva | None:
    """Si alguien espera exactamente este turno, la reserva original pasa a `reasignada` y se crea
    un borrador nuevo para la persona que esperaba (primero en llegar, primero en ser atendido)."""
    siguiente = db.scalars(
        select(ListaEspera).where(ListaEspera.cancha_id == original.cancha_id, ListaEspera.periodo == original.periodo)
        .order_by(ListaEspera.creado_en).limit(1).with_for_update(skip_locked=True)
    ).first()
    if siguiente is None:
        return None
    cambiar_estado(db, original, "reasignada", detalle={"regla": 4, "reasignada_a_usuario": str(siguiente.usuario_id)})
    db.flush()   # el turno queda libre antes de insertar el nuevo borrador
    cancha = db.get(Cancha, original.cancha_id)
    nueva = Reserva(
        usuario_id=siguiente.usuario_id, cancha_id=original.cancha_id, periodo=original.periodo, estado="borrador",
        deposito=cancha.deposito, expira_en=tiempo.ahora() + timedelta(minutes=get_settings().borrador_minutos),
    )
    db.add(nueva)
    db.flush()
    emitir(db, "cambio_estado", nueva, de_estado=None, a_estado="borrador",
           detalle={"regla": 4, "origen": "lista_espera", "reserva_original": str(original.id)})
    db.execute(delete(ListaEspera).where(ListaEspera.id == siguiente.id))
    return nueva


# ---------------------------------------------------------------- luz y check-in (regla 3)
def token_luz(db: Session, reserva: Reserva, usuario: Usuario) -> dict:
    es_jugador = usuario.id == reserva.usuario_id or db.get(ReservaJugador, (reserva.id, usuario.id)) is not None
    if not es_jugador and usuario.rol != "administrador":
        raise ReglaDeNegocio("Reserva no encontrada", 404)
    if reserva.estado != "confirmada":
        raise ReglaDeNegocio(f"La luz solo se activa con una reserva confirmada (estado actual: {reserva.estado})", 403)
    inicio, fin = inicio_fin(reserva)
    delta = (tiempo.ahora() - inicio).total_seconds() / 60
    if abs(delta) > VENTANA_LUZ_MIN:
        raise ReglaDeNegocio(f"El token de luz solo se emite entre {VENTANA_LUZ_MIN} min antes y {VENTANA_LUZ_MIN} min después del inicio", 403)
    s = get_settings()
    token = jwt.encode({"sub": str(reserva.id), "scope": "luz", "cancha": str(reserva.cancha_id), "exp": fin},
                       s.jwt_secret, algorithm=s.jwt_algorithm)
    emitir(db, "luz_token", reserva, usuario_id=usuario.id, detalle={"regla": 3, "minutos_desde_inicio": round(delta, 1)})
    db.commit()
    return {"token": token, "valido_hasta": fin}


def checkin(db: Session, reserva: Reserva, admin: Usuario) -> Reserva:
    inicio, fin = inicio_fin(reserva)
    now = tiempo.ahora()
    if reserva.estado == "confirmada" and not (inicio - timedelta(minutes=VENTANA_LUZ_MIN) <= now < fin):
        raise ReglaDeNegocio("El check-in solo puede hacerse desde 10 min antes del inicio y hasta el fin del turno", 403)
    cambiar_estado(db, reserva, "en_juego", actor_id=admin.id, detalle={"regla": 3})
    db.commit()
    return reserva


# ---------------------------------------------------------------- tareas programadas (CronJobs)
def expirar_borradores(db: Session) -> int:
    filas = db.scalars(select(Reserva).where(Reserva.estado == "borrador", Reserva.expira_en <= tiempo.ahora())
                       .with_for_update(skip_locked=True)).all()
    for r in filas:
        cambiar_estado(db, r, "expirada", detalle={"motivo": "borrador_vencido"})
    db.commit()
    return len(filas)


def revisar_quorum(db: Session) -> dict:
    """Regla 2: 2 h antes del inicio, un partido abierto sin quórum completo libera el turno."""
    limite = tiempo.ahora() + timedelta(hours=CORTE_QUORUM_H)
    filas = db.scalars(select(Reserva).where(
        Reserva.estado == "confirmada", Reserva.es_partido_abierto.is_(True), func.lower(Reserva.periodo) <= limite,
        func.lower(Reserva.periodo) > tiempo.ahora()).with_for_update(skip_locked=True)).all()
    liberadas = reasignadas = 0
    for r in filas:
        if quorum_completo(db, r):
            continue
        cambiar_estado(db, r, "liberada_espera", detalle={"regla": 2, "motivo": "quorum_incompleto"})
        liberadas += 1
        if reasignar_si_hay_espera(db, r):
            reasignadas += 1
    db.commit()
    return {"revisadas": len(filas), "liberadas": liberadas, "reasignadas": reasignadas}


def marcar_no_show(db: Session) -> int:
    """No-show = no hubo check-in 15 min después del inicio: se trata como cancelación de último minuto."""
    limite = tiempo.ahora() - timedelta(minutes=TOLERANCIA_NO_SHOW_MIN)
    filas = db.scalars(select(Reserva).where(Reserva.estado == "confirmada", func.lower(Reserva.periodo) <= limite)
                       .with_for_update(skip_locked=True)).all()
    for r in filas:
        _aplicar_penalizacion(db, r, Decimal("1"), motivo="no_show", horas=0)
        cambiar_estado(db, r, "liberada_espera", detalle={"regla": 1, "motivo": "no_show"})
    db.commit()
    return len(filas)


def finalizar_turnos(db: Session) -> int:
    filas = db.scalars(select(Reserva).where(Reserva.estado == "en_juego", func.upper(Reserva.periodo) <= tiempo.ahora())
                       .with_for_update(skip_locked=True)).all()
    for r in filas:
        cambiar_estado(db, r, "completada", detalle={"ranking": f"+{PUNTOS_POR_PARTIDO}"})
        participantes = {r.usuario_id} | set(db.scalars(
            select(ReservaJugador.usuario_id).where(ReservaJugador.reserva_id == r.id, ReservaJugador.confirmado.is_(True))))
        for u in db.scalars(select(Usuario).where(Usuario.id.in_(participantes))):
            u.ranking_puntos += PUNTOS_POR_PARTIDO
    db.commit()
    return len(filas)
