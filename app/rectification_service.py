"""整改案件领域服务。

职责：
- 由已核实违规线索立案，把违规结论拆成可验收、有期限的整改要求；
- 管理版本化证据、不可变但可撤销的复核决定；
- 所有评分影响以独立 ScoreAdjustment 记录落账，撤销/逾期冲正一律用对冲记录，永不删改；
- 同一问题被多条线索引用时，通过“线索关联案件”结构共享同一案件与同一份扣分。
"""
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .models import (
    ViolationClue, ClueStatus, CluePriority, ClueType,
    ScoreItem, ScoreAdjustment, ScoreAdjustmentType,
    RectificationCase, RectificationCaseClue, RectificationRequirement,
    RectificationEvidence, RectificationReview,
    RectificationCaseStatus, RequirementStatus, ReviewDecision,
)

# 各评分维度满分（与 compliance_score 路由保持一致）
ITEM_MAX_SCORES = {
    ScoreItem.LICENSE_VALID: 15.0,
    ScoreItem.LICENSE_COMPLETE: 10.0,
    ScoreItem.NO_OVER_RANGE: 20.0,
    ScoreItem.ALL_STAFF_LICENSED: 20.0,
    ScoreItem.NO_QUICK_TRAINING: 15.0,
    ScoreItem.NO_FALSE_ADVERTISEMENT: 10.0,
    ScoreItem.NO_VERIFIED_VIOLATION: 10.0,
}

# 线索类型对应的专项评分维度（在“无已核实违规”之外的专项扣分）
CLUE_SCORE_ITEM = {
    ClueType.QUICK_TRAINING: ScoreItem.NO_QUICK_TRAINING,
    ClueType.FALSE_ADVERTISEMENT: ScoreItem.NO_FALSE_ADVERTISEMENT,
}

# 单条整改要求逾期的固定扣分
OVERDUE_PENALTY = 2.0

# “无已核实违规”维度的边际扣分：第1个问题 -4，之后每个 -3
FIRST_VIOLATION_PENALTY = 4.0
LATER_VIOLATION_PENALTY = 3.0


# ---------------------------------------------------------------------------
# 台账工具
# ---------------------------------------------------------------------------

def _add_adjustment(
    db: Session,
    institution_id: int,
    adjustment_type: ScoreAdjustmentType,
    score_item: ScoreItem,
    score_delta: float,
    reason: str,
    case_id: Optional[int] = None,
    requirement_id: Optional[int] = None,
    clue_id: Optional[int] = None,
    review_id: Optional[int] = None,
    related_adjustment_id: Optional[int] = None,
    problem_signature: Optional[str] = None,
    commit: bool = False,
) -> ScoreAdjustment:
    adj = ScoreAdjustment(
        institution_id=institution_id,
        adjustment_type=adjustment_type,
        score_item=score_item,
        score_delta=round(score_delta, 2),
        problem_signature=problem_signature,
        case_id=case_id,
        requirement_id=requirement_id,
        clue_id=clue_id,
        review_id=review_id,
        related_adjustment_id=related_adjustment_id,
        reason=reason,
    )
    db.add(adj)
    db.flush()
    if commit:
        db.commit()
        db.refresh(adj)
    return adj


def _reverse_adjustment(
    db: Session,
    original: ScoreAdjustment,
    reverse_type: ScoreAdjustmentType,
    reason: str,
    review_id: Optional[int] = None,
) -> ScoreAdjustment:
    """以一条金额相反的台账记录对冲原记录。"""
    return _add_adjustment(
        db,
        institution_id=original.institution_id,
        adjustment_type=reverse_type,
        score_item=original.score_item,
        score_delta=-original.score_delta,
        reason=reason,
        case_id=original.case_id,
        requirement_id=original.requirement_id,
        clue_id=original.clue_id,
        review_id=review_id,
        related_adjustment_id=original.id,
        problem_signature=original.problem_signature,
    )


