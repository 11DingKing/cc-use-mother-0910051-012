from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import date
import json

from ..database import get_db
from ..models import (
    Institution, ViolationClue,
    RectificationCase, RectificationCaseClue, RectificationRequirement,
    RectificationEvidence, RectificationReview, ScoreAdjustment,
    RectificationCaseStatus, RequirementStatus,
    ClueStatus, ScoreItem, ComplianceScore,
)
from .. import schemas
from .. import rectification_service as svc

router = APIRouter()


def _build_requirement_detail(req: RectificationRequirement) -> schemas.RectificationRequirementDetail:
    evidence = sorted(req.evidence_versions, key=lambda e: e.version_no, reverse=True)
    reviews = sorted(req.reviews, key=lambda r: r.reviewed_at, reverse=True)
    return schemas.RectificationRequirementDetail(
        id=req.id,
        case_id=req.case_id,
        content=req.content,
        acceptance_criteria=req.acceptance_criteria,
        due_date=req.due_date,
        status=req.status,
        effective_decision=req.effective_decision,
        penalty_score=req.penalty_score,
        approved_at=req.approved_at,
        approved_by=req.approved_by,
        created_at=req.created_at,
        evidence_versions=evidence,
        latest_evidence_version=evidence[0].version_no if evidence else None,
        reviews=reviews,
    )


def _build_case_detail(case: RectificationCase) -> schemas.RectificationCaseDetail:
    req_details = [_build_requirement_detail(r) for r in case.requirements]
    reviews = sorted(case.reviews, key=lambda r: r.reviewed_at, reverse=True)
    return schemas.RectificationCaseDetail(
        id=case.id,
        case_no=case.case_no,
        title=case.title,
        clue_id=case.clue_id,
        institution_id=case.institution_id,
        status=case.status,
        priority=case.priority,
        score_item=case.score_item,
        violation_summary=case.violation_summary,
        problem_signature=case.problem_signature,
        penalty_score=case.penalty_score,
        escalated=case.escalated,
        escalated_at=case.escalated_at,
        closed_at=case.closed_at,
        created_at=case.created_at,
        updated_at=case.updated_at,
        clue=case.clue,
        institution=case.institution,
        requirements=req_details,
        reviews=reviews,
        clue_links=case.clue_links,
        linked_clue_count=len(case.clue_links),
    )


def _build_case_summary(db: Session, case: RectificationCase) -> schemas.RectificationCaseSummary:
    today = date.today()
    reqs = case.requirements
    overdue = sum(
        1 for r in reqs
        if r.due_date < today and r.status != RequirementStatus.APPROVED
    )
    return schemas.RectificationCaseSummary(
        case_id=case.id,
        case_no=case.case_no,
        title=case.title,
        institution_id=case.institution_id,
        status=case.status,
        priority=case.priority,
        score_item=case.score_item,
        penalty_score=case.penalty_score,
        escalated=case.escalated,
        total_requirements=len(reqs),
        approved_requirements=sum(1 for r in reqs if r.status == RequirementStatus.APPROVED),
        pending_requirements=sum(1 for r in reqs if r.status != RequirementStatus.APPROVED),
        recurred_requirements=sum(1 for r in reqs if r.status == RequirementStatus.RECURRED),
        overdue_count=overdue,
        created_at=case.created_at,
    )


# ---------------------------------------------------------------------------
# 整改案件
# ---------------------------------------------------------------------------

@router.post("/cases", response_model=schemas.RectificationCaseDetail, status_code=201)
def create_rectification_case(
    data: schemas.RectificationCaseCreate,
    db: Session = Depends(get_db)
):
    case = svc.create_case(
        db, clue_id=data.clue_id, title=data.title,
        priority=data.priority, requirements_data=data.requirements
    )
    return _build_case_detail(case)


