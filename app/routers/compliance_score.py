from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from datetime import date, datetime, timedelta
import json

from ..database import get_db
from ..models import (
    Institution, ComplianceScore, ComplianceGrade, ScoreItem,
    InspectionFrequency, SupervisionPlan, PlanStatus, CluePriority,
)
from .. import schemas
from .. import score_service
from ..score_service import (
    MAX_SCORES, GRADE_RANGES, GRADE_NAMES,
    get_grade, get_inspection_frequency,
    calculate_compliance_score,
)

router = APIRouter()

GRADE_FREQUENCY = score_service.GRADE_FREQUENCY

FREQUENCY_MONTHS = {
    InspectionFrequency.QUARTERLY: 3,
    InspectionFrequency.BIANNUAL: 6,
    InspectionFrequency.ANNUAL: 12,
    InspectionFrequency.EXTENDED: 24,
}


def save_compliance_score(
    result: schemas.ScoreCalculationResult, db: Session, remark: Optional[str] = None
) -> ComplianceScore:
    scores_map = {}
    for d in result.deductions:
        scores_map[d.item] = d.actual_score

    deduction_details = json.dumps([
        {
            "item": d.item.value,
            "max_score": d.max_score,
            "actual_score": d.actual_score,
            "deduction": d.deduction,
            "reason": d.reason
        } for d in result.deductions
    ], ensure_ascii=False) if result.deductions else None

    now = datetime.utcnow()
    score_record = ComplianceScore(
        institution_id=result.institution_id,
        total_score=result.total_score,
        grade=result.grade,
        license_valid_score=scores_map.get(ScoreItem.LICENSE_VALID, MAX_SCORES[ScoreItem.LICENSE_VALID]),
        license_complete_score=scores_map.get(ScoreItem.LICENSE_COMPLETE, MAX_SCORES[ScoreItem.LICENSE_COMPLETE]),
        no_over_range_score=scores_map.get(ScoreItem.NO_OVER_RANGE, MAX_SCORES[ScoreItem.NO_OVER_RANGE]),
        all_staff_licensed_score=scores_map.get(ScoreItem.ALL_STAFF_LICENSED, MAX_SCORES[ScoreItem.ALL_STAFF_LICENSED]),
        no_quick_training_score=scores_map.get(ScoreItem.NO_QUICK_TRAINING, MAX_SCORES[ScoreItem.NO_QUICK_TRAINING]),
        no_false_advertisement_score=scores_map.get(ScoreItem.NO_FALSE_ADVERTISEMENT, MAX_SCORES[ScoreItem.NO_FALSE_ADVERTISEMENT]),
        no_verified_violation_score=scores_map.get(ScoreItem.NO_VERIFIED_VIOLATION, MAX_SCORES[ScoreItem.NO_VERIFIED_VIOLATION]),
        deduction_details=deduction_details,
        inspection_frequency=result.inspection_frequency,
        scored_at=now,
        scoring_period=f"{now.year}年第{(now.month - 1) // 3 + 1}季度",
        remark=remark
    )

    db.add(score_record)
    db.commit()
    db.refresh(score_record)
    return score_record


