"""
合规评分核心：基础规则分 + 整改调整台账。

评分口径（唯一事实来源）：
1. 基础分按许可、超范围记录、人员持证、待核实线索等客观数据计算；
2. 已核实违规的扣分/恢复/复发/撤销一律以 ScoreAdjustment 台账为准，
   台账记录按 idempotency_key 幂等，同一违规被多条线索引用只产生一次调整；
3. 已被台账扣分覆盖的违规结论不再参与基础分的"已核实违规条数"统计，
   避免同一问题重复扣分。
"""
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .compliance_utils import (
    check_institution_license_valid,
    count_unlicensed_practitioners,
)
from .models import (
    ActualProcedureRecord,
    ClueViolationLink,
    ComplianceGrade,
    ClueStatus,
    ClueType,
    InspectionFrequency,
    Institution,
    ScoreAdjustment,
    ScoreAdjustmentStatus,
    ScoreAdjustmentType,
    ScoreItem,
    ViolationClue,
)
from . import schemas

MAX_SCORES: Dict[ScoreItem, float] = {
    ScoreItem.LICENSE_VALID: 15.0,
    ScoreItem.LICENSE_COMPLETE: 10.0,
    ScoreItem.NO_OVER_RANGE: 20.0,
    ScoreItem.ALL_STAFF_LICENSED: 20.0,
    ScoreItem.NO_QUICK_TRAINING: 15.0,
    ScoreItem.NO_FALSE_ADVERTISEMENT: 10.0,
    ScoreItem.NO_VERIFIED_VIOLATION: 10.0,
}

# 立案时每项违规结论的默认扣分（在对应评分类别内生效）。
DEFAULT_VIOLATION_POINTS = 5.0
# 逾期升级的额外扣分（计入"无已核实违规记录"项）。
OVERDUE_PENALTY_POINTS = 3.0

GRADE_RANGES = [
    (ComplianceGrade.EXCELLENT, 90.0, 100.0),
    (ComplianceGrade.GOOD, 75.0, 89.99),
    (ComplianceGrade.FAIR, 60.0, 74.99),
    (ComplianceGrade.POOR, 0.0, 59.99),
]

GRADE_FREQUENCY = {
    ComplianceGrade.EXCELLENT: InspectionFrequency.EXTENDED,
    ComplianceGrade.GOOD: InspectionFrequency.ANNUAL,
    ComplianceGrade.FAIR: InspectionFrequency.BIANNUAL,
    ComplianceGrade.POOR: InspectionFrequency.QUARTERLY,
}

GRADE_NAMES = {
    ComplianceGrade.EXCELLENT: "优秀",
    ComplianceGrade.GOOD: "良好",
    ComplianceGrade.FAIR: "合格",
    ComplianceGrade.POOR: "不合格",
}

# 线索类型到评分类别的映射。
CLUE_TYPE_SCORE_ITEM = {
    ClueType.QUICK_TRAINING: ScoreItem.NO_QUICK_TRAINING,
    ClueType.FALSE_ADVERTISEMENT: ScoreItem.NO_FALSE_ADVERTISEMENT,
}


def get_grade(total_score: float) -> ComplianceGrade:
    for grade, min_score, max_score in GRADE_RANGES:
        if min_score <= total_score <= max_score:
            return grade
    return ComplianceGrade.POOR


def get_inspection_frequency(grade: ComplianceGrade) -> InspectionFrequency:
    return GRADE_FREQUENCY.get(grade, InspectionFrequency.ANNUAL)


def natural_violation_key(clue: ViolationClue) -> str:
    """同一违规问题的自然键：类型+机构+人员+项目，用于多线索引用去重。"""
    return (
        f"{clue.clue_type.value}|inst={clue.institution_id}"
        f"|prac={clue.practitioner_id or 0}|proc={clue.procedure_id or 0}"
    )


def active_adjustments(
    db: Session,
    institution_id: int,
    as_of: Optional[datetime] = None,
) -> List[ScoreAdjustment]:
    """
    返回某时点有效的调整记录（用于历史评分追溯）：
    生效时间不晚于该时点，且当时尚未被撤销。不传 as_of 时按当前状态返回。
    """
    query = db.query(ScoreAdjustment).filter(
        ScoreAdjustment.institution_id == institution_id,
    )
    if as_of is None:
        query = query.filter(ScoreAdjustment.status == ScoreAdjustmentStatus.EFFECTIVE)
    else:
        query = query.filter(
            ScoreAdjustment.effective_at <= as_of,
            (ScoreAdjustment.revoked_at.is_(None))
            | (ScoreAdjustment.revoked_at > as_of),
        )
    return query.order_by(ScoreAdjustment.effective_at.asc()).all()