def get_active_adjustment_map(db: Session, institution_id: int) -> Dict[ScoreItem, float]:
    """机构各维度台账净额（扣分为负、恢复为正；对冲记录天然计入）。"""
    from sqlalchemy import func
    agg = db.query(
        ScoreAdjustment.score_item,
        func.sum(ScoreAdjustment.score_delta),
    ).filter(
        ScoreAdjustment.institution_id == institution_id
    ).group_by(ScoreAdjustment.score_item).all()
    return {item: float(delta or 0.0) for item, delta in agg}


# ---------------------------------------------------------------------------
# 案件/要求状态工具
# ---------------------------------------------------------------------------

def _case_no(db: Session) -> str:
    today = date.today().strftime("%Y%m%d")
    prefix = f"ZG-{today}-"
    count = db.query(RectificationCase).filter(
        RectificationCase.case_no.like(f"{prefix}%")
    ).count()
    return f"{prefix}{count + 1:04d}"


def _get_case(db: Session, case_id: int) -> RectificationCase:
    case = db.query(RectificationCase).filter(RectificationCase.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="整改案件不存在")
    return case


def _get_requirement(db: Session, requirement_id: int) -> RectificationRequirement:
    req = db.query(RectificationRequirement).filter(
        RectificationRequirement.id == requirement_id
    ).first()
    if not req:
        raise HTTPException(status_code=404, detail="整改要求不存在")
    return req


def _all_requirements_approved(case: RectificationCase) -> bool:
    return (
        len(case.requirements) > 0
        and all(r.status == RequirementStatus.APPROVED for r in case.requirements)
    )


def _requirement_open_status(req: RectificationRequirement) -> RequirementStatus:
    """撤销复核后，依据是否已提交过证据回退要求状态。"""
    if req.evidence_versions:
        return RequirementStatus.SUBMITTED
    return RequirementStatus.PENDING


def _refresh_case_status(
    db: Session, case: RectificationCase,
    reopen_type: ScoreAdjustmentType = ScoreAdjustmentType.RECURRENCE_PENALTY
) -> None:
    """根据全部要求的有效复核结论重算案件状态；只有全部通过才允许关闭。"""
    if _all_requirements_approved(case):
        if case.status != RectificationCaseStatus.CLOSED:
            case.status = RectificationCaseStatus.CLOSED
            case.closed_at = datetime.utcnow()
            _restore_case_level_penalties(db, case)
    else:
        if case.status == RectificationCaseStatus.CLOSED:
            # 已关闭案件出现未通过的有效要求 → 复发重开（或撤销通过导致重开）
            case.status = RectificationCaseStatus.REOPENED
            case.closed_at = None
            # 旧的整体关闭摘要随重开自动失效
            for summary in db.query(RectificationReview).filter(
                RectificationReview.case_id == case.id,
                RectificationReview.requirement_id.is_(None),
                RectificationReview.is_active == True,  # noqa: E712
            ).all():
                summary.is_active = False
                summary.revoke_reason = "案件重新打开，关闭摘要自动失效"
            _reapply_case_level_penalties(db, case, reopen_type)
        elif any(r.status == RequirementStatus.RECURRED for r in case.requirements):
            case.status = RectificationCaseStatus.REOPENED


def _case_level_net(db: Session, case_id: int) -> float:
    """案件级（不挂具体要求）台账净额，当前只有“无已核实违规”边际扣分。"""
    from sqlalchemy import func
    net = db.query(func.sum(ScoreAdjustment.score_delta)).filter(
        ScoreAdjustment.case_id == case_id,
        ScoreAdjustment.requirement_id.is_(None),
    ).scalar()
    return float(net or 0.0)


