"""Contraseñas (argon2id) y tokens JWT."""
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.config import get_settings

_hasher = PasswordHasher()  # argon2id con parámetros por defecto de la biblioteca
# Hash de relleno: se verifica cuando el usuario no existe, para que responder
# "usuario inexistente" no tarde menos que "contraseña incorrecta".
_HASH_RELLENO = _hasher.hash("relleno-para-igualar-tiempos")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def verify_relleno(password: str) -> None:
    verify_password(_HASH_RELLENO, password)


def create_access_token(user_id: uuid.UUID, rol: str) -> str:
    s = get_settings()
    ahora = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "rol": rol,  # informativo: la autorización real consulta el rol en la BD
        "iat": ahora,
        "exp": ahora + timedelta(minutes=s.access_token_minutes),
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)


def decode_token(token: str) -> dict:
    """Lanza jwt.ExpiredSignatureError o jwt.PyJWTError si el token no es válido."""
    s = get_settings()
    return jwt.decode(
        token, s.jwt_secret, algorithms=[s.jwt_algorithm],
        options={"require": ["exp", "sub"]},
    )