def _managed_clue_ids(db: Session, institution_id: int) -> Set[int]:
    """已通过线索引用纳入整改案件的线索，其违规由调整台账处理，基础分不再统计。"""
    rows = db.query(ClueViolationLink.clue_id).filter(
        ClueViolationLink.institution_id == institution_id
    ).all()
    return {row[0] for row in rows}


def _verified_natural_keys(
    db: Session, institution_id: int, clue_type: Optional[ClueType] = None,
    exclude_clue_ids: Optional[Set[int]] = None,
) -> Set[str]:
    query = db.query(ViolationClue).filter(
        ViolationClue.institution_id == institution_id,
        ViolationClue.status == ClueStatus.VERIFIED,
    )
    if clue_type is not None:
        query = query.filter(ViolationClue.clue_type == clue_type)
    if exclude_clue_ids:
        query = query.filter(~ViolationClue.id.in_(exclude_clue_ids))
    return {natural_violation_key(c) for c in query.all()}


def _has_pending_clue(db: Session, institution_id: int, clue_type: ClueType) -> bool:
    return db.query(ViolationClue).filter(
        ViolationClue.institution_id == institution_id,
        ViolationClue.clue_type == clue_type,
        ViolationClue.status.in_([ClueStatus.PENDING, ClueStatus.ASSIGNED]),
    ).count() > 0


def _covered_keys(adjustments: List[ScoreAdjustment]) -> Set[str]:
    return {
        a.violation_key for a in adjustments
        if a.violation_key
        and a.adjustment_type in (
            ScoreAdjustmentType.DEDUCTION,
            ScoreAdjustmentType.RECURRENCE_DEDUCTION,
        )
    }