def _restore_case_level_penalties(db: Session, case: RectificationCase) -> None:
    """案件关闭：若案件级净额仍为负，用一条恢复记录补到零（绝不重复加分）。"""
    net = _case_level_net(db, case.id)
    if net < -0.001:
        _add_adjustment(
            db,
            institution_id=case.institution_id,
            adjustment_type=ScoreAdjustmentType.RESTORE,
            score_item=ScoreItem.NO_VERIFIED_VIOLATION,
            score_delta=-net,
            reason=f"案件 {case.case_no} 全部整改要求复核通过并关闭，恢复案件级扣分",
            case_id=case.id,
            problem_signature=case.problem_signature,
        )


def _reapply_case_level_penalties(
    db: Session, case: RectificationCase, adj_type: ScoreAdjustmentType
) -> None:
    """案件重开（复发/撤销关闭）：仅当案件级净额已被恢复到非负时，按最近一次恢复金额重新扣分。"""
    net = _case_level_net(db, case.id)
    if net >= -0.001:
        last_restore = db.query(ScoreAdjustment).filter(
            ScoreAdjustment.case_id == case.id,
            ScoreAdjustment.requirement_id.is_(None),
            ScoreAdjustment.score_delta > 0,
        ).order_by(ScoreAdjustment.id.desc()).first()
        amount = last_restore.score_delta if last_restore else 0.0
        if amount > 0:
            _add_adjustment(
                db,
                institution_id=case.institution_id,
                adjustment_type=adj_type,
                score_item=ScoreItem.NO_VERIFIED_VIOLATION,
                score_delta=-amount,
                reason=f"案件 {case.case_no} 重新打开，恢复原案件级违规扣分",
                case_id=case.id,
                problem_signature=case.problem_signature,
            )


# ---------------------------------------------------------------------------
# 立案
# ---------------------------------------------------------------------------

def create_case(db: Session, clue_id: int, title: Optional[str],
                priority: CluePriority, requirements_data: List) -> RectificationCase:
    clue = db.query(ViolationClue).filter(ViolationClue.id == clue_id).first()
    if not clue:
        raise HTTPException(status_code=404, detail="违规线索不存在")
    if clue.status != ClueStatus.VERIFIED:
        raise HTTPException(status_code=400, detail="只能对已核实违规的线索建立整改案件")
    if not clue.institution_id:
        raise HTTPException(status_code=400, detail="线索未关联机构，无法建立整改案件")
    if not requirements_data:
        raise HTTPException(status_code=400, detail="至少需要一条可验收的整改要求")

    existing_link = db.query(RectificationCaseClue).filter(
        RectificationCaseClue.clue_id == clue_id
    ).first()
    if existing_link:
        raise HTTPException(status_code=400, detail="该线索已关联整改案件，不能重复立案")

    score_item = CLUE_SCORE_ITEM.get(clue.clue_type, ScoreItem.NO_VERIFIED_VIOLATION)
    signature = f"inst:{clue.institution_id}|clue:{clue_id}|item:{score_item.name}"

    case = RectificationCase(
        case_no=_case_no(db),
        title=title or f"整改案件：{clue.title}",
        clue_id=clue_id,
        institution_id=clue.institution_id,
        status=RectificationCaseStatus.OPEN,
        priority=priority,
        score_item=score_item,
        violation_summary=clue.conclusion or clue.description,
        problem_signature=signature,
        penalty_score=0.0,
    )
    db.add(case)
    db.flush()

    db.add(RectificationCaseClue(case_id=case.id, clue_id=clue_id,
                                 link_remark="立案线索"))
    db.flush()

    # 要求级专项扣分：按要求均摊专项维度分值（无专项维度的违规类型为 0）
    item_penalty_total = ITEM_MAX_SCORES[score_item] if score_item in (
        ScoreItem.NO_QUICK_TRAINING, ScoreItem.NO_FALSE_ADVERTISEMENT
    ) else 0.0
    share = round(item_penalty_total / len(requirements_data), 2)

    total_req_penalty = 0.0
    req_penalties = []
    for idx, rq in enumerate(requirements_data):
        penalty = share
        if idx == len(requirements_data) - 1:
            # 末条承担舍入差，保证总额一致
            penalty = round(item_penalty_total - total_req_penalty, 2)
        total_req_penalty = round(total_req_penalty + penalty, 2)
        req = RectificationRequirement(
            case_id=case.id,
            content=rq.content,
            acceptance_criteria=rq.acceptance_criteria,
            due_date=rq.due_date,
            status=RequirementStatus.PENDING,
            penalty_score=penalty,
        )
        db.add(req)
        req_penalties.append((req, penalty))
    db.flush()

    # 专项维度扣分按要求份额逐条落账，每条要求通过/复发只影响自己的份额
    for req, penalty in req_penalties:
        if penalty > 0:
            _add_adjustment(
                db,
                institution_id=case.institution_id,
                adjustment_type=ScoreAdjustmentType.VIOLATION_PENALTY,
                score_item=score_item,
                score_delta=-penalty,
                reason=f"线索#{clue_id} 已核实违规（{clue.clue_type.value}），立案 {case.case_no}，要求#{req.id} 对应扣分",
                case_id=case.id,
                requirement_id=req.id,
                clue_id=clue_id,
                problem_signature=signature,
            )
            case.penalty_score = round(case.penalty_score + penalty, 2)

    # “无已核实违规”边际扣分（案件级，关闭时恢复）
    prior_cases = db.query(RectificationCase).filter(
        RectificationCase.institution_id == case.institution_id,
        RectificationCase.id < case.id,
    ).count()
    marginal = FIRST_VIOLATION_PENALTY if prior_cases == 0 else LATER_VIOLATION_PENALTY
    _add_adjustment(
        db,
        institution_id=case.institution_id,
        adjustment_type=ScoreAdjustmentType.VIOLATION_PENALTY,
        score_item=ScoreItem.NO_VERIFIED_VIOLATION,
        score_delta=-marginal,
        reason=f"案件 {case.case_no} 立案，第 {prior_cases + 1} 个已核实违规问题",
        case_id=case.id,
        clue_id=clue_id,
        problem_signature=signature,
    )
    case.penalty_score = round(case.penalty_score + marginal, 2)

    db.commit()
    db.refresh(case)
    return case


