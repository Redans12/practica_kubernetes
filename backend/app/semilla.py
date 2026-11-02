"""Datos sintéticos de demostración (ver D04, sección 7). Uso:  SEED_PASSWORD=... python -m app.semilla

- Idempotente: si ya existe el administrador de la semilla, no hace nada.
- Todos los correos terminan en @ejemplo.test (dominio reservado para ejemplos).
- La contraseña viene de la variable SEED_PASSWORD (en Kubernetes, de un Secret). No hay contraseñas en el código.
- Se corre con la cuenta de la aplicación (canchas_app): solo necesita INSERT.
"""
import json
import os
import random
import sys
from datetime import datetime, timedelta, time as hora

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.orm import Session

from app import tiempo
from app.bitacora import asegurar_colecciones
from app.db import get_sessionmaker
from app.models import Cancha, Material, Pago, Reserva, ReservaJugador, ReservaMaterial, Usuario
from app.mongo import get_mongo_db
from app.security import hash_password
from app.servicios.reservas import CUPO

ADMIN_EMAIL = "admin@ejemplo.test"

CANCHAS = [  # nombre, deporte, tarifa/hora, depósito
    ("Pádel 1", "padel", 400, 150), ("Pádel 2", "padel", 400, 150), ("Pádel 3 (panorámica)", "padel", 480, 200),
    ("Fútbol 7 A", "futbol7", 900, 300), ("Fútbol 7 B", "futbol7", 900, 300), ("Fútbol 7 C (techada)", "futbol7", 1100, 400),
    ("Tenis 1 (arcilla)", "tenis", 350, 100), ("Tenis 2 (dura)", "tenis", 300, 100), ("Tenis 3 (dura)", "tenis", 300, 100),
]
MATERIALES = [  # nombre, tipo, cantidad, precio
    ("Raqueta de pádel", "raqueta", 12, 60), ("Pelotas de pádel (tubo)", "pelotas", 20, 40),
    ("Raqueta de tenis", "raqueta", 10, 50), ("Pelotas de tenis (tubo)", "pelotas", 20, 35),
    ("Balón de fútbol", "balon", 8, 30), ("Chalecos (juego de 7)", "chalecos", 6, 70),
    ("Red portátil", "red", 3, 80), ("Botiquín", "botiquin", 4, 0),
]
MATERIAL_POR_DEPORTE = {
    "padel": {"Raqueta de pádel", "Pelotas de pádel (tubo)", "Botiquín"},
    "tenis": {"Raqueta de tenis", "Pelotas de tenis (tubo)", "Red portátil"},
    "futbol7": {"Balón de fútbol", "Chalecos (juego de 7)", "Botiquín"},
}
FICHAS = {
    "padel": lambda r: {"dimensiones": {"largo_m": 20, "ancho_m": 10}, "piso": "césped sintético", "techada": r.random() < 0.3,
                        "iluminacion": {"potencia_w": 800, "zonas": 4}, "extras": {"paredes": "cristal templado", "altura_malla_m": 3}},
    "futbol7": lambda r: {"dimensiones": {"largo_m": 60, "ancho_m": 35}, "piso": "pasto sintético", "techada": r.random() < 0.3,
                          "iluminacion": {"potencia_w": 2400, "zonas": 6}, "extras": {"porterias": 2, "gradas": r.random() < 0.5}},
    "tenis": lambda r: {"dimensiones": {"largo_m": 23.77, "ancho_m": 10.97}, "piso": "arcilla" if r.random() < 0.3 else "dura",
                        "techada": False, "iluminacion": {"potencia_w": 1200, "zonas": 4}, "extras": {"altura_red_cm": 91.4}},
}


