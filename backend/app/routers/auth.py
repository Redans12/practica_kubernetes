"""Registro, inicio de sesión y perfil."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.models import Usuario
from app.schemas import LoginIn, RegistroIn, TokenOut, UsuarioOut
from app.security import create_access_token, hash_password, verify_password, verify_relleno

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/registro", response_model=UsuarioOut, status_code=201)
def registro(datos: RegistroIn, db: Session = Depends(get_db)):
    """Alta de jugadores. El rol administrador solo se asigna por la semilla o por otro administrador."""
    usuario = Usuario(
        email=datos.email.lower(),
        nombre=datos.nombre.strip(),
        password_hash=hash_password(datos.password),
        rol="jugador",
    )
    db.add(usuario)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "El correo ya está registrado")
    return usuario


@router.post("/login", response_model=TokenOut)
def login(datos: LoginIn, db: Session = Depends(get_db)):
    s = get_settings()
    ahora = datetime.now(timezone.utc)

    # FOR UPDATE: los intentos fallidos se cuentan bien aunque lleguen a réplicas distintas
    usuario = db.execute(
        select(Usuario).where(Usuario.email == datos.email.lower()).with_for_update()
    ).scalar_one_or_none()

    if usuario is None or not usuario.activo:
        verify_relleno(datos.password)
        raise HTTPException(401, "Credenciales inválidas")

    if usuario.login_bloqueado_hasta and usuario.login_bloqueado_hasta > ahora:
        espera = int((usuario.login_bloqueado_hasta - ahora).total_seconds()) + 1
        db.rollback()
        raise HTTPException(
            429, "Demasiados intentos fallidos. Intenta más tarde",
            headers={"Retry-After": str(espera)},
        )

    if not verify_password(usuario.password_hash, datos.password):
        usuario.intentos_fallidos += 1
        if usuario.intentos_fallidos >= s.login_max_intentos:
            usuario.login_bloqueado_hasta = ahora + timedelta(minutes=s.login_bloqueo_minutos)
            usuario.intentos_fallidos = 0
        db.commit()
        raise HTTPException(401, "Credenciales inválidas")

    usuario.intentos_fallidos = 0
    usuario.login_bloqueado_hasta = None
    db.commit()
    return TokenOut(
        access_token=create_access_token(usuario.id, usuario.rol),
        expires_in=s.access_token_minutes * 60,
    )


@router.get("/me", response_model=UsuarioOut)
def me(usuario: Usuario = Depends(get_current_user)):
    return usuario