def link_clue(db: Session, case_id: int, clue_id: int,
              link_remark: Optional[str]) -> RectificationCaseClue:
    """把另一条引用同一问题的线索关联到既有案件：不产生新的扣分。"""
    case = _get_case(db, case_id)
    clue = db.query(ViolationClue).filter(ViolationClue.id == clue_id).first()
    if not clue:
        raise HTTPException(status_code=404, detail="违规线索不存在")
    if clue.institution_id != case.institution_id:
        raise HTTPException(status_code=400, detail="线索所属机构与案件不一致，不能关联")
    if clue.id == case.clue_id:
        raise HTTPException(status_code=400, detail="该线索是案件的立案线索，无需重复关联")

    existing = db.query(RectificationCaseClue).filter(
        RectificationCaseClue.clue_id == clue_id
    ).first()
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"该线索已关联案件 #{existing.case_id}，同一问题不可重复扣分"
        )

    link = RectificationCaseClue(case_id=case_id, clue_id=clue_id,
                                 link_remark=link_remark or "引用同一问题")
    db.add(link)
    db.commit()
    db.refresh(link)
    return link


# ---------------------------------------------------------------------------
# 证据（版本化、不可变）
# ---------------------------------------------------------------------------

def submit_evidence(db: Session, requirement_id: int, data) -> RectificationEvidence:
    req = _get_requirement(db, requirement_id)
    if req.status == RequirementStatus.APPROVED:
        raise HTTPException(status_code=400, detail="该要求已复核通过，无需再提交证据")

    last = db.query(RectificationEvidence).filter(
        RectificationEvidence.requirement_id == requirement_id
    ).order_by(RectificationEvidence.version_no.desc()).first()
    version_no = (last.version_no + 1) if last else 1

    evidence = RectificationEvidence(
        requirement_id=requirement_id,
        version_no=version_no,
        file_name=data.file_name,
        file_url=data.file_url,
        file_hash=data.file_hash,
        content_text=data.content_text,
        submit_remark=data.submit_remark,
        submitted_by=data.submitted_by,
    )
    db.add(evidence)
    req.status = RequirementStatus.SUBMITTED
    db.commit()
    db.refresh(evidence)
    return evidence


