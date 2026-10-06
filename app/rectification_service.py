"""
整改案件域服务。

案件生命周期：立案（拆分违规结论→可验收要求）→ 机构提交版本化证据
→ 复核人逐项 部分通过/退回/认定复发 → 全部要求通过后关闭。
评分影响只通过 ScoreAdjustment 台账生效，并按 violation_key + 复核记录幂等：
- 同一违规结论被多条线索引用（ClueViolationLink）只产生一次扣分/恢复；
- 撤销错误复核会把该复核的逐项结论与评分调整一并回滚；
- 逾期升级一次性处罚，重复执行不重复扣分。
"""
from datetime import date, datetime
from typing import Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .models import (
    CaseEventType,
    ClueStatus,
    ClueViolationLink,
    RectificationCase,
    RectificationCaseStatus,
    RectificationDecision,
    RectificationEvidence,
    RectificationEvent,
    RectificationItem,
    RectificationItemReview,
    RectificationItemStatus,
    RectificationReview,
    ReviewResult,
    ScoreAdjustment,
    ScoreAdjustmentStatus,
    ScoreAdjustmentType,
    ViolationClue,
)
from .score_service import DEFAULT_VIOLATION_POINTS, OVERDUE_PENALTY_POINTS, natural_violation_key
from . import schemas

OPEN_STATUSES = {
    RectificationCaseStatus.OPEN,
    RectificationCaseStatus.PARTIALLY_PASSED,
    RectificationCaseStatus.RETURNED,
    RectificationCaseStatus.OVERDUE,
    RectificationCaseStatus.RECURRED,
}


def _event(case: RectificationCase, event_type: CaseEventType,
           detail: str, actor: Optional[str] = None) -> RectificationEvent:
    return RectificationEvent(
        case=case, event_type=event_type, detail=detail, actor=actor
    )


def _get_clue(db: Session, clue_id: int) -> ViolationClue:
    clue = db.query(ViolationClue).filter(ViolationClue.id == clue_id).first()
    if not clue:
        raise HTTPException(status_code=404, detail="线索不存在")
    if clue.status != ClueStatus.VERIFIED:
        raise HTTPException(status_code=400, detail="只能对已核实违规的线索建立整改案件")
    if not clue.institution_id:
        raise HTTPException(status_code=400, detail="线索未关联机构，无法建立整改案件")
    return clue


def _find_open_case_for_key(db: Session, institution_id: int,
                            violation_key: str) -> Optional[RectificationCase]:
    return (
        db.query(RectificationCase)
        .join(RectificationItem, RectificationItem.case_id == RectificationCase.id)
        .filter(
            RectificationCase.institution_id == institution_id,
            RectificationCase.status.in_(list(OPEN_STATUSES)),
            RectificationItem.violation_key == violation_key,
        )
        .first()
    )


def _add_adjustment_if_absent(
    db: Session,
    *,
    institution_id: int,
    score_item,
    adjustment_type: ScoreAdjustmentType,
    points: float,
    reason: str,
    idempotency_key: str,
    case_id: Optional[int] = None,
    review_id: Optional[int] = None,
    violation_key: Optional[str] = None,
) -> Optional[ScoreAdjustment]:
    existing = db.query(ScoreAdjustment).filter(
        ScoreAdjustment.idempotency_key == idempotency_key
    ).first()
    if existing:
        return None
    adj = ScoreAdjustment(
        institution_id=institution_id,
        score_item=score_item,
        adjustment_type=adjustment_type,
        points=points,
        reason=reason,
        case_id=case_id,
        review_id=review_id,
        violation_key=violation_key,
        idempotency_key=idempotency_key,
    )
    db.add(adj)
    return adj


