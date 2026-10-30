"""Dependencias de autenticación y autorización (se verifican siempre en el servidor)."""
import uuid

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Usuario
from app.security import decode_token

_bearer = HTTPBearer(auto_error=False)
_NO_AUTH = {"WWW-Authenticate": "Bearer"}


def get_current_user(
    cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> Usuario:
    if cred is None:
        raise HTTPException(401, "No autenticado", headers=_NO_AUTH)
    try:
        payload = decode_token(cred.credentials)
        user_id = uuid.UUID(payload["sub"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Sesión expirada", headers=_NO_AUTH)
    except (jwt.PyJWTError, ValueError, KeyError):
        raise HTTPException(401, "Token inválido", headers=_NO_AUTH)

    usuario = db.get(Usuario, user_id)
    if usuario is None or not usuario.activo:
        raise HTTPException(401, "Usuario no válido", headers=_NO_AUTH)
    return usuario


def require_rol(*roles: str):
    """Exige que el usuario tenga alguno de los roles. El rol sale de la BD, no del token."""
    def _checker(usuario: Usuario = Depends(get_current_user)) -> Usuario:
        if usuario.rol not in roles:
            raise HTTPException(403, "No tienes permiso para esta operación")
        return usuario
    return _checker