# ---------------------------------------------------------------------------
# 复核
# ---------------------------------------------------------------------------

def _deactivate_active_reviews(db: Session, requirement_id: int) -> None:
    active = db.query(RectificationReview).filter(
        RectificationReview.requirement_id == requirement_id,
        RectificationReview.is_active == True,  # noqa: E712
    ).all()
    for r in active:
        r.is_active = False


def _requirement_item_net(db: Session, req: RectificationRequirement, item: ScoreItem) -> float:
    """该要求在某评分维度上的台账净额。"""
    from sqlalchemy import func
    net = db.query(func.sum(ScoreAdjustment.score_delta)).filter(
        ScoreAdjustment.requirement_id == req.id,
        ScoreAdjustment.score_item == item,
    ).scalar()
    return float(net or 0.0)


def _requirement_overdue_net(db: Session, req: RectificationRequirement) -> float:
    from sqlalchemy import func
    net = db.query(func.sum(ScoreAdjustment.score_delta)).filter(
        ScoreAdjustment.requirement_id == req.id,
        ScoreAdjustment.adjustment_type.in_([
            ScoreAdjustmentType.OVERDUE_PENALTY,
            ScoreAdjustmentType.OVERDUE_VOID,
        ]),
    ).scalar()
    return float(net or 0.0)


def _apply_requirement_decision(
    db: Session, case: RectificationCase, req: RectificationRequirement,
    decision: ReviewDecision, reviewer: str, comment: Optional[str],
    evidence_version: Optional[int],
) -> RectificationReview:
    """把单条要求的复核决定落库，并维护台账。"""
    review = RectificationReview(
        case_id=case.id,
        requirement_id=req.id,
        decision=decision,
        reviewer=reviewer,
        comment=comment,
        evidence_version=evidence_version,
        is_active=True,
    )
    db.add(review)
    db.flush()
    _deactivate_active_reviews(db, req.id)
    # _deactivate 会把刚插入的记录也置为失效，重新激活当前记录
    review.is_active = True

    req.effective_decision = decision
    req.approved_at = None
    req.approved_by = None

    if decision == ReviewDecision.APPROVED:
        req.status = RequirementStatus.APPROVED
        req.approved_at = datetime.utcnow()
        req.approved_by = reviewer
        # 仅恢复该要求尚未恢复的专项分值份额（净额为负才补，杜绝重复加分）
        if req.penalty_score > 0:
            net = _requirement_item_net(db, req, case.score_item)
            if net < -0.001:
                restore_amount = min(req.penalty_score, -net)
                _add_adjustment(
                    db,
                    institution_id=case.institution_id,
                    adjustment_type=ScoreAdjustmentType.RESTORE,
                    score_item=case.score_item,
                    score_delta=restore_amount,
                    reason=f"案件 {case.case_no} 要求#{req.id} 复核通过，恢复专项扣分",
                    case_id=case.id,
                    requirement_id=req.id,
                    review_id=review.id,
                    problem_signature=case.problem_signature,
                )
        # 若该要求仍挂着未冲回的逾期扣分，通过时一并冲回
        overdue_net = _requirement_overdue_net(db, req)
        if overdue_net < -0.001:
            _add_adjustment(
                db,
                institution_id=case.institution_id,
                adjustment_type=ScoreAdjustmentType.OVERDUE_VOID,
                score_item=ScoreItem.NO_VERIFIED_VIOLATION,
                score_delta=-overdue_net,
                reason=f"案件 {case.case_no} 要求#{req.id} 复核通过，冲回逾期扣分",
                case_id=case.id,
                requirement_id=req.id,
                review_id=review.id,
                problem_signature=case.problem_signature,
            )
    elif decision == ReviewDecision.PARTIAL_APPROVED:
        req.status = RequirementStatus.PARTIAL_APPROVED
    elif decision == ReviewDecision.REJECTED:
        req.status = RequirementStatus.REJECTED
    elif decision == ReviewDecision.RECURRED:
        req.status = RequirementStatus.RECURRED
        # 仅当该要求份额此前确已恢复（净额非负）时，复发才重新扣分；
        # 原本就未恢复的违规不再重复扣
        if req.penalty_score > 0:
            net = _requirement_item_net(db, req, case.score_item)
            if net >= -0.001:
                _add_adjustment(
                    db,
                    institution_id=case.institution_id,
                    adjustment_type=ScoreAdjustmentType.RECURRENCE_PENALTY,
                    score_item=case.score_item,
                    score_delta=-req.penalty_score,
                    reason=f"案件 {case.case_no} 要求#{req.id} 被认定问题复发，重新扣分",
                    case_id=case.id,
                    requirement_id=req.id,
                    review_id=review.id,
                    problem_signature=case.problem_signature,
                )

    return review