def create_case(db: Session, payload: schemas.RectificationCaseCreate) -> RectificationCase:
    clue = _get_clue(db, payload.clue_id)

    if not payload.violations:
        raise HTTPException(status_code=400, detail="至少需要一项违规结论")
    planned_keys = []
    for v in payload.violations:
        if not v.requirements:
            raise HTTPException(status_code=400, detail="每项违规结论至少需要一条整改要求")
        planned_keys.append(v.violation_key or natural_violation_key(clue))
    if len(set(planned_keys)) != len(planned_keys):
        raise HTTPException(status_code=400, detail="同一案件内违规结论不能重复，请合并为一项")
    for vkey in planned_keys:
        if _find_open_case_for_key(db, clue.institution_id, vkey):
            raise HTTPException(
                status_code=409,
                detail=f"违规结论 {vkey} 已在其他整改案件中处理，请改用线索引用接口关联，不要重复立案",
            )

    case = RectificationCase(
        case_no=f"ZG-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{clue.id}",
        institution_id=clue.institution_id,
        title=payload.title,
        source_clue_id=clue.id,
        status=RectificationCaseStatus.OPEN,
        score_item=payload.score_item,
        due_date=payload.due_date,
        created_by=payload.created_by,
    )
    db.add(case)
    db.flush()

    for v_idx, v in enumerate(payload.violations):
        vkey = v.violation_key or natural_violation_key(clue)

        db.add(ClueViolationLink(
            clue_id=clue.id,
            case_id=case.id,
            institution_id=clue.institution_id,
            violation_key=vkey,
            violation_summary=v.violation_summary,
        ))

        for r_idx, req in enumerate(v.requirements):
            db.add(RectificationItem(
                case_id=case.id,
                violation_key=vkey,
                violation_summary=v.violation_summary,
                requirement=req.requirement,
                acceptance_criteria=req.acceptance_criteria,
                due_date=req.due_date or payload.due_date,
                sort_order=r_idx,
            ))

        # 违规扣分：按整改案件（违规处置 episode）计一次；
        # 同一案件内被多条线索引用（link 接口）不会产生第二条扣分。
        _add_adjustment_if_absent(
            db,
            institution_id=clue.institution_id,
            score_item=payload.score_item,
            adjustment_type=ScoreAdjustmentType.DEDUCTION,
            points=-DEFAULT_VIOLATION_POINTS,
            reason=f"违规结论 {v.violation_summary or vkey} 已核实，立案整改",
            idempotency_key=f"deduct|case={case.id}|vk={vkey}",
            case_id=case.id,
            violation_key=vkey,
        )

    db.add(_event(case, CaseEventType.CREATED,
                  f"立案：{payload.title}；引用线索 #{clue.id}", payload.created_by))
    db.commit()
    db.refresh(case)
    return case


def link_clue(db: Session, case_id: int,
              payload: schemas.ClueLinkRequest) -> ClueViolationLink:
    case = db.get(RectificationCase, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="整改案件不存在")
    if case.status not in OPEN_STATUSES:
        raise HTTPException(status_code=400, detail="案件已关闭，不能再引用线索")

    clue = _get_clue(db, payload.clue_id)
    if clue.institution_id != case.institution_id:
        raise HTTPException(status_code=400, detail="被引用线索不属于本机构")

    case_keys = {it.violation_key for it in case.items}
    if payload.violation_key not in case_keys:
        raise HTTPException(status_code=400, detail="该违规结论不在本案件中")

    dup = db.query(ClueViolationLink).filter(
        ClueViolationLink.clue_id == clue.id,
        ClueViolationLink.violation_key == payload.violation_key,
    ).first()
    if dup:
        raise HTTPException(status_code=400, detail="该线索已引用此违规结论，不能重复关联")

    link = ClueViolationLink(
        clue_id=clue.id,
        case_id=case.id,
        institution_id=case.institution_id,
        violation_key=payload.violation_key,
        violation_summary=payload.violation_summary,
    )
    db.add(link)
    db.add(_event(case, CaseEventType.CREATED,
                  f"线索 #{clue.id} 引用违规结论 {payload.violation_key}（不重复扣分）"))
    db.commit()
    db.refresh(link)
    return link


