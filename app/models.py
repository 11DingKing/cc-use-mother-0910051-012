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
    CLOSED = "已关闭"
    REOPENED = "复发重开"


class RequirementStatus(str, enum.Enum):
    PENDING = "待整改"
    SUBMITTED = "待复核"
    PARTIAL_APPROVED = "部分通过"
    APPROVED = "复核通过"
    REJECTED = "已退回"
    RECURRED = "认定复发"


class ReviewDecision(str, enum.Enum):
    APPROVED = "通过"
    PARTIAL_APPROVED = "部分通过"
    REJECTED = "退回"
    RECURRED = "认定复发"


class ScoreAdjustmentType(str, enum.Enum):
    VIOLATION_PENALTY = "违规扣分"
    DISMISS_VOID = "撤案恢复"
    RESTORE = "整改复核恢复"
    RECURRENCE_PENALTY = "复发扣分"
    REVOKE_APPROVAL_PENALTY = "撤销通过重扣"
    REVOKE_RECUR_RESTORE = "撤销复发恢复"
    OVERDUE_PENALTY = "逾期扣分"
    OVERDUE_VOID = "逾期撤销恢复"


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
    rectification_cases = relationship("RectificationCase", back_populates="institution")
    score_adjustments = relationship("ScoreAdjustment", back_populates="institution")


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
    rectification_cases = relationship("RectificationCase", back_populates="clue")


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


class RectificationCase(Base):
    """整改案件：由一条已核实违规线索立案，可被多条线索引用同一问题。"""
    __tablename__ = "rectification_cases"

    id = Column(Integer, primary_key=True, index=True)
    case_no = Column(String(50), unique=True, nullable=False, index=True)
    title = Column(String(300), nullable=False)
    clue_id = Column(Integer, ForeignKey("violation_clues.id"), nullable=False)
    institution_id = Column(Integer, ForeignKey("institutions.id"), nullable=False)
    status = Column(Enum(RectificationCaseStatus), default=RectificationCaseStatus.OPEN, nullable=False)
    priority = Column(Enum(CluePriority), default=CluePriority.MEDIUM, nullable=False)
    score_item = Column(Enum(ScoreItem), nullable=False)
    violation_summary = Column(Text, nullable=False)
    # 问题指纹：机构+评分维度+问题对象，同一问题被多条线索引用时共享指纹，扣分/恢复只计一次
    problem_signature = Column(String(200), nullable=False, index=True)
    penalty_score = Column(Float, nullable=False, default=0.0)
    escalated = Column(Boolean, default=False, nullable=False)
    escalated_at = Column(DateTime)
    closed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    clue = relationship("ViolationClue", back_populates="rectification_cases")
    institution = relationship("Institution", back_populates="rectification_cases")
    requirements = relationship("RectificationRequirement", back_populates="case",
                                cascade="all, delete-orphan", order_by="RectificationRequirement.id")
    reviews = relationship("RectificationReview", back_populates="case",
                           cascade="all, delete-orphan", order_by="desc(RectificationReview.reviewed_at)")
    clue_links = relationship("RectificationCaseClue", back_populates="case",
                              cascade="all, delete-orphan")


class RectificationCaseClue(Base):
    """线索-案件关联：支持多条线索引用同一整改案件（同一问题不重复扣分）。"""
    __tablename__ = "rectification_case_clues"

    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(Integer, ForeignKey("rectification_cases.id"), nullable=False)
    clue_id = Column(Integer, ForeignKey("violation_clues.id"), nullable=False)
    linked_at = Column(DateTime, default=datetime.utcnow)
    link_remark = Column(String(500))

    __table_args__ = (
        UniqueConstraint("case_id", "clue_id", name="uq_case_clue"),
    )

    case = relationship("RectificationCase", back_populates="clue_links")
    clue = relationship("ViolationClue", foreign_keys=[clue_id])


class RectificationRequirement(Base):
    """整改要求：每项违规结论拆成可验收、有期限的要求。"""
    __tablename__ = "rectification_requirements"

    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(Integer, ForeignKey("rectification_cases.id"), nullable=False)
    content = Column(Text, nullable=False)
    acceptance_criteria = Column(Text)
    due_date = Column(Date, nullable=False)
    status = Column(Enum(RequirementStatus), default=RequirementStatus.PENDING, nullable=False)
    effective_decision = Column(Enum(ReviewDecision))
    penalty_score = Column(Float, nullable=False, default=0.0)
    overdue_escalated = Column(Boolean, default=False, nullable=False)
    approved_at = Column(DateTime)
    approved_by = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    case = relationship("RectificationCase", back_populates="requirements")
    evidence_versions = relationship("RectificationEvidence", back_populates="requirement",
                                     cascade="all, delete-orphan",
                                     order_by="desc(RectificationEvidence.version_no)")
    reviews = relationship("RectificationReview", back_populates="requirement",
                           cascade="all, delete-orphan",
                           order_by="desc(RectificationReview.reviewed_at)")


class RectificationEvidence(Base):
    """整改证据：机构提交，按要求递增版本号，提交后不可修改（版本化）。"""
    __tablename__ = "rectification_evidence"

    id = Column(Integer, primary_key=True, index=True)
    requirement_id = Column(Integer, ForeignKey("rectification_requirements.id"), nullable=False)
    version_no = Column(Integer, nullable=False)
    file_name = Column(String(300), nullable=False)
    file_url = Column(String(500))
    file_hash = Column(String(128))
    content_text = Column(Text)
    submit_remark = Column(String(500))
    submitted_by = Column(String(100))
    submitted_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    requirement = relationship("RectificationRequirement", back_populates="evidence_versions")


class RectificationReview(Base):
    """复核决定：每次复核一条不可变记录，仅 is_active 的决定对状态/评分生效，可撤销。"""
    __tablename__ = "rectification_reviews"

    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(Integer, ForeignKey("rectification_cases.id"), nullable=False)
    requirement_id = Column(Integer, ForeignKey("rectification_requirements.id"))
    decision = Column(Enum(ReviewDecision), nullable=False)
    reviewer = Column(String(100), nullable=False)
    comment = Column(Text)
    evidence_version = Column(Integer)
    reviewed_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    revoked_at = Column(DateTime)
    revoked_by = Column(String(100))
    revoke_reason = Column(Text)

    case = relationship("RectificationCase", back_populates="reviews")
    requirement = relationship("RectificationRequirement", back_populates="reviews")


class ScoreAdjustment(Base):
    """评分调整台账：所有影响评分的事件均为独立、不可变记录，撤销以对冲记录实现，永不删除。"""
    __tablename__ = "score_adjustments"

    id = Column(Integer, primary_key=True, index=True)
    institution_id = Column(Integer, ForeignKey("institutions.id"), nullable=False)
    adjustment_type = Column(Enum(ScoreAdjustmentType), nullable=False)
    score_item = Column(Enum(ScoreItem), nullable=False)
    # 分值变化：扣分为负，恢复为正
    score_delta = Column(Float, nullable=False)
    problem_signature = Column(String(200), index=True)
    case_id = Column(Integer, ForeignKey("rectification_cases.id"))
    requirement_id = Column(Integer, ForeignKey("rectification_requirements.id"))
    clue_id = Column(Integer, ForeignKey("violation_clues.id"))
    review_id = Column(Integer, ForeignKey("rectification_reviews.id"))
    related_adjustment_id = Column(Integer, ForeignKey("score_adjustments.id"))
    reason = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    institution = relationship("Institution", back_populates="score_adjustments")
    related_adjustment = relationship("ScoreAdjustment", remote_side=[id])