@router.get("/cases", response_model=List[schemas.RectificationCaseSummary])
def list_cases(
    institution_id: Optional[int] = None,
    status: Optional[RectificationCaseStatus] = None,
    clue_id: Optional[int] = None,
    escalated: Optional[bool] = None,
    overdue_only: bool = Query(False, description="只看存在逾期要求的案件"),
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    query = db.query(RectificationCase)
    if institution_id:
        query = query.filter(RectificationCase.institution_id == institution_id)
    if status:
        query = query.filter(RectificationCase.status == status)
    if escalated is not None:
        query = query.filter(RectificationCase.escalated == escalated)
    if clue_id is not None:
        query = query.join(RectificationCaseClue).filter(
            RectificationCaseClue.clue_id == clue_id
        )
    cases = query.order_by(RectificationCase.created_at.desc()).offset(skip).limit(limit).all()
    summaries = [_build_case_summary(db, c) for c in cases]
    if overdue_only:
        summaries = [s for s in summaries if s.overdue_count > 0]
    return summaries


@router.get("/cases/{case_id}", response_model=schemas.RectificationCaseDetail)
def get_case(case_id: int, db: Session = Depends(get_db)):
    case = svc._get_case(db, case_id)
    return _build_case_detail(case)


@router.post("/cases/{case_id}/link-clue", response_model=schemas.RectificationCaseClue)
def link_clue_to_case(
    case_id: int,
    data: schemas.RectificationClueLink,
    db: Session = Depends(get_db)
):
    link = svc.link_clue(db, case_id, data.clue_id, data.link_remark)
    db.refresh(link)
    return link


# ---------------------------------------------------------------------------
# 证据提交（版本化）
# ---------------------------------------------------------------------------

@router.post(
    "/requirements/{requirement_id}/evidence",
    response_model=schemas.RectificationEvidence,
    status_code=201
)
def submit_evidence(
    requirement_id: int,
    data: schemas.RectificationEvidenceCreate,
    db: Session = Depends(get_db)
):
    return svc.submit_evidence(db, requirement_id, data)


@router.get(
    "/requirements/{requirement_id}/evidence",
    response_model=List[schemas.RectificationEvidence]
)
def list_evidence(requirement_id: int, db: Session = Depends(get_db)):
    req = svc._get_requirement(db, requirement_id)
    return sorted(req.evidence_versions, key=lambda e: e.version_no, reverse=True)


# ---------------------------------------------------------------------------
# 复核决定
# ---------------------------------------------------------------------------

@router.post("/cases/{case_id}/reviews", response_model=schemas.RectificationCaseDetail)
def submit_review(
    case_id: int,
    data: schemas.RectificationReviewCreate,
    db: Session = Depends(get_db)
):
    case, _reviews = svc.submit_review(db, case_id, data)
    return _build_case_detail(case)


@router.get("/cases/{case_id}/reviews", response_model=List[schemas.RectificationReview])
def list_case_reviews(case_id: int, db: Session = Depends(get_db)):
    case = svc._get_case(db, case_id)
    return sorted(case.reviews, key=lambda r: r.reviewed_at, reverse=True)


@router.post("/reviews/{review_id}/revoke", response_model=schemas.RectificationReview)
def revoke_review(
    review_id: int,
    data: schemas.RectificationReviewRevoke,
    db: Session = Depends(get_db)
):
    return svc.revoke_review(db, review_id, data.revoked_by, data.reason)


# ---------------------------------------------------------------------------
# 逾期升级
# ---------------------------------------------------------------------------

@router.post("/overdue-scan", response_model=schemas.EscalationResult)
def scan_overdue(db: Session = Depends(get_db)):
    return svc.scan_overdue(db)


# ---------------------------------------------------------------------------
# 评分调整台账
# ---------------------------------------------------------------------------

@router.get("/adjustments", response_model=List[schemas.ScoreAdjustment])
def list_adjustments(
    institution_id: int = Query(..., description="机构ID"),
    case_id: Optional[int] = None,
    score_item: Optional[ScoreItem] = None,
    db: Session = Depends(get_db)
):
    query = db.query(ScoreAdjustment).filter(ScoreAdjustment.institution_id == institution_id)
    if case_id:
        query = query.filter(ScoreAdjustment.case_id == case_id)
    if score_item:
        query = query.filter(ScoreAdjustment.score_item == score_item)
    return query.order_by(ScoreAdjustment.created_at.asc(), ScoreAdjustment.id.asc()).all()


# ---------------------------------------------------------------------------
# 机构合规追溯：当前分数 → 原违规 → 整改版本 → 复核决定 → 历次评分变化
# ---------------------------------------------------------------------------

def _parse_deduction_details(raw: Optional[str]):
    if not raw:
        return []
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return []


@router.get(
    "/traceability/institution/{institution_id}",
    response_model=schemas.InstitutionComplianceTraceability
)
def institution_traceability(institution_id: int, db: Session = Depends(get_db)):
    institution = db.query(Institution).filter(Institution.id == institution_id).first()
    if not institution:
        raise HTTPException(status_code=404, detail="机构不存在")

    scores = db.query(ComplianceScore).filter(
        ComplianceScore.institution_id == institution_id
    ).order_by(ComplianceScore.scored_at.desc()).all()

    score_details: List[schemas.ComplianceScoreDetail] = []
    for s in scores:
        raw = _parse_deduction_details(s.deduction_details)
        deduction_list = []
        for d in raw:
            try:
                deduction_list.append(schemas.ScoreDeduction(
                    item=ScoreItem(d["item"]),
                    max_score=d.get("max_score", 0.0),
                    actual_score=d.get("actual_score", 0.0),
                    deduction=d.get("deduction", 0.0),
                    reason=d.get("reason", ""),
                ))
            except (KeyError, ValueError):
                continue
        score_details.append(schemas.ComplianceScoreDetail(
            **{c.name: getattr(s, c.name) for c in s.__table__.columns},
            institution=institution,
            deduction_list=deduction_list,
        ))

    adjustments = db.query(ScoreAdjustment).filter(
        ScoreAdjustment.institution_id == institution_id
    ).order_by(ScoreAdjustment.created_at.asc()).all()

    cases = db.query(RectificationCase).filter(
        RectificationCase.institution_id == institution_id
    ).order_by(RectificationCase.created_at.desc()).all()
    case_details = [_build_case_detail(c) for c in cases]

    verified_clues = db.query(ViolationClue).filter(
        ViolationClue.institution_id == institution_id,
        ViolationClue.status == ClueStatus.VERIFIED,
    ).order_by(ViolationClue.verified_at.desc()).all()

    return schemas.InstitutionComplianceTraceability(
        institution_id=institution_id,
        institution_name=institution.name,
        latest_score=score_details[0] if score_details else None,
        score_history=score_details,
        adjustments=adjustments,
        rectification_cases=case_details,
        verified_clues=verified_clues,
    )
