from sqlalchemy import Column, Integer, String, Date, DateTime, ForeignKey, Text, Enum, Float, Boolean, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

from .database import Base


class InstitutionType(str, enum.Enum):
    CLINIC = "医疗美容诊所"
    HOSPITAL = "医疗美容医院"
    DEPARTMENT = "医院美容科"
    OUTPATIENT = "医疗美容门诊部"


class ProcedureCategory(str, enum.Enum):
    SURGERY = "手术类"
    INJECTION = "注射类"
    PHOTOELECTRIC = "光电类"
    SKINCARE = "皮肤护理类"
    ORAL = "口腔美容类"


class SurgeryLevel(str, enum.Enum):
    LEVEL_1 = "一级"
    LEVEL_2 = "二级"
    LEVEL_3 = "三级"
    LEVEL_4 = "四级"


class QualificationType(str, enum.Enum):
    DOCTOR = "医师资格证"
    PRACTICE = "医师执业证"
    NURSE = "护士执业证"
    ANESTHESIA = "麻醉医师资格证"
    COSMETOLOGY = "医疗美容主诊医师资格证"


class ClueType(str, enum.Enum):
    QUICK_TRAINING = "疑似速成班培训"
    UNLICENSED_STAFF = "无证人员上岗"
    FALSE_ADVERTISEMENT = "广告虚假宣传"
    OVER_RANGE_PRACTICE = "超范围执业"


class ClueStatus(str, enum.Enum):
    PENDING = "待分派"
    ASSIGNED = "核查中"
    VERIFIED = "已核实违规"
    DISMISSED = "已排除"


class CluePriority(str, enum.Enum):
    HIGH = "高"
    MEDIUM = "中"
    LOW = "低"


class ComplianceGrade(str, enum.Enum):
    EXCELLENT = "A"
    GOOD = "B"
    FAIR = "C"
    POOR = "D"


class ScoreItem(str, enum.Enum):
    LICENSE_VALID = "许可证有效性"
    LICENSE_COMPLETE = "许可证完整性"
    NO_OVER_RANGE = "无超范围执业"
    ALL_STAFF_LICENSED = "从业人员全部持证"
    NO_QUICK_TRAINING = "无速成班线索"
    NO_FALSE_ADVERTISEMENT = "无虚假宣传线索"
    NO_VERIFIED_VIOLATION = "无已核实违规记录"


class InspectionFrequency(str, enum.Enum):
    QUARTERLY = "每季度一次"
    BIANNUAL = "每半年一次"
    ANNUAL = "每年一次"
    EXTENDED = "每两年一次"


class PlanStatus(str, enum.Enum):
    PENDING = "待执行"
    IN_PROGRESS = "进行中"
    COMPLETED = "已完成"
    CANCELLED = "已取消"


class RectificationCaseStatus(str, enum.Enum):
    OPEN = "整改中"
    PARTIALLY_PASSED = "部分通过"
    RETURNED = "已退回"
    OVERDUE = "逾期升级"
    CLOSED = "已关闭"
    RECURRED = "认定复发"


class RectificationItemStatus(str, enum.Enum):
    PENDING = "待整改"
    SUBMITTED = "已提交"
    PASSED = "复核通过"
    RETURNED = "退回整改"
    RECURRED = "认定复发"
    REVOKED = "复核已撤销"


class RectificationDecision(str, enum.Enum):
    PARTIAL_PASS = "部分通过"
    RETURN = "退回"
    RECURRENCE = "认定复发"
    CLOSE = "关闭案件"
    REVOKE = "撤销复核"


class ReviewResult(str, enum.Enum):
    PASSED = "通过"
    RETURNED = "退回"
    RECURRED = "复发"


class ScoreAdjustmentType(str, enum.Enum):
    DEDUCTION = "违规扣分"
    RESTORE = "整改恢复"
    RECURRENCE_DEDUCTION = "复发扣分"
    REVOKE_RESTORE = "撤销恢复回滚"
    OVERDUE_PENALTY = "逾期升级扣分"


class ScoreAdjustmentStatus(str, enum.Enum):
    EFFECTIVE = "有效"
    REVOKED = "已撤销"
    SUPERSEDED = "已被后续调整抵消"


