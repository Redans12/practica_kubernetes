"""Pruebas del esquema: reglas que debe garantizar la propia base de datos."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.exc import IntegrityError

from app.models import Cancha, Reserva, Usuario

BASE = datetime(2026, 11, 3, 18, 0, tzinfo=timezone.utc)


def _turno(hora_inicio: int, horas: int = 1) -> Range:
    inicio = BASE + timedelta(hours=hora_inicio)
    return Range(inicio, inicio + timedelta(hours=horas), bounds="[)")


@pytest.fixture
def base(db):
    usuario = Usuario(email="u@ejemplo.test", nombre="U", password_hash="x")
    cancha = Cancha(nombre="Pádel 1", deporte="padel", tarifa_hora=300, deposito=100)
    db.add_all([usuario, cancha])
    db.commit()
    return usuario, cancha


def _reserva(usuario, cancha, periodo, estado="confirmada"):
    return Reserva(usuario_id=usuario.id, cancha_id=cancha.id, periodo=periodo, estado=estado)


def test_regla4_turnos_traslapados_se_rechazan(db, base):
    usuario, cancha = base
    db.add(_reserva(usuario, cancha, _turno(0)))
    db.commit()
    db.add(_reserva(usuario, cancha, _turno(0)))  # mismo horario
    with pytest.raises(IntegrityError) as e:
        db.commit()
    assert "reserva_sin_traslape" in str(e.value)


def test_regla4_traslape_parcial_se_rechaza(db, base):
    usuario, cancha = base
    db.add(_reserva(usuario, cancha, _turno(0, horas=2)))  # 18:00-20:00
    db.commit()
    db.add(_reserva(usuario, cancha, _turno(1)))           # 19:00-20:00
    with pytest.raises(IntegrityError):
        db.commit()


def test_regla4_turnos_contiguos_se_permiten(db, base):
    usuario, cancha = base
    db.add_all([_reserva(usuario, cancha, _turno(0)), _reserva(usuario, cancha, _turno(1))])
    db.commit()  # 18-19 y 19-20 no se traslapan


def test_regla4_otra_cancha_mismo_horario_se_permite(db, base):
    usuario, cancha = base
    otra = Cancha(nombre="Pádel 2", deporte="padel", tarifa_hora=300)
    db.add(otra)
    db.commit()
    db.add_all([_reserva(usuario, cancha, _turno(0)), _reserva(usuario, otra, _turno(0))])
    db.commit()


def test_regla4_turno_liberado_ya_no_bloquea(db, base):
    usuario, cancha = base
    db.add(_reserva(usuario, cancha, _turno(0), estado="liberada_espera"))
    db.commit()
    db.add(_reserva(usuario, cancha, _turno(0)))  # el turno quedó libre
    db.commit()


def test_estado_invalido_se_rechaza(db, base):
    usuario, cancha = base
    db.add(_reserva(usuario, cancha, _turno(0), estado="inventado"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_cancha_con_tarifa_no_positiva_se_rechaza(db):
    db.add(Cancha(nombre="Mala", deporte="tenis", tarifa_hora=0))
    with pytest.raises(IntegrityError):
        db.commit()


def test_rol_invalido_se_rechaza(db):
    db.add(Usuario(email="r@ejemplo.test", nombre="R", password_hash="x", rol="superusuario"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_email_duplicado_se_rechaza(db):
    db.add(Usuario(email="d@ejemplo.test", nombre="D", password_hash="x"))
    db.commit()
    db.add(Usuario(email="d@ejemplo.test", nombre="D2", password_hash="x"))
    with pytest.raises(IntegrityError):
        db.commit()
