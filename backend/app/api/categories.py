from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.schemas import CategoryCreate, CategoryView
from app.core.auth import admin, principal
from app.core.security import AppError
from app.db.models import AuditEvent, Category
from app.db.session import get_db

router = APIRouter(prefix="/api/v1/categories", tags=["品类"])


@router.post("", response_model=CategoryView, status_code=201)
def create_category(
    body: CategoryCreate, request: Request, role=Depends(admin), db: Session = Depends(get_db)
):
    category = Category(code=body.code, name=body.name)
    db.add(category)
    try:
        db.flush()
        db.add(
            AuditEvent(
                principal_id=role,
                action="category.create",
                resource_id=category.id,
                correlation_id=request.state.correlation_id,
                details_json={},
            )
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "CATEGORY_CONFLICT", "产品品类编码已存在") from exc
    return CategoryView(
        id=category.id, code=category.code, name=category.name, knowledge_ready=False
    )


@router.get("", response_model=list[CategoryView])
def list_categories(role=Depends(principal), db: Session = Depends(get_db)):
    return [
        CategoryView(
            id=c.id, code=c.code, name=c.name, knowledge_ready=c.active_snapshot_id is not None
        )
        for c in db.scalars(select(Category).order_by(Category.code))
    ]