class CaseEventType(str, enum.Enum):
    CREATED = "立案"
    EVIDENCE_SUBMITTED = "提交整改证据"
    REVIEW = "复核"
    CLOSED = "关闭案件"
    OVERDUE_ESCALATED = "逾期升级"
    REOPENED = "案件重开"


class Institution(Base):
    __tablename__ = "institutions"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False, index=True)
    unified_social_code = Column(String(50), unique=True, index=True)
    institution_type = Column(Enum(InstitutionType), nullable=False)
    legal_person = Column(String(100))
    address = Column(String(500))
    phone = Column(String(50))
    registration_date = Column(Date)
    business_scope = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    licenses = relationship("InstitutionLicense", back_populates="institution", cascade="all, delete-orphan")
    authorized_procedures = relationship("InstitutionAuthorizedProcedure", back_populates="institution", cascade="all, delete-orphan")
    practitioners = relationship("Practitioner", back_populates="institution")
    actual_procedure_records = relationship("ActualProcedureRecord", back_populates="institution")
    clues = relationship("ViolationClue", back_populates="institution")
    compliance_scores = relationship("ComplianceScore", back_populates="institution", cascade="all, delete-orphan")
    supervision_plans = relationship("SupervisionPlan", back_populates="institution", cascade="all, delete-orphan")
    rectification_cases = relationship("RectificationCase", back_populates="institution", cascade="all, delete-orphan")


class InstitutionLicense(Base):
    __tablename__ = "institution_licenses"

    id = Column(Integer, primary_key=True, index=True)
    institution_id = Column(Integer, ForeignKey("institutions.id"), nullable=False)
    license_number = Column(String(100), unique=True, nullable=False, index=True)
    issuing_authority = Column(String(200))
    issue_date = Column(Date)
    valid_until = Column(Date)
    approved_surgeries = Column(Text)
    is_valid = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    institution = relationship("Institution", back_populates="licenses")


class Practitioner(Base):
    __tablename__ = "practitioners"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, index=True)
    id_card = Column(String(18), unique=True, index=True)
    gender = Column(String(10))
    birth_date = Column(Date)
    institution_id = Column(Integer, ForeignKey("institutions.id"))
    position = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    institution = relationship("Institution", back_populates="practitioners")
    qualifications = relationship("PractitionerQualification", back_populates="practitioner", cascade="all, delete-orphan")
    authorized_procedures = relationship("PractitionerAuthorizedProcedure", back_populates="practitioner", cascade="all, delete-orphan")
    actual_procedure_records = relationship("ActualProcedureRecord", back_populates="practitioner")


class PractitionerQualification(Base):
    __tablename__ = "practitioner_qualifications"

    id = Column(Integer, primary_key=True, index=True)
    practitioner_id = Column(Integer, ForeignKey("practitioners.id"), nullable=False)
    qualification_type = Column(Enum(QualificationType), nullable=False)
    certificate_number = Column(String(100), nullable=False, index=True)
    issuing_authority = Column(String(200))
    issue_date = Column(Date)
    valid_until = Column(Date)
    practice_scope = Column(String(500))
    is_valid = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    practitioner = relationship("Practitioner", back_populates="qualifications")


class Procedure(Base):
    __tablename__ = "procedures"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False, unique=True, index=True)
    code = Column(String(50), unique=True, index=True)
    category = Column(Enum(ProcedureCategory), nullable=False)
    surgery_level = Column(Enum(SurgeryLevel))
    description = Column(Text)
    requires_qualification = Column(String(500))
    created_at = Column(DateTime, default=datetime.utcnow)

    institution_authorizations = relationship("InstitutionAuthorizedProcedure", back_populates="procedure")
    practitioner_authorizations = relationship("PractitionerAuthorizedProcedure", back_populates="procedure")
    actual_records = relationship("ActualProcedureRecord", back_populates="procedure")


class InstitutionAuthorizedProcedure(Base):
    __tablename__ = "institution_authorized_procedures"

    id = Column(Integer, primary_key=True, index=True)
    institution_id = Column(Integer, ForeignKey("institutions.id"), nullable=False)
    procedure_id = Column(Integer, ForeignKey("procedures.id"), nullable=False)
    authorized_date = Column(Date)
    remark = Column(String(500))
    created_at = Column(DateTime, default=datetime.utcnow)

    institution = relationship("Institution", back_populates="authorized_procedures")
    procedure = relationship("Procedure", back_populates="institution_authorizations")