def submit_review(db: Session, case_id: int, data) -> Tuple[RectificationCase, List[RectificationReview]]:
    case = _get_case(db, case_id)
    decision = data.decision
    reviews: List[RectificationReview] = []

    if decision == ReviewDecision.APPROVED:
        # 整体通过：每条要求都必须有证据材料；对尚未通过的要求逐条形成通过决定
        blocked = [r for r in case.requirements if not r.evidence_versions]
        if blocked:
            raise HTTPException(
                status_code=400,
                detail=f"有 {len(blocked)} 条要求尚未提交整改证据，不能整体通过"
            )
        for req in case.requirements:
            if req.status != RequirementStatus.APPROVED:
                reviews.append(_apply_requirement_decision(
                    db, case, req, ReviewDecision.APPROVED,
                    data.reviewer, data.comment or "复核通过", data.evidence_version
                ))
        summary = RectificationReview(
            case_id=case.id, requirement_id=None, decision=ReviewDecision.APPROVED,
            reviewer=data.reviewer,
            comment=data.comment or "全部整改要求复核通过，案件关闭",
            evidence_version=data.evidence_version, is_active=True,
        )
        db.add(summary)
        reviews.append(summary)

    elif decision == ReviewDecision.REJECTED:
        targets = [r for r in case.requirements
                   if r.status in (RequirementStatus.SUBMITTED, RequirementStatus.PARTIAL_APPROVED)]
        if not targets:
            raise HTTPException(status_code=400, detail="没有待复核的整改材料，无法退回")
        for req in targets:
            reviews.append(_apply_requirement_decision(
                db, case, req, ReviewDecision.REJECTED,
                data.reviewer, data.comment, data.evidence_version
            ))

    elif decision == ReviewDecision.RECURRED:
        if data.item_decisions:
            targets = []
            for item in data.item_decisions:
                req = _get_requirement(db, item.requirement_id)
                if req.case_id != case.id:
                    raise HTTPException(status_code=400, detail=f"要求#{item.requirement_id} 不属于该案件")
                if item.decision != ReviewDecision.RECURRED:
                    raise HTTPException(status_code=400, detail="复发复核中每条要求的决定必须为“认定复发”")
                targets.append((req, item.comment or data.comment))
        else:
            # 案件级复发：所有有效要求均认定复发
            targets = [(r, data.comment) for r in case.requirements
                       if r.status != RequirementStatus.RECURRED]
        if not targets:
            raise HTTPException(status_code=400, detail="没有可认定复发的整改要求")
        for req, comment in targets:
            reviews.append(_apply_requirement_decision(
                db, case, req, ReviewDecision.RECURRED,
                data.reviewer, comment, data.evidence_version
            ))

    elif decision == ReviewDecision.PARTIAL_APPROVED:
        if not data.item_decisions:
            raise HTTPException(
                status_code=400,
                detail="部分通过必须提供每条要求的逐项复核决定（item_decisions）"
            )
        decisions = {it.decision for it in data.item_decisions}
        if ReviewDecision.APPROVED not in decisions:
            raise HTTPException(status_code=400, detail="部分通过至少应包含一条“通过”的要求")
        # 若本次逐项决定使全部要求都达到通过，状态刷新阶段会自动关闭案件
        for item in data.item_decisions:
            req = _get_requirement(db, item.requirement_id)
            if req.case_id != case.id:
                raise HTTPException(status_code=400, detail=f"要求#{item.requirement_id} 不属于该案件")
            if item.decision not in (
                ReviewDecision.APPROVED, ReviewDecision.PARTIAL_APPROVED,
                ReviewDecision.REJECTED, ReviewDecision.RECURRED,
            ):
                raise HTTPException(status_code=400, detail="不支持的逐项复核决定")
            reviews.append(_apply_requirement_decision(
                db, case, req, item.decision,
                data.reviewer, item.comment or data.comment, data.evidence_version
            ))

    db.flush()
    _refresh_case_status(db, case)
    db.commit()
    for r in reviews:
        db.refresh(r)
    db.refresh(case)
    return case, reviews


