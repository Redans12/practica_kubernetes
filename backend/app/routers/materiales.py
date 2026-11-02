"""Material rentable: lectura para usuarios autenticados; escritura solo administrador."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user, require_rol
from app.models import Material
from app.schemas import MaterialIn, MaterialOut, MaterialUpdate, Pagina

router = APIRouter(prefix="/materiales", tags=["materiales"])
solo_admin = Depends(require_rol("administrador"))


def _o_404(db: Session, material_id: uuid.UUID) -> Material:
    m = db.get(Material, material_id)
    if m is None:
        raise HTTPException(404, "Material no encontrado")
    return m


@router.get("", response_model=Pagina[MaterialOut], dependencies=[Depends(get_current_user)])
def listar(q: str | None = Query(None, max_length=80), incluir_inactivos: bool = False,
           pagina: int = Query(1, ge=1), tamano: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)):
    filtros = []
    if q:
        filtros.append(Material.nombre.icontains(q, autoescape=True))
    if not incluir_inactivos:
        filtros.append(Material.activo.is_(True))
    total = db.scalar(select(func.count()).select_from(Material).where(*filtros))
    filas = db.scalars(select(Material).where(*filtros).order_by(Material.nombre)
                       .offset((pagina - 1) * tamano).limit(tamano)).all()
    return Pagina(items=filas, total=total, pagina=pagina, tamano=tamano)


@router.post("", response_model=MaterialOut, status_code=201, dependencies=[solo_admin])
def crear(datos: MaterialIn, db: Session = Depends(get_db)):
    m = Material(**datos.model_dump())
    db.add(m)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Ya existe un material con ese nombre") from None
    return m


@router.get("/{material_id}", response_model=MaterialOut, dependencies=[Depends(get_current_user)])
def detalle(material_id: uuid.UUID, db: Session = Depends(get_db)):
    return _o_404(db, material_id)


@router.patch("/{material_id}", response_model=MaterialOut, dependencies=[solo_admin])
def actualizar(material_id: uuid.UUID, datos: MaterialUpdate, db: Session = Depends(get_db)):
    m = _o_404(db, material_id)
    for campo, valor in datos.model_dump(exclude_unset=True).items():
        if valor is None:
            raise HTTPException(422, f"'{campo}' no puede ser nulo")
        setattr(m, campo, valor)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Ya existe un material con ese nombre") from None
    return m


@router.delete("/{material_id}", status_code=204, dependencies=[solo_admin])
def dar_de_baja(material_id: uuid.UUID, db: Session = Depends(get_db)):
    """Baja lógica: las reservas existentes conservan su historial."""
    _o_404(db, material_id).activo = False
    db.commit()