def submit_evidence(db: Session, item_id: int,
                    payload: schemas.EvidenceSubmit) -> RectificationEvidence:
    item = db.get(RectificationItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="整改要求不存在")
    if item.status in (RectificationItemStatus.PASSED, RectificationItemStatus.REVOKED):
        raise HTTPException(status_code=400, detail="该要求已通过，不能再提交证据")

    last_version = db.query(RectificationEvidence).filter(
        RectificationEvidence.item_id == item_id
    ).count()
    evidence = RectificationEvidence(
        item_id=item_id,
        version=last_version + 1,
        material_name=payload.material_name,
        file_ref=payload.file_ref,
        content=payload.content,
        submitted_by=payload.submitted_by,
    )
    db.add(evidence)
    db.flush()

    item.current_evidence_id = evidence.id
    item.status = RectificationItemStatus.SUBMITTED
    db.add(_event(item.case, CaseEventType.EVIDENCE_SUBMITTED,
                  f"要求#{item.id} 提交 v{evidence.version} 证据：{payload.material_name}",
                  payload.submitted_by))
    db.commit()
    db.refresh(evidence)
    return evidence


def _effective_adjustments_for_key(db: Session, institution_id: int,
                                   violation_key: str) -> List[ScoreAdjustment]:
    return db.query(ScoreAdjustment).filter(
        ScoreAdjustment.institution_id == institution_id,
        ScoreAdjustment.status == ScoreAdjustmentStatus.EFFECTIVE,
        ScoreAdjustment.violation_key == violation_key,
    ).all()


