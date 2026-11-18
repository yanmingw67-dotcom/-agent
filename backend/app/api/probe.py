from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.core.auth import principal
from app.core.config import get_settings
from app.core.security import AppError
from app.retrieval.probe import KnowledgeProbe, probe_db
from app.retrieval.probe_schemas import ProbeRequest, ProbeResponse

router = APIRouter(prefix="/api/v1", tags=["只读Knowledge Probe"])


def retrieval_channel():
    if get_settings().service_mode != "retrieval":
        raise AppError(503, "PROBE_RETRIEVAL_SERVICE_REQUIRED", "Probe请使用独立只读检索服务")


@router.post("/probes", response_model=ProbeResponse, dependencies=[Depends(retrieval_channel)])
def probe(
    body: ProbeRequest,
    request: Request,
    response: Response,
    role=Depends(principal),
    db: Session = Depends(probe_db),
):
    if request.query_params:
        raise AppError(422, "PROBE_SCOPE_OVERRIDE_FORBIDDEN", "Probe不接受快照、版本或其他查询参数")
    response.headers["Cache-Control"] = "no-store"
    return KnowledgeProbe(db, body.category_id, role).retrieve(body.requirement_text, body.limit)