def generate_inspection_plans(
    compliance_score: ComplianceScore, db: Session,
    start_date: Optional[date] = None,
    plan_count: Optional[int] = None
) -> List[SupervisionPlan]:
    institution = db.query(Institution).filter(
        Institution.id == compliance_score.institution_id
    ).first()
    if not institution:
        return []

    start_date = start_date or date.today()
    months = FREQUENCY_MONTHS.get(compliance_score.inspection_frequency, 12)

    if plan_count is None:
        if compliance_score.grade == ComplianceGrade.EXCELLENT:
            plan_count = 1
        elif compliance_score.grade == ComplianceGrade.GOOD:
            plan_count = 2
        elif compliance_score.grade == ComplianceGrade.FAIR:
            plan_count = 3
        else:
            plan_count = 4

    plans = []
    grade_name = GRADE_NAMES.get(compliance_score.grade, "未知")
    priority = CluePriority.LOW
    if compliance_score.grade == ComplianceGrade.FAIR:
        priority = CluePriority.MEDIUM
    elif compliance_score.grade == ComplianceGrade.POOR:
        priority = CluePriority.HIGH

    focus_areas_map = {
        ComplianceGrade.EXCELLENT: "常规合规检查，重点关注执业资质维护",
        ComplianceGrade.GOOD: "常规合规检查，重点关注执业规范性",
        ComplianceGrade.FAIR: "重点检查超范围执业、人员资质问题，核查整改落实情况",
        ComplianceGrade.POOR: "全面执法检查，重点核查无证上岗、超范围执业、虚假宣传等严重违规行为",
    }

    content_template = {
        ComplianceGrade.EXCELLENT: [
            "1. 检查医疗机构执业许可证有效性及诊疗科目范围",
            "2. 抽查从业人员执业资质",
            "3. 检查医疗广告发布情况"
        ],
        ComplianceGrade.GOOD: [
            "1. 检查医疗机构执业许可证及诊疗科目",
            "2. 核查从业人员执业资质，重点抽查医师、护士",
            "3. 检查医疗质量安全管理制度落实情况",
            "4. 抽查近期开展的医疗美容项目是否合规"
        ],
        ComplianceGrade.FAIR: [
            "1. 全面核查医疗机构执业许可证及诊疗项目授权",
            "2. 逐一核查所有从业人员执业资质",
            "3. 检查近6个月所有诊疗记录，排查超范围执业情况",
            "4. 核查广告宣传内容真实性",
            "5. 检查前期问题整改落实情况"
        ],
        ComplianceGrade.POOR: [
            "1. 全面执法检查，核查所有执业资质",
            "2. 逐一核查所有从业人员资质，严禁无证上岗",
            "3. 检查近12个月所有诊疗记录，逐一核实项目合规性",
            "4. 全面排查虚假宣传线索，包括线上线下广告",
            "5. 核查所有已核实违规问题的整改情况",
            "6. 依法查处发现的违法违规行为"
        ],
    }

    for i in range(plan_count):
        planned_date = start_date + timedelta(days=months * 30 * i)
        plan_content = "\n".join(content_template.get(compliance_score.grade, []))

        plan = SupervisionPlan(
            compliance_score_id=compliance_score.id,
            institution_id=institution.id,
            plan_title=f"[{grade_name}] {institution.name} 第{i+1}轮监管检查计划",
            plan_content=plan_content,
            planned_date=planned_date,
            status=PlanStatus.PENDING,
            priority=priority,
            focus_areas=focus_areas_map.get(compliance_score.grade, "")
        )
        db.add(plan)
        plans.append(plan)

    db.commit()
    for plan in plans:
        db.refresh(plan)
    return plans


@router.post("/calculate/{institution_id}", response_model=schemas.ComplianceScoreDetail)
def calculate_and_save_score(
    institution_id: int,
    remark: Optional[str] = None,
    generate_plans: bool = Query(True, description="是否自动生成监管计划"),
    db: Session = Depends(get_db)
):
    result = calculate_compliance_score(institution_id, db)
    score_record = save_compliance_score(result, db, remark)

    if generate_plans:
        generate_inspection_plans(score_record, db)

    deduction_list = []
    if score_record.deduction_details:
        try:
            raw_deductions = json.loads(score_record.deduction_details)
            deduction_list = [
                schemas.ScoreDeduction(
                    item=ScoreItem(d["item"]),
                    max_score=d["max_score"],
                    actual_score=d["actual_score"],
                    deduction=d["deduction"],
                    reason=d["reason"]
                ) for d in raw_deductions
            ]
        except (json.JSONDecodeError, KeyError):
            pass

    return schemas.ComplianceScoreDetail(
        **{c.name: getattr(score_record, c.name) for c in score_record.__table__.columns},
        institution=score_record.institution,
        deduction_list=deduction_list
    )