def sembrar(db: Session, password: str, mdb=None, rng: random.Random | None = None) -> dict:
    rng = rng or random.Random(2026)
    if db.scalar(select(func.count()).select_from(Usuario).where(Usuario.email == ADMIN_EMAIL)):
        return {"mensaje": "La semilla ya estaba aplicada; no se hizo nada"}

    hash_comun = hash_password(password)   # misma contraseña de demostración para todos los usuarios sintéticos
    usuarios = [Usuario(email=ADMIN_EMAIL, nombre="Administración Canchas", password_hash=hash_comun, rol="administrador")]
    usuarios += [Usuario(email=f"jugador{i:02d}@ejemplo.test", nombre=f"Jugador {i:02d}", password_hash=hash_comun,
                         ranking_puntos=900 + rng.randint(0, 300)) for i in range(1, 20)]
    canchas = [Cancha(nombre=n, deporte=d, tarifa_hora=t, deposito=dep) for n, d, t, dep in CANCHAS]
    materiales = [Material(nombre=n, tipo=t, cantidad_total=c, precio_renta=p) for n, t, c, p in MATERIALES]
    db.add_all(usuarios + canchas + materiales)
    db.flush()
    jugadores = usuarios[1:]

    ahora = tiempo.ahora()
    zona = tiempo.zona()
    hoy_local = ahora.astimezone(zona).date()
    reservas, pagos, rm, rj = [], [], [], []
    ESTADOS_PASADO = ["completada"] * 5 + ["liberada_espera", "expirada"]
    ESTADOS_FUTURO = ["confirmada"] * 4 + ["borrador_vencido"]
    for i in range(70):
        cancha = canchas[i % len(canchas)]
        k = i // len(canchas)                         # número de reserva dentro de la cancha: un día distinto cada una
        pasado = k % 2 == 0
        dia = hoy_local + timedelta(days=-(k // 2 + 1) if pasado else (k // 2 + 1))
        hora_inicio = rng.choice([8, 10, 17, 18, 19, 20])
        inicio = datetime.combine(dia, hora(hora_inicio), tzinfo=zona).astimezone(ahora.tzinfo)
        horas = rng.choice([1, 1, 2])
        estado = rng.choice(ESTADOS_PASADO if pasado else ESTADOS_FUTURO)
        if estado == "borrador_vencido":
            estado = "expirada"
        abierto = cancha.deporte in ("padel", "tenis") and rng.random() < 0.35 and not pasado
        r = Reserva(usuario_id=rng.choice(jugadores).id, cancha_id=cancha.id, estado=estado, es_partido_abierto=abierto,
                    periodo=Range(inicio, inicio + timedelta(hours=horas), bounds="[)"), deposito=cancha.deposito)
        if estado == "liberada_espera":
            r.penalizacion = round(float(cancha.deposito) * rng.choice([0, 0.5, 1]), 2)
        reservas.append(r)
    db.add_all(reservas)
    db.flush()

    for r in reservas:
        cancha = next(c for c in canchas if c.id == r.cancha_id)
        if r.estado in ("confirmada", "completada", "liberada_espera") and r.deposito > 0:
            pagos.append(Pago(reserva_id=r.id, tipo="deposito", monto=r.deposito, estado="aprobado"))
        if r.penalizacion and r.penalizacion > 0:
            pagos.append(Pago(reserva_id=r.id, tipo="penalizacion", monto=r.penalizacion, estado="aprobado"))
        if r.estado in ("confirmada", "completada") and rng.random() < 0.4:
            m = rng.choice([x for x in materiales if x.nombre in MATERIAL_POR_DEPORTE[cancha.deporte]])
            rm.append(ReservaMaterial(reserva_id=r.id, material_id=m.id, cantidad=1))
        if r.es_partido_abierto:
            cupo = CUPO[cancha.deporte]
            otros = rng.sample([j for j in jugadores if j.id != r.usuario_id], rng.randint(0, cupo - 1))
            rj.append(ReservaJugador(reserva_id=r.id, usuario_id=r.usuario_id, confirmado=True))
            rj += [ReservaJugador(reserva_id=r.id, usuario_id=j.id, confirmado=rng.random() < 0.6) for j in otros]
    db.add_all(pagos + rm + rj)
    db.commit()

    fichas = 0
    if mdb is not None:
        asegurar_colecciones(mdb)
        for c in canchas:
            doc = {"cancha_id": str(c.id), **FICHAS[c.deporte](rng)}
            mdb["ficha_cancha"].replace_one({"cancha_id": doc["cancha_id"]}, doc, upsert=True)
            fichas += 1
    return {"usuarios": len(usuarios), "canchas": len(canchas), "materiales": len(materiales), "reservas": len(reservas),
            "pagos": len(pagos), "reserva_material": len(rm), "reserva_jugador": len(rj), "fichas_mongo": fichas,
            "total_postgres": len(usuarios) + len(canchas) + len(materiales) + len(reservas) + len(pagos) + len(rm) + len(rj)}


def main() -> int:
    password = os.environ.get("SEED_PASSWORD", "")
    if len(password) < 10:
        print("Falta SEED_PASSWORD (mínimo 10 caracteres)", file=sys.stderr)
        return 2
    with get_sessionmaker()() as db:
        try:
            mdb = get_mongo_db()
            mdb.command("ping")
        except Exception:  # noqa: BLE001
            print("Aviso: MongoDB no responde; se siembra solo PostgreSQL", file=sys.stderr)
            mdb = None
        print(json.dumps(sembrar(db, password, mdb), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
