from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import date

from ..database import get_db
from ..models import (
    RectificationCase, RectificationCaseStatus, ScoreAdjustment,
)
from .. import schemas
from .. import rectification_service

router = APIRouter()


@router.post("/cases", response_model=schemas.RectificationCaseOut, status_code=201)
def create_rectification_case(
    payload: schemas.RectificationCaseCreate,
    db: Session = Depends(get_db),
):
    """对已核实违规线索建立整改案件，把每项违规结论拆成可验收要求与期限。"""
    return rectification_service.create_case(db, payload)


@router.get("/cases", response_model=List[schemas.RectificationCaseOut])
def list_rectification_cases(
    institution_id: Optional[int] = None,
    status: Optional[RectificationCaseStatus] = None,
    escalated: Optional[bool] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    query = db.query(RectificationCase)
    if institution_id is not None:
        query = query.filter(RectificationCase.institution_id == institution_id)
    if status is not None:
        query = query.filter(RectificationCase.status == status)
    if escalated is not None:
        query = query.filter(RectificationCase.escalated == escalated)
    return query.order_by(RectificationCase.created_at.desc()) \
        .offset(skip).limit(limit).all()


@router.get("/cases/{case_id}", response_model=schemas.RectificationCaseOut)
def get_rectification_case(case_id: int, db: Session = Depends(get_db)):
    case = db.get(RectificationCase, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="整改案件不存在")
    return case


@router.post("/cases/{case_id}/link-clue", status_code=201)
def link_clue_to_case(
    case_id: int,
    payload: schemas.ClueLinkRequest,
    db: Session = Depends(get_db),
):
    """把另一条线索引用到案件内已有违规结论，共用整改流程且不重复扣分。"""
    link = rectification_service.link_clue(db, case_id, payload)
    return {
        "id": link.id,
        "case_id": link.case_id,
        "clue_id": link.clue_id,
        "violation_key": link.violation_key,
        "message": "关联成功，该线索不重复扣分",
    }


@router.post("/items/{item_id}/evidence",
             response_model=schemas.RectificationEvidenceOut, status_code=201)
def submit_evidence(
    item_id: int,
    payload: schemas.EvidenceSubmit,
    db: Session = Depends(get_db),
):
    """机构为某条整改要求提交证据，自动生成递增版本号，历史版本保留。"""
    return rectification_service.submit_evidence(db, item_id, payload)


@router.post("/cases/{case_id}/reviews",
             response_model=schemas.RectificationReviewOut, status_code=201)
def create_review(
    case_id: int,
    payload: schemas.ReviewCreate,
    db: Session = Depends(get_db),
):
    """复核人逐项给出 通过/退回/复发；全部通过后可直接关闭案件。"""
    return rectification_service.create_review(db, case_id, payload)


@router.post("/reviews/{review_id}/revoke",
             response_model=schemas.RectificationReviewOut)
def revoke_review(
    review_id: int,
    payload: schemas.ReviewRevoke,
    db: Session = Depends(get_db),
):
    """撤销错误复核：逐项验收结论回滚，关联评分调整同步作废，案件必要时重开。"""
    return rectification_service.revoke_review(db, review_id, payload)


@router.post("/escalate-overdue", response_model=schemas.OverdueEscalationResult)
def escalate_overdue(
    today: Optional[date] = Query(None, description="考核日期，默认今天"),
    db: Session = Depends(get_db),
):
    """逾期未关闭案件统一升级并一次性扣分；重复执行不会重复处罚。"""
    return rectification_service.escalate_overdue(db, today)


@router.get("/cases/{case_id}/adjustments",
            response_model=List[schemas.ScoreAdjustmentOut])
def list_case_adjustments(case_id: int, db: Session = Depends(get_db)):
    case = db.get(RectificationCase, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="整改案件不存在")
    return db.query(ScoreAdjustment).filter(
        ScoreAdjustment.case_id == case_id
    ).order_by(ScoreAdjustment.effective_at.asc()).all()