def create_review(db: Session, case_id: int,
                  payload: schemas.ReviewCreate) -> RectificationReview:
    case = db.get(RectificationCase, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="整改案件不存在")
    if case.status not in OPEN_STATUSES:
        if case.status == RectificationCaseStatus.CLOSED \
                and payload.decision == RectificationDecision.RECURRENCE:
            # 关闭后发现复发：案件自动重开，按复发流程处理。
            case.status = RectificationCaseStatus.RECURRED
            case.closed_at = None
            case.close_review_id = None
            db.add(_event(case, CaseEventType.REOPENED,
                          "关闭后发现复发，案件自动重开", payload.reviewer))
            db.flush()
        else:
            raise HTTPException(status_code=400, detail="案件已关闭，不能再复核")
    if not payload.reviewer:
        raise HTTPException(status_code=400, detail="必须指定复核人")

    if payload.decision == RectificationDecision.CLOSE:
        return _close_case(db, case, payload)

    if not payload.item_results:
        raise HTTPException(status_code=400, detail="请逐项给出复核结果")

    item_ids = [r.item_id for r in payload.item_results]
    items = db.query(RectificationItem).filter(
        RectificationItem.id.in_(item_ids),
        RectificationItem.case_id == case.id,
    ).all()
    if len(items) != len(set(item_ids)):
        raise HTTPException(status_code=400, detail="复核要求不属于本案件或存在重复项")
    item_map = {it.id: it for it in items}
    for spec in payload.item_results:
        if spec.item_id not in item_map:
            raise HTTPException(status_code=400, detail=f"要求#{spec.item_id}不属于本案件")
        if not item_map[spec.item_id].current_evidence_id:
            raise HTTPException(status_code=400,
                                detail=f"要求#{spec.item_id}尚未提交整改证据，不能复核")

    review = RectificationReview(
        case_id=case.id,
        decision=payload.decision,
        reviewer=payload.reviewer,
        comment=payload.comment,
    )
    db.add(review)
    db.flush()

    now = datetime.utcnow()
    for spec in payload.item_results:
        db.add(RectificationItemReview(
            review_id=review.id,
            item_id=spec.item_id,
            result=spec.result,
            comment=spec.comment,
            evidence_id=spec.evidence_id or item_map[spec.item_id].current_evidence_id,
        ))
        item = item_map[spec.item_id]
        if spec.result == ReviewResult.PASSED:
            item.status = RectificationItemStatus.PASSED
            item.passed_at = now
        elif spec.result == ReviewResult.RETURNED:
            item.status = RectificationItemStatus.RETURNED
        elif spec.result == ReviewResult.RECURRED:
            item.status = RectificationItemStatus.RECURRED

    db.flush()

    # 按违规结论汇总：该结论下全部要求通过才恢复分数；复发仅在此前已恢复时再扣。
    keys_in_review = {item_map[r.item_id].violation_key for r in payload.item_results}
    all_items = db.query(RectificationItem).filter(
        RectificationItem.case_id == case.id
    ).all()
    items_by_key: Dict[str, List[RectificationItem]] = {}
    for it in all_items:
        items_by_key.setdefault(it.violation_key, []).append(it)

    results_by_key: Dict[str, ReviewResult] = {}
    for spec in payload.item_results:
        results_by_key[item_map[spec.item_id].violation_key] = spec.result

    for vkey in keys_in_review:
        result = results_by_key[vkey]
        if result == ReviewResult.PASSED and all(
            it.status == RectificationItemStatus.PASSED for it in items_by_key[vkey]
        ):
            # 仅恢复当前仍在处罚中的额度，重复复核不会重复加分；
            # 复发重新扣分后再次全部通过，可恢复新一轮额度。
            net_penalty = round(-sum(
                a.points for a in _effective_adjustments_for_key(db, case.institution_id, vkey)
            ), 2)
            restore_points = min(DEFAULT_VIOLATION_POINTS, net_penalty)
            if restore_points > 0:
                _add_adjustment_if_absent(
                    db,
                    institution_id=case.institution_id,
                    score_item=case.score_item,
                    adjustment_type=ScoreAdjustmentType.RESTORE,
                    points=restore_points,
                    reason=f"违规结论 {vkey} 全部整改要求复核通过（复核 #{review.id}）",
                    idempotency_key=f"restore|case={case.id}|review={review.id}|vk={vkey}",
                    case_id=case.id,
                    review_id=review.id,
                    violation_key=vkey,
                )
        elif result == ReviewResult.RECURRED:
            key_adjustments = _effective_adjustments_for_key(
                db, case.institution_id, vkey)
            restored = sum(
                a.points for a in key_adjustments
                if a.adjustment_type == ScoreAdjustmentType.RESTORE
            )
            already_recurred = sum(
                -a.points for a in key_adjustments
                if a.adjustment_type == ScoreAdjustmentType.RECURRENCE_DEDUCTION
            )
            # 仅对"已恢复且尚未被复发抵消"的额度重新扣分，避免重复处罚。
            outstanding = round(restored - already_recurred, 2)
            if outstanding > 0:
                _add_adjustment_if_absent(
                    db,
                    institution_id=case.institution_id,
                    score_item=case.score_item,
                    adjustment_type=ScoreAdjustmentType.RECURRENCE_DEDUCTION,
                    points=-outstanding,
                    reason=f"违规结论 {vkey} 整改后复发（复核 #{review.id}）",
                    idempotency_key=f"recur|case={case.id}|review={review.id}|vk={vkey}",
                    case_id=case.id,
                    review_id=review.id,
                    violation_key=vkey,
                )

    if payload.decision == RectificationDecision.PARTIAL_PASS:
        case.status = RectificationCaseStatus.PARTIALLY_PASSED
    elif payload.decision == RectificationDecision.RETURN:
        case.status = RectificationCaseStatus.RETURNED
    elif payload.decision == RectificationDecision.RECURRENCE:
        case.status = RectificationCaseStatus.RECURRED

    db.add(_event(case, CaseEventType.REVIEW,
                  f"复核 #{review.id}：{payload.decision.value}（{payload.reviewer}）"
                  f"{payload.comment or ''}", payload.reviewer))
    db.commit()
    db.refresh(review)
    return review