class PractitionerAuthorizedProcedure(Base):
    __tablename__ = "practitioner_authorized_procedures"

    id = Column(Integer, primary_key=True, index=True)
    practitioner_id = Column(Integer, ForeignKey("practitioners.id"), nullable=False)
    procedure_id = Column(Integer, ForeignKey("procedures.id"), nullable=False)
    authorized_date = Column(Date)
    remark = Column(String(500))
    created_at = Column(DateTime, default=datetime.utcnow)

    practitioner = relationship("Practitioner", back_populates="authorized_procedures")
    procedure = relationship("Procedure", back_populates="practitioner_authorizations")


class ActualProcedureRecord(Base):
    __tablename__ = "actual_procedure_records"

    id = Column(Integer, primary_key=True, index=True)
    institution_id = Column(Integer, ForeignKey("institutions.id"), nullable=False)
    practitioner_id = Column(Integer, ForeignKey("practitioners.id"), nullable=False)
    procedure_id = Column(Integer, ForeignKey("procedures.id"), nullable=False)
    procedure_date = Column(Date, nullable=False)
    patient_count = Column(Integer, default=1)
    remark = Column(String(500))
    is_over_range = Column(Boolean, default=False)
    over_range_detail = Column(String(500))
    created_at = Column(DateTime, default=datetime.utcnow)

    institution = relationship("Institution", back_populates="actual_procedure_records")
    practitioner = relationship("Practitioner", back_populates="actual_procedure_records")
    procedure = relationship("Procedure", back_populates="actual_records")


class ViolationClue(Base):
    __tablename__ = "violation_clues"

    id = Column(Integer, primary_key=True, index=True)
    clue_type = Column(Enum(ClueType), nullable=False)
    title = Column(String(300), nullable=False)
    description = Column(Text, nullable=False)
    institution_id = Column(Integer, ForeignKey("institutions.id"))
    practitioner_id = Column(Integer, ForeignKey("practitioners.id"))
    procedure_id = Column(Integer, ForeignKey("procedures.id"))
    source = Column(String(200))
    priority = Column(Enum(CluePriority), default=CluePriority.MEDIUM)
    status = Column(Enum(ClueStatus), default=ClueStatus.PENDING)
    assignee = Column(String(100))
    assigned_at = Column(DateTime)
    conclusion = Column(Text)
    verified_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    institution = relationship("Institution", back_populates="clues")
    procedure = relationship("Procedure")
    inspection_records = relationship("InspectionRecord", back_populates="clue", cascade="all, delete-orphan")
    violation_links = relationship(
        "ClueViolationLink", back_populates="clue", cascade="all, delete-orphan"
    )


class InspectionRecord(Base):
    __tablename__ = "inspection_records"

    id = Column(Integer, primary_key=True, index=True)
    clue_id = Column(Integer, ForeignKey("violation_clues.id"), nullable=False)
    inspector = Column(String(100), nullable=False)
    inspection_date = Column(Date, nullable=False)
    content = Column(Text, nullable=False)
    finding = Column(String(500))
    created_at = Column(DateTime, default=datetime.utcnow)

    clue = relationship("ViolationClue", back_populates="inspection_records")


class ComplianceScore(Base):
    __tablename__ = "compliance_scores"

    id = Column(Integer, primary_key=True, index=True)
    institution_id = Column(Integer, ForeignKey("institutions.id"), nullable=False)
    total_score = Column(Float, nullable=False, default=100.0)
    grade = Column(Enum(ComplianceGrade), nullable=False)
    license_valid_score = Column(Float, default=15.0)
    license_complete_score = Column(Float, default=10.0)
    no_over_range_score = Column(Float, default=20.0)
    all_staff_licensed_score = Column(Float, default=20.0)
    no_quick_training_score = Column(Float, default=15.0)
    no_false_advertisement_score = Column(Float, default=10.0)
    no_verified_violation_score = Column(Float, default=10.0)
    deduction_details = Column(Text)
    inspection_frequency = Column(Enum(InspectionFrequency), nullable=False)
    scored_at = Column(DateTime, default=datetime.utcnow)
    scoring_period = Column(String(50))
    remark = Column(String(500))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    institution = relationship("Institution", back_populates="compliance_scores")
    supervision_plans = relationship("SupervisionPlan", back_populates="compliance_score", cascade="all, delete-orphan")


