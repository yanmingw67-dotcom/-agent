from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import principal
from app.db.session import get_db
from app.retrieval.schemas import ToolEnvelope, ToolRequest
from app.retrieval.tools import ReadOnlyTools, bind_context

router = APIRouter(prefix="/api/v1", tags=["统一只读检索"])


@router.post("/retrieval/{category_id}", response_model=ToolEnvelope)
def retrieve(
    category_id: str,
    request: ToolRequest,
    snapshot_id: str | None = None,
    role=Depends(principal),
    db: Session = Depends(get_db),
):
    context = bind_context(db, category_id, role, snapshot_id)
    return ReadOnlyTools(db, context).execute(request)