def _close_case(db: Session, case: RectificationCase,
                payload: schemas.ReviewCreate) -> RectificationReview:
    items = db.query(RectificationItem).filter(
        RectificationItem.case_id == case.id
    ).all()
    not_passed = [it for it in items if it.status != RectificationItemStatus.PASSED]
    if not_passed:
        raise HTTPException(
            status_code=400,
            detail=f"仍有 {len(not_passed)} 条整改要求未通过复核，不能关闭案件",
        )

    review = RectificationReview(
        case_id=case.id,
        decision=RectificationDecision.CLOSE,
        reviewer=payload.reviewer,
        comment=payload.comment,
    )
    db.add(review)
    db.flush()

    case.status = RectificationCaseStatus.CLOSED
    case.closed_at = datetime.utcnow()
    case.close_review_id = review.id
    db.add(_event(case, CaseEventType.CLOSED,
                  f"全部 {len(items)} 条整改要求复核通过，案件关闭（{payload.reviewer}）",
                  payload.reviewer))
    db.commit()
    db.refresh(review)
    return review


def _recompute_item_status(db: Session, item: RectificationItem):
    """按最新一条有效逐项复核记录重算要求状态。"""
    latest = (
        db.query(RectificationItemReview)
        .join(RectificationReview,
              RectificationItemReview.review_id == RectificationReview.id)
        .filter(
            RectificationItemReview.item_id == item.id,
            RectificationReview.is_effective == True,  # noqa: E712
        )
        .order_by(RectificationItemReview.created_at.desc(),
                  RectificationItemReview.id.desc())
        .first()
    )
    if latest is None:
        item.status = (
            RectificationItemStatus.SUBMITTED if item.current_evidence_id
            else RectificationItemStatus.PENDING
        )
        item.passed_at = None
        return
    if latest.result == ReviewResult.PASSED:
        item.status = RectificationItemStatus.PASSED
        item.passed_at = latest.created_at
    elif latest.result == ReviewResult.RETURNED:
        item.status = RectificationItemStatus.RETURNED
        item.passed_at = None
    elif latest.result == ReviewResult.RECURRED:
        item.status = RectificationItemStatus.RECURRED
        item.passed_at = None


def revoke_review(db: Session, review_id: int,
                  payload: schemas.ReviewRevoke) -> RectificationReview:
    review = db.get(RectificationReview, review_id)
    if not review:
        raise HTTPException(status_code=404, detail="复核记录不存在")
    if not review.is_effective:
        raise HTTPException(status_code=400, detail="该复核已撤销，不能重复撤销")

    case = review.case
    now = datetime.utcnow()
    review.is_effective = False
    review.revoked_at = now
    review.revoke_reason = payload.reason
    db.flush()

    # 回滚该复核产生的评分调整（保留撤销痕迹，后续评分与历史快照均不再计入）。
    adjusted = db.query(ScoreAdjustment).filter(
        ScoreAdjustment.review_id == review.id,
        ScoreAdjustment.status == ScoreAdjustmentStatus.EFFECTIVE,
    ).all()
    for adj in adjusted:
        adj.status = ScoreAdjustmentStatus.REVOKED
        adj.revoked_at = now

    affected_item_ids = [ir.item_id for ir in review.item_reviews]
    if affected_item_ids:
        items = db.query(RectificationItem).filter(
            RectificationItem.id.in_(affected_item_ids)
        ).all()
        for item in items:
            _recompute_item_status(db, item)

    if review.decision == RectificationDecision.CLOSE and case.close_review_id == review.id:
        _recompute_case_status(db, case)
        case.closed_at = None
        case.close_review_id = None
        db.add(_event(case, CaseEventType.REOPENED,
                      f"关闭复核 #{review.id} 被撤销，案件重开：{payload.reason}",
                      payload.reviewer))
    else:
        _recompute_case_status(db, case)
        db.add(_event(case, CaseEventType.REVIEW,
                      f"复核 #{review.id} 被撤销：{payload.reason}", payload.reviewer))

    db.commit()
    db.refresh(review)
    return review