class SupervisionPlan(Base):
    __tablename__ = "supervision_plans"

    id = Column(Integer, primary_key=True, index=True)
    compliance_score_id = Column(Integer, ForeignKey("compliance_scores.id"), nullable=False)
    institution_id = Column(Integer, ForeignKey("institutions.id"), nullable=False)
    plan_title = Column(String(300), nullable=False)
    plan_content = Column(Text, nullable=False)
    planned_date = Column(Date, nullable=False)
    inspector = Column(String(100))
    status = Column(Enum(PlanStatus), default=PlanStatus.PENDING)
    priority = Column(Enum(CluePriority), default=CluePriority.MEDIUM)
    focus_areas = Column(Text)
    actual_inspection_date = Column(Date)
    result = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    compliance_score = relationship("ComplianceScore", back_populates="supervision_plans")
    institution = relationship("Institution", back_populates="supervision_plans")


class ClueViolationLink(Base):
    """线索对案件内违规结论的引用：同一 violation_key 可被多条线索引用，扣分只认结论去重。"""
    __tablename__ = "clue_violation_links"

    id = Column(Integer, primary_key=True, index=True)
    clue_id = Column(Integer, ForeignKey("violation_clues.id"), nullable=False, index=True)
    case_id = Column(Integer, ForeignKey("rectification_cases.id"), nullable=False, index=True)
    institution_id = Column(Integer, ForeignKey("institutions.id"), nullable=False, index=True)
    violation_key = Column(String(200), nullable=False, index=True)
    violation_summary = Column(String(500))
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("clue_id", "violation_key", name="uq_clue_violation_key"),
    )

    clue = relationship("ViolationClue", back_populates="violation_links")
    case = relationship("RectificationCase", back_populates="violation_links")


class RectificationCase(Base):
    __tablename__ = "rectification_cases"

    id = Column(Integer, primary_key=True, index=True)
    case_no = Column(String(50), unique=True, index=True, nullable=False)
    institution_id = Column(Integer, ForeignKey("institutions.id"), nullable=False, index=True)
    title = Column(String(300), nullable=False)
    source_clue_id = Column(Integer, ForeignKey("violation_clues.id"))
    status = Column(Enum(RectificationCaseStatus), nullable=False,
                    default=RectificationCaseStatus.OPEN)
    score_item = Column(Enum(ScoreItem), nullable=False)
    due_date = Column(Date, nullable=False)
    escalated = Column(Boolean, default=False)
    escalated_at = Column(DateTime)
    closed_at = Column(DateTime)
    close_review_id = Column(Integer)
    created_by = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    institution = relationship("Institution", back_populates="rectification_cases")
    source_clue = relationship("ViolationClue", foreign_keys=[source_clue_id])
    items = relationship(
        "RectificationItem", back_populates="case",
        cascade="all, delete-orphan",
        order_by="RectificationItem.sort_order"
    )
    violation_links = relationship(
        "ClueViolationLink", back_populates="case",
        cascade="all, delete-orphan"
    )
    reviews = relationship(
        "RectificationReview", back_populates="case",
        cascade="all, delete-orphan",
        order_by="RectificationReview.id"
    )
    events = relationship(
        "RectificationEvent", back_populates="case",
        cascade="all, delete-orphan", order_by="RectificationEvent.created_at"
    )


class RectificationItem(Base):
    __tablename__ = "rectification_items"

    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(Integer, ForeignKey("rectification_cases.id"), nullable=False, index=True)
    violation_key = Column(String(200), nullable=False, index=True)
    violation_summary = Column(String(500))
    requirement = Column(Text, nullable=False)
    acceptance_criteria = Column(Text)
    due_date = Column(Date, nullable=False)
    status = Column(Enum(RectificationItemStatus), nullable=False,
                    default=RectificationItemStatus.PENDING)
    sort_order = Column(Integer, default=0)
    current_evidence_id = Column(
        Integer, ForeignKey("rectification_evidences.id", use_alter=True)
    )
    passed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("case_id", "violation_key", "sort_order", name="uq_case_item_order"),
    )

    case = relationship("RectificationCase", back_populates="items")
    evidences = relationship(
        "RectificationEvidence", back_populates="item",
        cascade="all, delete-orphan",
        foreign_keys="RectificationEvidence.item_id",
        order_by="RectificationEvidence.version"
    )
    current_evidence = relationship(
        "RectificationEvidence", foreign_keys=[current_evidence_id], post_update=True
    )
    item_reviews = relationship(
        "RectificationItemReview", back_populates="item",
        cascade="all, delete-orphan"
    )