@router.post("/batch-calculate", response_model=schemas.BatchScoreResult)
def batch_calculate_scores(
    generate_plans: bool = Query(True, description="是否自动生成监管计划"),
    db: Session = Depends(get_db)
):
    institutions = db.query(Institution).all()
    results = []
    scored_count = 0
    skipped_count = 0

    for inst in institutions:
        try:
            result = calculate_compliance_score(inst.id, db)
            score_record = save_compliance_score(result, db, "批量评分生成")
            if generate_plans:
                generate_inspection_plans(score_record, db)
            results.append(result)
            scored_count += 1
        except Exception as e:
            skipped_count += 1
            continue

    return schemas.BatchScoreResult(
        total_institutions=len(institutions),
        scored_count=scored_count,
        skipped_count=skipped_count,
        results=results
    )


@router.get("/institution/{institution_id}/latest", response_model=schemas.ComplianceScoreDetail)
def get_latest_score(institution_id: int, db: Session = Depends(get_db)):
    institution = db.query(Institution).filter(Institution.id == institution_id).first()
    if not institution:
        raise HTTPException(status_code=404, detail="机构不存在")

    latest_score = db.query(ComplianceScore).filter(
        ComplianceScore.institution_id == institution_id
    ).order_by(ComplianceScore.scored_at.desc()).first()

    if not latest_score:
        raise HTTPException(status_code=404, detail="该机构暂无合规评分记录")

    deduction_list = []
    if latest_score.deduction_details:
        try:
            raw_deductions = json.loads(latest_score.deduction_details)
            deduction_list = [
                schemas.ScoreDeduction(
                    item=ScoreItem(d["item"]),
                    max_score=d["max_score"],
                    actual_score=d["actual_score"],
                    deduction=d["deduction"],
                    reason=d["reason"]
                ) for d in raw_deductions
            ]
        except (json.JSONDecodeError, KeyError):
            pass

    return schemas.ComplianceScoreDetail(
        **{c.name: getattr(latest_score, c.name) for c in latest_score.__table__.columns},
        institution=latest_score.institution,
        deduction_list=deduction_list
    )


@router.get("/list", response_model=List[schemas.ComplianceScoreDetail])
def list_scores(
    grade: Optional[ComplianceGrade] = Query(None, description="按合规等级筛选"),
    min_score: Optional[float] = Query(None, ge=0, le=100, description="最低评分"),
    max_score: Optional[float] = Query(None, ge=0, le=100, description="最高评分"),
    only_latest: bool = Query(True, description="是否只显示最新评分"),
    db: Session = Depends(get_db)
):
    query = db.query(ComplianceScore)

    if grade:
        query = query.filter(ComplianceScore.grade == grade)
    if min_score is not None:
        query = query.filter(ComplianceScore.total_score >= min_score)
    if max_score is not None:
        query = query.filter(ComplianceScore.total_score <= max_score)

    if only_latest:
        subquery = db.query(
            ComplianceScore.institution_id,
            func.max(ComplianceScore.scored_at).label("max_scored_at")
        ).group_by(ComplianceScore.institution_id).subquery()
        query = query.join(
            subquery,
            (ComplianceScore.institution_id == subquery.c.institution_id) &
            (ComplianceScore.scored_at == subquery.c.max_scored_at)
        )

    scores = query.order_by(ComplianceScore.total_score.asc()).all()
    results = []

    for score in scores:
        deduction_list = []
        if score.deduction_details:
            try:
                raw_deductions = json.loads(score.deduction_details)
                deduction_list = [
                    schemas.ScoreDeduction(
                        item=ScoreItem(d["item"]),
                        max_score=d["max_score"],
                        actual_score=d["actual_score"],
                        deduction=d["deduction"],
                        reason=d["reason"]
                    ) for d in raw_deductions
                ]
            except (json.JSONDecodeError, KeyError):
                pass

        results.append(schemas.ComplianceScoreDetail(
            **{c.name: getattr(score, c.name) for c in score.__table__.columns},
            institution=score.institution,
            deduction_list=deduction_list
        ))

    return results