def _recompute_case_status(db: Session, case: RectificationCase):
    items = db.query(RectificationItem).filter(
        RectificationItem.case_id == case.id
    ).all()
    statuses = {it.status for it in items}
    if RectificationItemStatus.RECURRED in statuses:
        case.status = RectificationCaseStatus.RECURRED
    elif RectificationItemStatus.PASSED in statuses and not all(
        s == RectificationItemStatus.PASSED for s in statuses
    ):
        case.status = RectificationCaseStatus.PARTIALLY_PASSED
    elif statuses and all(s == RectificationItemStatus.PASSED for s in statuses):
        case.status = RectificationCaseStatus.PARTIALLY_PASSED
    else:
        case.status = RectificationCaseStatus.OPEN
    # 已升级过的案件保持逾期状态优先（待全部通过后关闭才能解除）。
    if case.escalated and not all(
        s == RectificationItemStatus.PASSED for s in statuses
    ):
        case.status = RectificationCaseStatus.OVERDUE


def escalate_overdue(db: Session, today: Optional[date] = None) -> dict:
    """逾期升级：超过期限仍未关闭的案件升级并一次性扣分，幂等可重复执行。"""
    today = today or date.today()
    escalated: List[int] = []
    penalized: List[int] = []

    cases = db.query(RectificationCase).filter(
        RectificationCase.status.in_(list({
            RectificationCaseStatus.OPEN,
            RectificationCaseStatus.PARTIALLY_PASSED,
            RectificationCaseStatus.RETURNED,
            RectificationCaseStatus.RECURRED,
        })),
        RectificationCase.due_date < today,
        RectificationCase.escalated == False,  # noqa: E712
    ).all()

    for case in cases:
        case.escalated = True
        case.escalated_at = datetime.utcnow()
        case.status = RectificationCaseStatus.OVERDUE
        escalated.append(case.id)
        db.add(_event(case, CaseEventType.OVERDUE_ESCALATED,
                      f"超过整改期限 {case.due_date} 未关闭，升级处理"))

        created = _add_adjustment_if_absent(
            db,
            institution_id=case.institution_id,
            score_item=case.score_item,
            adjustment_type=ScoreAdjustmentType.OVERDUE_PENALTY,
            points=-OVERDUE_PENALTY_POINTS,
            reason=f"整改案件 {case.case_no} 逾期未关闭，升级扣分",
            idempotency_key=f"overdue|case={case.id}",
            case_id=case.id,
        )
        if created:
            penalized.append(case.id)

    db.commit()
    return {
        "escalated_cases": escalated,
        "penalized_cases": penalized,
        "checked_at": datetime.utcnow(),
    }


def _latest_effective_item_review(db: Session, item_id: int):
    return (
        db.query(RectificationItemReview)
        .join(RectificationReview,
              RectificationItemReview.review_id == RectificationReview.id)
        .filter(
            RectificationItemReview.item_id == item_id,
            RectificationReview.is_effective == True,  # noqa: E712
        )
        .order_by(RectificationItemReview.created_at.desc(),
                  RectificationItemReview.id.desc())
        .first()
    )