class RectificationEvidence(Base):
    """机构提交的版本化整改证据，每次提交生成新版本，旧版本不可变。"""
    __tablename__ = "rectification_evidences"

    id = Column(Integer, primary_key=True, index=True)
    item_id = Column(Integer, ForeignKey("rectification_items.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    material_name = Column(String(300), nullable=False)
    file_ref = Column(String(500))
    content = Column(Text)
    submitted_by = Column(String(100))
    submitted_at = Column(DateTime, default=datetime.utcnow)

    item = relationship(
        "RectificationItem", back_populates="evidences",
        foreign_keys=[item_id]
    )


class RectificationReview(Base):
    """一次复核动作的结论（部分通过/退回/认定复发/关闭/撤销）。"""
    __tablename__ = "rectification_reviews"

    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(Integer, ForeignKey("rectification_cases.id"), nullable=False, index=True)
    decision = Column(Enum(RectificationDecision), nullable=False)
    reviewer = Column(String(100), nullable=False)
    comment = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    revoked_at = Column(DateTime)
    revoke_reason = Column(Text)
    is_effective = Column(Boolean, default=True)

    case = relationship("RectificationCase", back_populates="reviews")
    item_reviews = relationship(
        "RectificationItemReview", back_populates="review",
        cascade="all, delete-orphan"
    )


class RectificationItemReview(Base):
    """复核决定下逐项的验收结果，撤销错误复核时以此逐项回滚。"""
    __tablename__ = "rectification_item_reviews"

    id = Column(Integer, primary_key=True, index=True)
    review_id = Column(Integer, ForeignKey("rectification_reviews.id"), nullable=False, index=True)
    item_id = Column(Integer, ForeignKey("rectification_items.id"), nullable=False, index=True)
    result = Column(Enum(ReviewResult), nullable=False)
    comment = Column(Text)
    evidence_id = Column(Integer, ForeignKey("rectification_evidences.id"))
    created_at = Column(DateTime, default=datetime.utcnow)

    review = relationship("RectificationReview", back_populates="item_reviews")
    item = relationship("RectificationItem", back_populates="item_reviews")
    evidence = relationship("RectificationEvidence")


class RectificationEvent(Base):
    """案件生命周期事件流：立案/提交证据/复核/关闭/逾期升级/重开。"""
    __tablename__ = "rectification_events"

    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(Integer, ForeignKey("rectification_cases.id"), nullable=False, index=True)
    event_type = Column(Enum(CaseEventType), nullable=False)
    detail = Column(Text)
    actor = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)

    case = relationship("RectificationCase", back_populates="events")


class ScoreAdjustment(Base):
    """
    独立评分调整记录：违规扣分/整改恢复/复发扣分/撤销回滚/逾期处罚。
    评分只汇总 EFFECTIVE 记录；同一 case+type 幂等，重复线索共用同一案件不重复调整。
    """
    __tablename__ = "score_adjustments"

    id = Column(Integer, primary_key=True, index=True)
    institution_id = Column(Integer, ForeignKey("institutions.id"), nullable=False, index=True)
    score_item = Column(Enum(ScoreItem), nullable=False)
    adjustment_type = Column(Enum(ScoreAdjustmentType), nullable=False)
    points = Column(Float, nullable=False)
    reason = Column(String(500), nullable=False)
    case_id = Column(Integer, ForeignKey("rectification_cases.id"), index=True)
    review_id = Column(Integer, ForeignKey("rectification_reviews.id"))
    violation_key = Column(String(200), index=True)
    idempotency_key = Column(String(300), unique=True, index=True)
    status = Column(Enum(ScoreAdjustmentStatus), nullable=False,
                    default=ScoreAdjustmentStatus.EFFECTIVE)
    effective_at = Column(DateTime, default=datetime.utcnow)
    revoked_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

    institution = relationship("Institution")
    case = relationship("RectificationCase")
    review = relationship("RectificationReview")