def _base_scores(
    db: Session,
    institution_id: int,
    adjustments: List[ScoreAdjustment],
) -> Tuple[Dict[ScoreItem, float], List[schemas.ScoreDeduction]]:
    """客观数据基础分；已被调整台账覆盖的违规结论不在这里重复统计。"""
    scores = {item: max_score for item, max_score in MAX_SCORES.items()}
    deductions: List[schemas.ScoreDeduction] = []

    def add_deduction(item: ScoreItem, actual: float, reason: str):
        scores[item] = actual
        deductions.append(schemas.ScoreDeduction(
            item=item,
            max_score=MAX_SCORES[item],
            actual_score=actual,
            deduction=round(MAX_SCORES[item] - actual, 2),
            reason=reason,
        ))

    has_valid_license, license_msg, valid_license = check_institution_license_valid(
        db, institution_id
    )
    if not has_valid_license:
        add_deduction(ScoreItem.LICENSE_VALID, 0.0,
                      license_msg or "无有效医疗机构执业许可证或许可证已过期")

    if valid_license and valid_license.is_valid and not valid_license.approved_surgeries:
        add_deduction(
            ScoreItem.LICENSE_COMPLETE,
            MAX_SCORES[ScoreItem.LICENSE_COMPLETE] * 0.5,
            "许可证未明确核准诊疗科目范围",
        )

    over_range_records = db.query(ActualProcedureRecord).filter(
        ActualProcedureRecord.institution_id == institution_id,
        ActualProcedureRecord.is_over_range == True,  # noqa: E712
    ).all()
    if over_range_records:
        over_range_count = len(over_range_records)
        total_records = db.query(ActualProcedureRecord).filter(
            ActualProcedureRecord.institution_id == institution_id
        ).count()
        over_range_ratio = over_range_count / total_records if total_records else 1.0
        if over_range_ratio >= 0.5:
            factor = 0.0
        elif over_range_ratio >= 0.3:
            factor = 0.3
        elif over_range_ratio >= 0.1:
            factor = 0.6
        else:
            factor = 1.0
        if factor < 1.0:
            add_deduction(
                ScoreItem.NO_OVER_RANGE,
                MAX_SCORES[ScoreItem.NO_OVER_RANGE] * factor,
                f"存在 {over_range_count} 条超范围执业记录，占比 {over_range_ratio:.1%}",
            )

    unlicensed_count, total_practitioners = count_unlicensed_practitioners(db, institution_id)
    if total_practitioners > 0:
        unlicensed_ratio = unlicensed_count / total_practitioners
        if unlicensed_ratio >= 0.5:
            factor = 0.0
        elif unlicensed_ratio >= 0.3:
            factor = 0.3
        elif unlicensed_ratio > 0:
            factor = 0.6
        else:
            factor = 1.0
        if factor < 1.0:
            add_deduction(
                ScoreItem.ALL_STAFF_LICENSED,
                MAX_SCORES[ScoreItem.ALL_STAFF_LICENSED] * factor,
                f"{unlicensed_count}/{total_practitioners} 名从业人员无有效执业证书，"
                f"占比 {unlicensed_ratio:.1%}",
            )

    covered = _covered_keys(adjustments)
    managed_clue_ids = _managed_clue_ids(db, institution_id)

    # 速成班 / 虚假宣传：已被整改台账覆盖的违规结论只在台账中扣分。
    for clue_type, item in CLUE_TYPE_SCORE_ITEM.items():
        item_adjustments = [a for a in adjustments if a.score_item == item]
        item_covered = {
            a.violation_key for a in item_adjustments if a.violation_key
        } & covered
        verified_keys = _verified_natural_keys(
            db, institution_id, clue_type, exclude_clue_ids=managed_clue_ids
        )
        uncovered_verified = verified_keys - item_covered

        if uncovered_verified:
            add_deduction(item, 0.0,
                          f"已核实 {len(uncovered_verified)} 条{item.value.replace('无', '').replace('线索', '')}线索（未建整改案件）")
        elif _has_pending_clue(db, institution_id, clue_type):
            add_deduction(
                item,
                MAX_SCORES[item] * 0.5,
                f"存在待核实的{clue_type.value}线索",
            )

    # 综合违规项：只统计未被任何台账调整覆盖、也未纳入整改案件的已核实违规。
    all_verified_keys = _verified_natural_keys(
        db, institution_id, exclude_clue_ids=managed_clue_ids
    )
    residual_keys = all_verified_keys - covered
    residual_count = len(residual_keys)
    if residual_count >= 3:
        add_deduction(ScoreItem.NO_VERIFIED_VIOLATION, 0.0,
                      f"存在 {residual_count} 条未纳入整改案件的已核实违规记录")
    elif residual_count == 2:
        add_deduction(
            ScoreItem.NO_VERIFIED_VIOLATION,
            MAX_SCORES[ScoreItem.NO_VERIFIED_VIOLATION] * 0.3,
            f"存在 {residual_count} 条未纳入整改案件的已核实违规记录",
        )
    elif residual_count == 1:
        add_deduction(
            ScoreItem.NO_VERIFIED_VIOLATION,
            MAX_SCORES[ScoreItem.NO_VERIFIED_VIOLATION] * 0.6,
            "存在 1 条未纳入整改案件的已核实违规记录",
        )

    return scores, deductions


def calculate_compliance_score(
    institution_id: int,
    db: Session,
    scoring_period: Optional[str] = None,
    as_of: Optional[datetime] = None,
) -> schemas.ScoreCalculationResult:
    institution = db.query(Institution).filter(Institution.id == institution_id).first()
    if not institution:
        raise HTTPException(status_code=404, detail=f"机构 {institution_id} 不存在")

    adjustments = active_adjustments(db, institution_id, as_of)
    scores, deductions = _base_scores(db, institution_id, adjustments)

    # 台账调整按评分类别汇总后叠加（含恢复加分），结果限制在 [0, 满分]。
    item_points: Dict[ScoreItem, float] = {}
    for adj in adjustments:
        item_points[adj.score_item] = item_points.get(adj.score_item, 0.0) + adj.points
        deductions.append(schemas.ScoreDeduction(
            item=adj.score_item,
            max_score=MAX_SCORES[adj.score_item],
            actual_score=0.0,  # 最终分项分在下面统一回填
            deduction=round(-adj.points, 2),
            reason=f"[{adj.adjustment_type.value}] {adj.reason}",
        ))

    for item, delta in item_points.items():
        scores[item] = max(0.0, min(MAX_SCORES[item], scores[item] + delta))

    for d in deductions:
        d.actual_score = scores[d.item]

    total_score = round(sum(scores.values()), 2)
    grade = get_grade(total_score)
    return schemas.ScoreCalculationResult(
        institution_id=institution_id,
        institution_name=institution.name,
        total_score=total_score,
        grade=grade,
        inspection_frequency=get_inspection_frequency(grade),
        deductions=deductions,
    )
