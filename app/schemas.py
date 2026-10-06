from pydantic import BaseModel, Field
from datetime import date, datetime
from typing import Optional, List
from .models import (
    InstitutionType, ProcedureCategory, SurgeryLevel,
    QualificationType, ClueType, ClueStatus, CluePriority,
    ComplianceGrade, ScoreItem, InspectionFrequency, PlanStatus,
    RectificationCaseStatus, RequirementStatus, ReviewDecision,
    ScoreAdjustmentType
)


class InstitutionBase(BaseModel):
    name: str
    unified_social_code: str
    institution_type: InstitutionType
    legal_person: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    registration_date: Optional[date] = None
    business_scope: Optional[str] = None


class InstitutionCreate(InstitutionBase):
    pass


class InstitutionUpdate(BaseModel):
    name: Optional[str] = None
    unified_social_code: Optional[str] = None
    institution_type: Optional[InstitutionType] = None
    legal_person: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    registration_date: Optional[date] = None
    business_scope: Optional[str] = None


class Institution(InstitutionBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class InstitutionLicenseBase(BaseModel):
    license_number: str
    issuing_authority: Optional[str] = None
    issue_date: Optional[date] = None
    valid_until: Optional[date] = None
    approved_surgeries: Optional[str] = None
    is_valid: bool = True


class InstitutionLicenseCreate(InstitutionLicenseBase):
    institution_id: int


class InstitutionLicense(InstitutionLicenseBase):
    id: int
    created_at: datetime
    institution: Optional[Institution] = None

    class Config:
        from_attributes = True


class PractitionerBase(BaseModel):
    name: str
    id_card: str
    gender: Optional[str] = None
    birth_date: Optional[date] = None
    institution_id: Optional[int] = None
    position: Optional[str] = None


class PractitionerCreate(PractitionerBase):
    pass


class PractitionerUpdate(BaseModel):
    name: Optional[str] = None
    id_card: Optional[str] = None
    gender: Optional[str] = None
    birth_date: Optional[date] = None
    institution_id: Optional[int] = None
    position: Optional[str] = None


class Practitioner(PractitionerBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class PractitionerQualificationBase(BaseModel):
    qualification_type: QualificationType
    certificate_number: str
    issuing_authority: Optional[str] = None
    issue_date: Optional[date] = None
    valid_until: Optional[date] = None
    practice_scope: Optional[str] = None
    is_valid: bool = True


class PractitionerQualificationCreate(PractitionerQualificationBase):
    practitioner_id: int


class PractitionerQualification(PractitionerQualificationBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class PractitionerWithQualifications(Practitioner):
    qualifications: List[PractitionerQualification] = []


class ProcedureBase(BaseModel):
    name: str
    code: str
    category: ProcedureCategory
    surgery_level: Optional[SurgeryLevel] = None
    description: Optional[str] = None
    requires_qualification: Optional[str] = None


class ProcedureCreate(ProcedureBase):
    pass


class ProcedureUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    category: Optional[ProcedureCategory] = None
    surgery_level: Optional[SurgeryLevel] = None
    description: Optional[str] = None
    requires_qualification: Optional[str] = None


class Procedure(ProcedureBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class InstitutionAuthorizedProcedureBase(BaseModel):
    institution_id: int
    procedure_id: int
    authorized_date: Optional[date] = None
    remark: Optional[str] = None


class InstitutionAuthorizedProcedureCreate(InstitutionAuthorizedProcedureBase):
    pass


class InstitutionAuthorizedProcedure(InstitutionAuthorizedProcedureBase):
    id: int
    created_at: datetime
    procedure: Optional[Procedure] = None

    class Config:
        from_attributes = True


class PractitionerAuthorizedProcedureBase(BaseModel):
    practitioner_id: int
    procedure_id: int
    authorized_date: Optional[date] = None
    remark: Optional[str] = None


class PractitionerAuthorizedProcedureCreate(PractitionerAuthorizedProcedureBase):
    pass


class PractitionerAuthorizedProcedure(PractitionerAuthorizedProcedureBase):
    id: int
    created_at: datetime
    procedure: Optional[Procedure] = None

    class Config:
        from_attributes = True


class ActualProcedureRecordBase(BaseModel):
    institution_id: int
    practitioner_id: int
    procedure_id: int
    procedure_date: date
    patient_count: int = 1
    remark: Optional[str] = None


class ActualProcedureRecordCreate(ActualProcedureRecordBase):
    pass


class ActualProcedureRecord(ActualProcedureRecordBase):
    id: int
    is_over_range: bool = False
    over_range_detail: Optional[str] = None
    created_at: datetime
    procedure: Optional[Procedure] = None
    practitioner: Optional[Practitioner] = None

    class Config:
        from_attributes = True


class ViolationClueBase(BaseModel):
    clue_type: ClueType
    title: str
    description: str
    institution_id: Optional[int] = None
    practitioner_id: Optional[int] = None
    procedure_id: Optional[int] = None
    source: Optional[str] = None
    priority: CluePriority = CluePriority.MEDIUM


class ViolationClueCreate(ViolationClueBase):
    pass


class ViolationClueUpdate(BaseModel):
    clue_type: Optional[ClueType] = None
    title: Optional[str] = None
    description: Optional[str] = None
    institution_id: Optional[int] = None
    practitioner_id: Optional[int] = None
    procedure_id: Optional[int] = None
    source: Optional[str] = None
    priority: Optional[CluePriority] = None
    status: Optional[ClueStatus] = None
    assignee: Optional[str] = None
    conclusion: Optional[str] = None


class ViolationClue(ViolationClueBase):
    id: int
    status: ClueStatus = ClueStatus.PENDING
    assignee: Optional[str] = None
    assigned_at: Optional[datetime] = None
    conclusion: Optional[str] = None
    verified_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ClueAssign(BaseModel):
    assignee: str


class ClueConclusion(BaseModel):
    status: ClueStatus
    conclusion: str


class InspectionRecordBase(BaseModel):
    clue_id: int
    inspector: str
    inspection_date: date
    content: str
    finding: Optional[str] = None


class InspectionRecordCreate(InspectionRecordBase):
    pass


class InspectionRecord(InspectionRecordBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class InstitutionComplianceCheck(BaseModel):
    institution_id: int
    institution_name: str
    has_valid_license: bool
    license_detail: Optional[str] = None
    authorized_procedure_count: int
    actual_procedure_count: int
    over_range_count: int
    unlicensed_practitioners: int
    total_practitioners: int
    clues_count: int
    verified_violations: int


class PractitionerComplianceCheck(BaseModel):
    practitioner_id: int
    name: str
    has_valid_doctor_license: bool
    has_valid_practice_license: bool
    has_cosmetology_license: bool
    authorized_procedures: List[str] = []
    actual_procedures: List[str] = []
    over_range_procedures: List[str] = []
    is_unlicensed: bool


class StatsByInstitution(BaseModel):
    institution_id: int
    institution_name: str
    total_clues: int
    verified_violations: int
    pending_clues: int
    over_range_count: int
    unlicensed_ratio: float
    risk_score: float


class StatsByCategory(BaseModel):
    category: ProcedureCategory
    total_actual: int
    over_range_count: int
    over_range_ratio: float
    clue_count: int


class ProblemInstitution(BaseModel):
    institution_id: int
    institution_name: str
    verified_violations: int
    over_range_count: int
    unlicensed_ratio: float
    risk_level: str


class ScoreDeduction(BaseModel):
    item: ScoreItem
    max_score: float
    actual_score: float
    deduction: float
    reason: str


class ComplianceScoreBase(BaseModel):
    institution_id: int
    remark: Optional[str] = None


class ComplianceScoreCreate(ComplianceScoreBase):
    pass


class ComplianceScoreUpdate(BaseModel):
    remark: Optional[str] = None


class ComplianceScore(ComplianceScoreBase):
    id: int
    total_score: float
    grade: ComplianceGrade
    license_valid_score: float
    license_complete_score: float
    no_over_range_score: float
    all_staff_licensed_score: float
    no_quick_training_score: float
    no_false_advertisement_score: float
    no_verified_violation_score: float
    deduction_details: Optional[str] = None
    inspection_frequency: InspectionFrequency
    scored_at: datetime
    scoring_period: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ComplianceScoreDetail(ComplianceScore):
    institution: Optional[Institution] = None
    deduction_list: Optional[List[ScoreDeduction]] = None


class SupervisionPlanBase(BaseModel):
    plan_title: str
    plan_content: str
    planned_date: date
    inspector: Optional[str] = None
    focus_areas: Optional[str] = None


class SupervisionPlanCreate(SupervisionPlanBase):
    compliance_score_id: int
    institution_id: int
    priority: Optional[CluePriority] = CluePriority.MEDIUM


class SupervisionPlanUpdate(BaseModel):
    plan_title: Optional[str] = None
    plan_content: Optional[str] = None
    planned_date: Optional[date] = None
    inspector: Optional[str] = None
    status: Optional[PlanStatus] = None
    priority: Optional[CluePriority] = None
    focus_areas: Optional[str] = None
    actual_inspection_date: Optional[date] = None
    result: Optional[str] = None


class SupervisionPlan(SupervisionPlanBase):
    id: int
    compliance_score_id: int
    institution_id: int
    status: PlanStatus
    priority: CluePriority
    actual_inspection_date: Optional[date] = None
    result: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SupervisionPlanDetail(SupervisionPlan):
    institution: Optional[Institution] = None
    compliance_score: Optional[ComplianceScore] = None


class ScoreCalculationResult(BaseModel):
    institution_id: int
    institution_name: str
    total_score: float
    grade: ComplianceGrade
    inspection_frequency: InspectionFrequency
    deductions: List[ScoreDeduction]


class BatchScoreResult(BaseModel):
    total_institutions: int
    scored_count: int
    skipped_count: int
    results: List[ScoreCalculationResult]


class PlanGenerationResult(BaseModel):
    institution_id: int
    institution_name: str
    grade: ComplianceGrade
    plans_created: int
    plans: List[SupervisionPlan]


class StatsByComplianceGrade(BaseModel):
    grade: ComplianceGrade
    institution_count: int
    avg_score: float
    total_verified_violations: int
    total_over_range_count: int
    avg_unlicensed_ratio: float


class StatsByInstitutionWithGrade(StatsByInstitution):
    compliance_grade: Optional[ComplianceGrade] = None
    compliance_score: Optional[float] = None
    inspection_frequency: Optional[InspectionFrequency] = None


class ComplianceGradeDistribution(BaseModel):
    grade: ComplianceGrade
    grade_name: str
    count: int
    percentage: float
    score_range: str


# ==================== 整改案件 ====================

class RectificationRequirementCreate(BaseModel):
    content: str
    acceptance_criteria: Optional[str] = None
    due_date: date


class RectificationCaseCreate(BaseModel):
    clue_id: int
    title: Optional[str] = None
    priority: CluePriority = CluePriority.HIGH
    requirements: List[RectificationRequirementCreate]


class RectificationCaseUpdate(BaseModel):
    title: Optional[str] = None
    priority: Optional[CluePriority] = None


class RectificationClueLink(BaseModel):
    clue_id: int
    link_remark: Optional[str] = None


class RectificationEvidenceCreate(BaseModel):
    file_name: str
    file_url: Optional[str] = None
    file_hash: Optional[str] = None
    content_text: Optional[str] = None
    submit_remark: Optional[str] = None
    submitted_by: Optional[str] = None


class RectificationReviewCreate(BaseModel):
    decision: ReviewDecision
    comment: str
    reviewer: str
    evidence_version: Optional[int] = None
    # 部分通过时显式指定每条要求的决定
    item_decisions: Optional[List["RequirementReviewItem"]] = None


class RequirementReviewItem(BaseModel):
    requirement_id: int
    decision: ReviewDecision
    comment: Optional[str] = None


class RectificationReviewRevoke(BaseModel):
    revoked_by: str
    reason: str


class RectificationEvidence(BaseModel):
    id: int
    requirement_id: int
    version_no: int
    file_name: str
    file_url: Optional[str] = None
    file_hash: Optional[str] = None
    content_text: Optional[str] = None
    submit_remark: Optional[str] = None
    submitted_by: Optional[str] = None
    submitted_at: datetime

    class Config:
        from_attributes = True


class RectificationReview(BaseModel):
    id: int
    case_id: int
    requirement_id: Optional[int] = None
    decision: ReviewDecision
    reviewer: str
    comment: Optional[str] = None
    evidence_version: Optional[int] = None
    reviewed_at: datetime
    is_active: bool
    revoked_at: Optional[datetime] = None
    revoked_by: Optional[str] = None
    revoke_reason: Optional[str] = None

    class Config:
        from_attributes = True


class RectificationRequirement(BaseModel):
    id: int
    case_id: int
    content: str
    acceptance_criteria: Optional[str] = None
    due_date: date
    status: RequirementStatus
    effective_decision: Optional[ReviewDecision] = None
    penalty_score: float
    approved_at: Optional[datetime] = None
    approved_by: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class RectificationRequirementDetail(RectificationRequirement):
    evidence_versions: List[RectificationEvidence] = []
    latest_evidence_version: Optional[int] = None
    reviews: List[RectificationReview] = []


class RectificationCaseClue(BaseModel):
    id: int
    case_id: int
    clue_id: int
    linked_at: datetime
    link_remark: Optional[str] = None
    clue: Optional[ViolationClue] = None

    class Config:
        from_attributes = True


class RectificationCase(BaseModel):
    id: int
    case_no: str
    title: str
    clue_id: int
    institution_id: int
    status: RectificationCaseStatus
    priority: CluePriority
    score_item: ScoreItem
    violation_summary: Optional[str] = None
    problem_signature: str
    penalty_score: float
    escalated: bool
    escalated_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class RectificationCaseDetail(RectificationCase):
    clue: Optional[ViolationClue] = None
    institution: Optional[Institution] = None
    requirements: List[RectificationRequirementDetail] = []
    reviews: List[RectificationReview] = []
    clue_links: List[RectificationCaseClue] = []
    linked_clue_count: int = 1


class RectificationCaseSummary(BaseModel):
    case_id: int
    case_no: str
    title: str
    institution_id: int
    status: RectificationCaseStatus
    priority: CluePriority
    score_item: ScoreItem
    penalty_score: float
    escalated: bool
    total_requirements: int
    approved_requirements: int
    pending_requirements: int
    recurred_requirements: int
    overdue_count: int
    created_at: datetime


class EscalationResult(BaseModel):
    scanned_cases: int
    overdue_requirements: int
    escalated_cases: int
    escalated_case_ids: List[int] = []


class ScoreAdjustment(BaseModel):
    id: int
    institution_id: int
    adjustment_type: ScoreAdjustmentType
    score_item: ScoreItem
    score_delta: float
    problem_signature: Optional[str] = None
    case_id: Optional[int] = None
    requirement_id: Optional[int] = None
    clue_id: Optional[int] = None
    review_id: Optional[int] = None
    related_adjustment_id: Optional[int] = None
    reason: str
    created_at: datetime

    class Config:
        from_attributes = True


class InstitutionComplianceTraceability(BaseModel):
    institution_id: int
    institution_name: str
    latest_score: Optional[ComplianceScoreDetail] = None
    score_history: List[ComplianceScoreDetail] = []
    adjustments: List[ScoreAdjustment] = []
    rectification_cases: List[RectificationCaseDetail] = []
    verified_clues: List[ViolationClue] = []


RectificationReviewCreate.model_rebuild()