def revoke_review(db: Session, review_id: int, revoked_by: str, reason: str) -> RectificationReview:
    """撤销错误复核决定：决定本身保留为失效记录，评分影响以对冲台账回滚。"""
    review = db.query(RectificationReview).filter(RectificationReview.id == review_id).first()
    if not review:
        raise HTTPException(status_code=404, detail="复核记录不存在")
    if not review.is_active:
        raise HTTPException(status_code=400, detail="该复核决定已被撤销或已被新决定替代")

    case = _get_case(db, review.case_id)

    if review.requirement_id is None:
        raise HTTPException(
            status_code=400,
            detail="案件关闭摘要是整体通过时自动生成的，不能单独撤销；"
                   "请撤销具体整改要求的复核通过记录"
        )

    if review.requirement_id is not None:
        req = _get_requirement(db, review.requirement_id)

        if review.decision == ReviewDecision.APPROVED:
            # 撤销通过：仅当该要求份额确已恢复（净额非负）时扣回；案件若已关闭随之重开
            if req.penalty_score > 0:
                net = _requirement_item_net(db, req, case.score_item)
                if net >= -0.001:
                    _add_adjustment(
                        db,
                        institution_id=case.institution_id,
                        adjustment_type=ScoreAdjustmentType.REVOKE_APPROVAL_PENALTY,
                        score_item=case.score_item,
                        score_delta=-req.penalty_score,
                        reason=f"复核决定 #{review.id} 被撤销（{reason}），扣回要求#{req.id} 已恢复分值",
                        case_id=case.id,
                        requirement_id=req.id,
                        review_id=review.id,
                        problem_signature=case.problem_signature,
                    )
            req.status = _requirement_open_status(req)
            req.effective_decision = None
            req.approved_at = None
            req.approved_by = None

        elif review.decision == ReviewDecision.RECURRED:
            # 撤销复发认定：返还复发扣分
            if req.penalty_score > 0:
                recurrence = db.query(ScoreAdjustment).filter(
                    ScoreAdjustment.review_id == review.id,
                    ScoreAdjustment.adjustment_type == ScoreAdjustmentType.RECURRENCE_PENALTY,
                ).first()
                if recurrence:
                    _reverse_adjustment(
                        db, recurrence, ScoreAdjustmentType.REVOKE_RECUR_RESTORE,
                        f"复发认定 #{review.id} 被撤销（{reason}），返还复发扣分",
                        review_id=review.id,
                    )
            # 恢复该要求在本决定之前最近一条未被撤销的复核结论（通常为“通过”）
            prior = db.query(RectificationReview).filter(
                RectificationReview.requirement_id == req.id,
                RectificationReview.id != review.id,
                RectificationReview.is_active == False,  # noqa: E712
                RectificationReview.revoked_at.is_(None),
            ).order_by(RectificationReview.reviewed_at.desc()).first()
            # 注意：旧有效决定在复发提交时已被置 is_active=False 且未撤销，正是上面的目标
            if prior and prior.decision == ReviewDecision.APPROVED:
                req.status = RequirementStatus.APPROVED
                req.effective_decision = ReviewDecision.APPROVED
                req.approved_at = prior.reviewed_at
                req.approved_by = prior.reviewer
                prior.is_active = True  # 恢复其有效地位
            elif prior and prior.decision == ReviewDecision.PARTIAL_APPROVED:
                req.status = RequirementStatus.PARTIAL_APPROVED
                req.effective_decision = ReviewDecision.PARTIAL_APPROVED
                prior.is_active = True
            elif prior and prior.decision == ReviewDecision.REJECTED:
                req.status = RequirementStatus.REJECTED
                req.effective_decision = ReviewDecision.REJECTED
                prior.is_active = True
            else:
                req.status = _requirement_open_status(req)
                req.effective_decision = None

        elif review.decision in (ReviewDecision.REJECTED, ReviewDecision.PARTIAL_APPROVED):
            req.status = _requirement_open_status(req)
            req.effective_decision = None

    review.is_active = False
    review.revoked_at = datetime.utcnow()
    review.revoked_by = revoked_by
    review.revoke_reason = reason

    db.flush()
    reopen_type = (
        ScoreAdjustmentType.REVOKE_APPROVAL_PENALTY
        if review.decision == ReviewDecision.APPROVED
        else ScoreAdjustmentType.RECURRENCE_PENALTY
    )
    _refresh_case_status(db, case, reopen_type)
    db.commit()
    db.refresh(review)
    return review