@router.get("/grade-distribution", response_model=List[schemas.ComplianceGradeDistribution])
def get_grade_distribution(db: Session = Depends(get_db)):
    subquery = db.query(
        ComplianceScore.institution_id,
        func.max(ComplianceScore.scored_at).label("max_scored_at")
    ).group_by(ComplianceScore.institution_id).subquery()

    latest_scores = db.query(ComplianceScore).join(
        subquery,
        (ComplianceScore.institution_id == subquery.c.institution_id) &
        (ComplianceScore.scored_at == subquery.c.max_scored_at)
    ).all()

    total = len(latest_scores) if latest_scores else 1
    distribution = []

    for grade in ComplianceGrade:
        grade_range = next((r for r in GRADE_RANGES if r[0] == grade), None)
        score_range = f"{grade_range[1]}-{grade_range[2]}" if grade_range else ""

        count = sum(1 for s in latest_scores if s.grade == grade)
        distribution.append(schemas.ComplianceGradeDistribution(
            grade=grade,
            grade_name=GRADE_NAMES.get(grade, "未知"),
            count=count,
            percentage=round(count / total * 100, 2) if total > 0 else 0.0,
            score_range=score_range
        ))

    return distribution


@router.post("/plans/generate/{score_id}", response_model=schemas.PlanGenerationResult)
def generate_plans_for_score(
    score_id: int,
    plan_count: Optional[int] = Query(None, ge=1, le=12, description="生成计划数量"),
    start_date: Optional[date] = Query(None, description="计划开始日期"),
    db: Session = Depends(get_db)
):
    compliance_score = db.query(ComplianceScore).filter(ComplianceScore.id == score_id).first()
    if not compliance_score:
        raise HTTPException(status_code=404, detail="评分记录不存在")

    institution = db.query(Institution).filter(
        Institution.id == compliance_score.institution_id
    ).first()

    plans = generate_inspection_plans(compliance_score, db, start_date, plan_count)

    return schemas.PlanGenerationResult(
        institution_id=compliance_score.institution_id,
        institution_name=institution.name if institution else "",
        grade=compliance_score.grade,
        plans_created=len(plans),
        plans=plans
    )


@router.get("/plans", response_model=List[schemas.SupervisionPlanDetail])
def list_plans(
    institution_id: Optional[int] = None,
    status: Optional[PlanStatus] = None,
    grade: Optional[ComplianceGrade] = None,
    priority: Optional[CluePriority] = None,
    db: Session = Depends(get_db)
):
    query = db.query(SupervisionPlan).join(ComplianceScore)

    if institution_id:
        query = query.filter(SupervisionPlan.institution_id == institution_id)
    if status:
        query = query.filter(SupervisionPlan.status == status)
    if priority:
        query = query.filter(SupervisionPlan.priority == priority)
    if grade:
        query = query.filter(ComplianceScore.grade == grade)

    plans = query.order_by(SupervisionPlan.planned_date.asc()).all()
    return [
        schemas.SupervisionPlanDetail(
            **{c.name: getattr(plan, c.name) for c in plan.__table__.columns},
            institution=plan.institution,
            compliance_score=plan.compliance_score
        ) for plan in plans
    ]


@router.get("/plans/{plan_id}", response_model=schemas.SupervisionPlanDetail)
def get_plan(plan_id: int, db: Session = Depends(get_db)):
    plan = db.query(SupervisionPlan).filter(SupervisionPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="计划不存在")
    return schemas.SupervisionPlanDetail(
        **{c.name: getattr(plan, c.name) for c in plan.__table__.columns},
        institution=plan.institution,
        compliance_score=plan.compliance_score
    )


@router.put("/plans/{plan_id}", response_model=schemas.SupervisionPlan)
def update_plan(
    plan_id: int,
    plan_data: schemas.SupervisionPlanUpdate,
    db: Session = Depends(get_db)
):
    plan = db.query(SupervisionPlan).filter(SupervisionPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="计划不存在")

    update_data = plan_data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(plan, key, value)

    db.commit()
    db.refresh(plan)
    return plan


@router.delete("/plans/{plan_id}")
def delete_plan(plan_id: int, db: Session = Depends(get_db)):
    plan = db.query(SupervisionPlan).filter(SupervisionPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="计划不存在")
    db.delete(plan)
    db.commit()
    return {"status": "success", "message": "计划已删除"}
