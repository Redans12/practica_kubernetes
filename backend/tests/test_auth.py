"""Pruebas de autenticación y autorización: válidos, inválidos, duplicados, sin permisos, sesión expirada."""
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends

from app.config import get_settings
from app.deps import require_rol
from app.main import app
from app.models import Usuario
from app.security import hash_password

DATOS = {"email": "ana@ejemplo.test", "nombre": "Ana Pérez", "password": "clave-segura-123"}


def _registrar(client, **extra):
    return client.post("/auth/registro", json={**DATOS, **extra})


def _token(client, email=DATOS["email"], password=DATOS["password"]):
    r = client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def test_registro_valido_no_devuelve_password(client):
    r = _registrar(client)
    assert r.status_code == 201
    cuerpo = r.json()
    assert cuerpo["email"] == "ana@ejemplo.test" and cuerpo["rol"] == "jugador"
    assert "password" not in cuerpo and "password_hash" not in cuerpo


def test_registro_guarda_hash_argon2_no_texto_plano(client, db):
    _registrar(client)
    usuario = db.query(Usuario).one()
    assert usuario.password_hash.startswith("$argon2")
    assert DATOS["password"] not in usuario.password_hash


def test_registro_duplicado_409(client):
    assert _registrar(client).status_code == 201
    assert _registrar(client, email="ANA@ejemplo.test").status_code == 409  # sin distinguir mayúsculas


def test_registro_datos_invalidos_422(client):
    assert _registrar(client, password="corta").status_code == 422
    assert _registrar(client, email="no-es-correo").status_code == 422
    assert _registrar(client, nombre="").status_code == 422


def test_registro_no_permite_elegir_rol(client):
    r = _registrar(client, rol="administrador")  # campo extra: se ignora
    assert r.status_code == 201 and r.json()["rol"] == "jugador"


def test_login_correcto_y_perfil(client):
    _registrar(client)
    token = _token(client)
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json()["email"] == DATOS["email"]


def test_login_password_incorrecta_y_usuario_inexistente_responden_igual(client):
    _registrar(client)
    a = client.post("/auth/login", json={"email": DATOS["email"], "password": "otra-clave-xyz"})
    b = client.post("/auth/login", json={"email": "nadie@ejemplo.test", "password": "otra-clave-xyz"})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_bloqueo_tras_intentos_fallidos_aunque_luego_use_la_clave_correcta(client):
    _registrar(client)
    maximo = get_settings().login_max_intentos
    for _ in range(maximo):
        assert client.post("/auth/login", json={"email": DATOS["email"], "password": "mala-clave-000"}).status_code == 401
    r = client.post("/auth/login", json={"email": DATOS["email"], "password": DATOS["password"]})
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) > 0


def test_sin_token_401(client):
    assert client.get("/auth/me").status_code == 401


def test_token_invalido_401(client):
    r = client.get("/auth/me", headers={"Authorization": "Bearer basura"})
    assert r.status_code == 401


def test_sesion_expirada_401(client):
    _registrar(client)
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {_token(client)}"}).json()
    s = get_settings()
    vencido = jwt.encode(
        {"sub": me["id"], "rol": "jugador", "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
        s.jwt_secret, algorithm=s.jwt_algorithm,
    )
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {vencido}"})
    assert r.status_code == 401 and r.json()["detail"] == "Sesión expirada"


def test_token_firmado_con_otra_clave_401(client):
    _registrar(client)
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {_token(client)}"}).json()
    falso = jwt.encode(
        {"sub": me["id"], "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        "x" * 40, algorithm="HS256",
    )
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {falso}"}).status_code == 401


def test_usuario_sin_permisos_403_y_administrador_200(client, db):
    ruta = "/_prueba/solo-admin"
    if not any(getattr(r, "path", None) == ruta for r in app.routes):
        app.add_api_route(ruta, lambda: {"ok": True}, dependencies=[Depends(require_rol("administrador"))])

    _registrar(client)  # jugador
    h_jugador = {"Authorization": f"Bearer {_token(client)}"}
    assert client.get(ruta, headers=h_jugador).status_code == 403

    db.add(Usuario(email="admin@ejemplo.test", nombre="Admin", password_hash=hash_password("clave-admin-123"), rol="administrador"))
    db.commit()
    h_admin = {"Authorization": f"Bearer {_token(client, 'admin@ejemplo.test', 'clave-admin-123')}"}
    assert client.get(ruta, headers=h_admin).status_code == 200


def test_el_rol_se_toma_de_la_bd_no_del_token(client, db):
    """Aunque el token diga 'administrador', manda el rol guardado en la BD."""
    ruta = "/_prueba/solo-admin"
    if not any(getattr(r, "path", None) == ruta for r in app.routes):
        app.add_api_route(ruta, lambda: {"ok": True}, dependencies=[Depends(require_rol("administrador"))])
    _registrar(client)
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {_token(client)}"}).json()
    s = get_settings()
    mentiroso = jwt.encode(
        {"sub": me["id"], "rol": "administrador", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        s.jwt_secret, algorithm=s.jwt_algorithm,
    )
    assert client.get(ruta, headers={"Authorization": f"Bearer {mentiroso}"}).status_code == 403