# ---------------------------------------------------------------------------
# 逾期升级
# ---------------------------------------------------------------------------

def scan_overdue(db: Session, today: Optional[date] = None) -> dict:
    """扫描逾期要求：每条要求只升级/扣分一次；要求通过后逾期分在复核通过时冲回。"""
    today = today or date.today()
    overdue_reqs = db.query(RectificationRequirement).join(RectificationCase).filter(
        RectificationRequirement.due_date < today,
        RectificationRequirement.status != RequirementStatus.APPROVED,
        RectificationRequirement.overdue_escalated == False,  # noqa: E712
        RectificationCase.status.in_([
            RectificationCaseStatus.OPEN, RectificationCaseStatus.REOPENED
        ]),
    ).all()

    escalated_case_ids = set()
    for req in overdue_reqs:
        case = req.case
        req.overdue_escalated = True
        _add_adjustment(
            db,
            institution_id=case.institution_id,
            adjustment_type=ScoreAdjustmentType.OVERDUE_PENALTY,
            score_item=ScoreItem.NO_VERIFIED_VIOLATION,
            score_delta=-OVERDUE_PENALTY,
            reason=f"案件 {case.case_no} 要求#{req.id} 超过期限 {req.due_date} 未通过复核，逾期升级",
            case_id=case.id,
            requirement_id=req.id,
            problem_signature=case.problem_signature,
        )
        if not case.escalated:
            case.escalated = True
            case.escalated_at = datetime.utcnow()
        if case.priority != CluePriority.HIGH:
            case.priority = CluePriority.HIGH
        escalated_case_ids.add(case.id)

    db.commit()
    return {
        "scanned_cases": db.query(RectificationCase).count(),
        "overdue_requirements": len(overdue_reqs),
        "escalated_cases": len(escalated_case_ids),
        "escalated_case_ids": sorted(escalated_case_ids),
    }