def build_compliance_trace(db: Session, institution_id: int) -> dict:
    """机构详情合规追溯：当前分数 → 原违规 → 整改版本 → 复核决定 → 历次评分变化。"""
    from .models import ComplianceScore, Institution
    from .score_service import active_adjustments

    institution = db.get(Institution, institution_id)
    if not institution:
        raise HTTPException(status_code=404, detail="机构不存在")

    cases = db.query(RectificationCase).filter(
        RectificationCase.institution_id == institution_id
    ).order_by(RectificationCase.created_at.asc()).all()

    violations: Dict[str, dict] = {}

    def ensure_violation(vkey: str, summary: Optional[str] = None) -> dict:
        v = violations.setdefault(vkey, {
            "violation_key": vkey,
            "violation_summary": summary,
            "clue_ids": [],
            "case_id": None,
            "case_no": None,
            "case_status": None,
            "requirements": [],
            "adjustments": [],
        })
        if summary and not v["violation_summary"]:
            v["violation_summary"] = summary
        return v

    for case in cases:
        links = db.query(ClueViolationLink).filter(
            ClueViolationLink.case_id == case.id
        ).all()
        clue_ids_by_key: Dict[str, List[int]] = {}
        for link in links:
            clue_ids_by_key.setdefault(link.violation_key, []).append(link.clue_id)

        for item in sorted(case.items, key=lambda it: it.sort_order):
            v = ensure_violation(item.violation_key, item.violation_summary)
            v["case_id"] = case.id
            v["case_no"] = case.case_no
            v["case_status"] = case.status
            v["clue_ids"] = sorted(set(v["clue_ids"] + clue_ids_by_key.get(item.violation_key, [])))

            last_ir = _latest_effective_item_review(db, item.id)
            current = item.current_evidence
            v["requirements"].append({
                "item_id": item.id,
                "requirement": item.requirement,
                "acceptance_criteria": item.acceptance_criteria,
                "due_date": item.due_date,
                "item_status": item.status,
                "current_evidence_version": current.version if current else None,
                "current_evidence_name": current.material_name if current else None,
                "last_review_result": last_ir.result if last_ir else None,
                "last_review_comment": last_ir.comment if last_ir else None,
                "last_reviewer": last_ir.review.reviewer if last_ir else None,
                "last_review_at": last_ir.created_at if last_ir else None,
                "evidence_versions": item.evidences,
            })

    # 已核实但尚未建立整改案件的违规结论，同样可从分数追溯到原违规线索。
    verified_clues = db.query(ViolationClue).filter(
        ViolationClue.institution_id == institution_id,
        ViolationClue.status == ClueStatus.VERIFIED,
    ).all()
    for clue in verified_clues:
        vkey = natural_violation_key(clue)
        if vkey in violations:
            v = violations[vkey]
            if clue.id not in v["clue_ids"]:
                v["clue_ids"].append(clue.id)
            v["clue_ids"].sort()
        else:
            ensure_violation(vkey, clue.title)
            violations[vkey]["clue_ids"] = [clue.id]

    all_adjustments = db.query(ScoreAdjustment).filter(
        ScoreAdjustment.institution_id == institution_id
    ).order_by(ScoreAdjustment.effective_at.asc()).all()
    for adj in all_adjustments:
        if adj.violation_key and adj.violation_key in violations:
            violations[adj.violation_key]["adjustments"].append(adj)

    scores = db.query(ComplianceScore).filter(
        ComplianceScore.institution_id == institution_id
    ).order_by(ComplianceScore.scored_at.asc(), ComplianceScore.id.asc()).all()

    score_history = []
    for s in scores:
        stored_deductions = None
        if s.deduction_details:
            try:
                import json
                from .models import ScoreItem as _ScoreItem
                stored_deductions = [
                    schemas.ScoreDeduction(
                        item=_ScoreItem(d["item"]),
                        max_score=d["max_score"],
                        actual_score=d["actual_score"],
                        deduction=d["deduction"],
                        reason=d["reason"],
                    ) for d in json.loads(s.deduction_details)
                ]
            except (ValueError, KeyError):
                stored_deductions = None
        score_history.append({
            "score_id": s.id,
            "total_score": s.total_score,
            "grade": s.grade,
            "scored_at": s.scored_at,
            "scoring_period": s.scoring_period,
            "remark": s.remark,
            "effective_adjustments": active_adjustments(db, institution_id, s.scored_at),
            "deduction_list": stored_deductions,
        })

    latest_score = scores[-1] if scores else None
    return {
        "institution_id": institution_id,
        "institution_name": institution.name,
        "latest_score": latest_score,
        "violations": list(violations.values()),
        "score_history": score_history,
    }
