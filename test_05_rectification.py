"""整改案件、版本化证据、复核决定、评分台账与追溯的验收测试。"""
from datetime import date, datetime, timedelta

import pytest

from app.models import (
    Institution, InstitutionType, InstitutionLicense,
    ViolationClue, ClueType, ClueStatus, CluePriority,
    ScoreItem, ScoreAdjustment, ScoreAdjustmentType,
    RectificationCase, RectificationRequirement, RequirementStatus,
    RectificationCaseStatus,
)
from app.routers.compliance_score import calculate_compliance_score
from app import rectification_service as svc


@pytest.fixture
def rectification_institution(db_session):
    """无任何违规时基线评分为 100 的机构。"""
    inst = Institution(
        name="整改测试机构",
        unified_social_code="91310000RECT0001",
        institution_type=InstitutionType.CLINIC,
        legal_person="整改法人",
        address="整改测试地址1号",
        business_scope="医疗美容科"
    )
    db_session.add(inst)
    db_session.flush()
    db_session.add(InstitutionLicense(
        institution_id=inst.id,
        license_number="PDYRECT001",
        issuing_authority="测试卫健委",
        issue_date=date(2020, 1, 1),
        valid_until=date(2030, 12, 31),
        approved_surgeries="医疗美容科全部项目",
        is_valid=True,
    ))
    db_session.flush()
    return inst


def _make_clue(db_session, institution_id, clue_type=ClueType.QUICK_TRAINING,
               title=None, description=None):
    clue = ViolationClue(
        clue_type=clue_type,
        title=title or f"{clue_type.value}线索",
        description=description or "测试违规事实",
        institution_id=institution_id,
        source="测试",
        priority=CluePriority.HIGH,
        status=ClueStatus.VERIFIED,
        assignee="稽查员A",
        assigned_at=datetime(2024, 2, 1),
        conclusion="违规事实清楚",
        verified_at=datetime(2024, 2, 5),
    )
    db_session.add(clue)
    db_session.flush()
    return clue


def _create_case_api(client, clue_id, due_days=30, n_requirements=2, priority="高"):
    future = (date.today() + timedelta(days=due_days)).isoformat()
    return client.post("/api/rectifications/cases", json={
        "clue_id": clue_id,
        "priority": priority,
        "requirements": [
            {
                "content": f"整改要求{i + 1}",
                "acceptance_criteria": f"验收标准{i + 1}",
                "due_date": future,
                "penalty_score": 0.0,
            } for i in range(n_requirements)
        ],
    })


def _submit_evidence(client, requirement_id, version_note=""):
    return client.post(
        f"/api/rectifications/requirements/{requirement_id}/evidence",
        json={
            "file_name": f"整改证据{version_note}.pdf",
            "file_url": f"https://example.com/evidence/{requirement_id}/{version_note}",
            "file_hash": f"hash-{requirement_id}-{version_note}",
            "content_text": f"整改说明{version_note}",
            "submitted_by": "机构管理员",
        }
    )


# ---------------------------------------------------------------------------
# 立案
# ---------------------------------------------------------------------------

class TestCaseCreation:
    def test_create_case_from_verified_clue(self, client, db_session, rectification_institution):
        clue = _make_clue(db_session, rectification_institution.id)
        resp = _create_case_api(client, clue.id)
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["status"] == "整改中"
        assert data["case_no"].startswith("ZG-")
        assert len(data["requirements"]) == 2
        assert data["requirements"][0]["status"] == "待整改"
        assert data["linked_clue_count"] == 1
        # 立案即落三笔扣分：专项15按两条要求均摊（7.5+7.5）+ 无已核实违规边际4
        adjs = db_session.query(ScoreAdjustment).filter(
            ScoreAdjustment.case_id == data["id"]
        ).all()
        deltas = sorted(a.score_delta for a in adjs)
        assert deltas == [-7.5, -7.5, -4.0]
        assert data["penalty_score"] == 19.0

    def test_cannot_create_case_for_unverified_clue(self, client, db_session, rectification_institution):
        clue = _make_clue(db_session, rectification_institution.id)
        clue.status = ClueStatus.PENDING
        db_session.flush()
        resp = _create_case_api(client, clue.id)
        assert resp.status_code == 400
        assert "已核实违规" in resp.json()["detail"]

    def test_cannot_duplicate_case_for_same_clue(self, client, db_session, rectification_institution):
        clue = _make_clue(db_session, rectification_institution.id)
        assert _create_case_api(client, clue.id).status_code == 201
        resp = _create_case_api(client, clue.id)
        assert resp.status_code == 400
        assert "不能重复立案" in resp.json()["detail"]

    def test_requires_at_least_one_requirement(self, client, db_session, rectification_institution):
        clue = _make_clue(db_session, rectification_institution.id)
        resp = client.post("/api/rectifications/cases", json={
            "clue_id": clue.id,
            "requirements": [],
        })
        assert resp.status_code == 422 or resp.status_code == 400

    def test_non_special_clue_only_case_level_penalty(self, client, db_session, rectification_institution):
        clue = _make_clue(db_session, rectification_institution.id,
                          clue_type=ClueType.UNLICENSED_STAFF)
        resp = _create_case_api(client, clue.id)
        assert resp.status_code == 201
        data = resp.json()
        assert data["score_item"] == "无已核实违规记录"
        assert all(r["penalty_score"] == 0.0 for r in data["requirements"])
        adjs = db_session.query(ScoreAdjustment).filter(
            ScoreAdjustment.case_id == data["id"]
        ).all()
        assert [a.score_delta for a in adjs] == [-4.0]


# ---------------------------------------------------------------------------
# 多线索引用同一问题不重复扣分
# ---------------------------------------------------------------------------

class TestSharedProblemNoDoubleDeduction:
    def test_link_second_clue_creates_no_adjustment(
        self, client, db_session, rectification_institution
    ):
        clue1 = _make_clue(db_session, rectification_institution.id,
                           title="速成班线索一")
        clue2 = _make_clue(db_session, rectification_institution.id,
                           title="速成班线索二（同一问题）")
        case_resp = _create_case_api(client, clue1.id)
        case_id = case_resp.json()["id"]

        before = db_session.query(ScoreAdjustment).filter(
            ScoreAdjustment.institution_id == rectification_institution.id
        ).count()

        resp = client.post(f"/api/rectifications/cases/{case_id}/link-clue", json={
            "clue_id": clue2.id,
            "link_remark": "第二条线索引用同一问题",
        })
        assert resp.status_code == 200, resp.text

        after = db_session.query(ScoreAdjustment).filter(
            ScoreAdjustment.institution_id == rectification_institution.id
        ).count()
        assert before == after  # 关联不产生任何评分变化

        detail = client.get(f"/api/rectifications/cases/{case_id}").json()
        assert detail["linked_clue_count"] == 2

    def test_cannot_link_clue_already_in_another_case(
        self, client, db_session, rectification_institution
    ):
        c1 = _make_clue(db_session, rectification_institution.id, title="问题A")
        c2 = _make_clue(db_session, rectification_institution.id, title="问题B")
        case1 = _create_case_api(client, c1.id).json()["id"]
        case2 = _create_case_api(client, c2.id).json()["id"]
        resp = client.post(f"/api/rectifications/cases/{case1}/link-clue",
                           json={"clue_id": c2.id})
        assert resp.status_code == 400
        assert "不可重复扣分" in resp.json()["detail"]

    def test_score_counts_shared_problem_once(self, client, db_session, rectification_institution):
        c1 = _make_clue(db_session, rectification_institution.id)
        c2 = _make_clue(db_session, rectification_institution.id)
        case_id = _create_case_api(client, c1.id).json()["id"]
        client.post(f"/api/rectifications/cases/{case_id}/link-clue", json={"clue_id": c2.id})

        result = calculate_compliance_score(rectification_institution.id, db_session)
        # 只有一个问题：速成班维度 0，已核实违规维度 6（-4），其余满分 → 81
        assert result.total_score == 81.0
        items = {d.item: d.actual_score for d in result.deductions}
        assert items[ScoreItem.NO_QUICK_TRAINING] == 0.0
        assert items[ScoreItem.NO_VERIFIED_VIOLATION] == 6.0


# ---------------------------------------------------------------------------
# 版本化证据
# ---------------------------------------------------------------------------

class TestVersionedEvidence:
    def test_evidence_versions_are_incremental_and_immutable(
        self, client, db_session, rectification_institution
    ):
        clue = _make_clue(db_session, rectification_institution.id)
        case_id = _create_case_api(client, clue.id).json()["id"]
        req_id = client.get(f"/api/rectifications/cases/{case_id}").json()["requirements"][0]["id"]

        r1 = _submit_evidence(client, req_id, "v1")
        r2 = _submit_evidence(client, req_id, "v2")
        assert r1.status_code == 201
        assert r1.json()["version_no"] == 1
        assert r2.json()["version_no"] == 2

        versions = client.get(
            f"/api/rectifications/requirements/{req_id}/evidence"
        ).json()
        assert [v["version_no"] for v in versions] == [2, 1]
        # 历史版本内容保留不变
        assert versions[1]["file_hash"] == f"hash-{req_id}-v1"

        req = client.get(f"/api/rectifications/cases/{case_id}").json()["requirements"][0]
        assert req["latest_evidence_version"] == 2
        assert req["status"] == "待复核"

    def test_cannot_submit_after_approval(self, client, db_session, rectification_institution):
        clue = _make_clue(db_session, rectification_institution.id)
        case_id = _create_case_api(client, clue.id, n_requirements=1).json()["id"]
        req_id = client.get(f"/api/rectifications/cases/{case_id}").json()["requirements"][0]["id"]
        _submit_evidence(client, req_id, "v1")
        client.post(f"/api/rectifications/cases/{case_id}/reviews", json={
            "decision": "通过",
            "reviewer": "复核员甲",
            "comment": "材料齐全，通过",
        })
        resp = _submit_evidence(client, req_id, "v2")
        assert resp.status_code == 400
        assert "已复核通过" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 复核决定：部分通过、退回、关闭规则、复发
# ---------------------------------------------------------------------------

class TestReviewDecisions:
    def _two_req_case_with_evidence(self, client, db_session, rectification_institution):
        clue = _make_clue(db_session, rectification_institution.id)
        case_id = _create_case_api(client, clue.id).json()["id"]
        reqs = client.get(f"/api/rectifications/cases/{case_id}").json()["requirements"]
        for r in reqs:
            _submit_evidence(client, r["id"], "v1")
        return case_id, reqs

    def test_partial_approval_keeps_case_open_and_restores_share(
        self, client, db_session, rectification_institution
    ):
        case_id, reqs = self._two_req_case_with_evidence(
            client, db_session, rectification_institution
        )
        resp = client.post(f"/api/rectifications/cases/{case_id}/reviews", json={
            "decision": "部分通过",
            "reviewer": "复核员甲",
            "comment": "第一项通过，第二项材料不足退回",
            "item_decisions": [
                {"requirement_id": reqs[0]["id"], "decision": "通过"},
                {"requirement_id": reqs[1]["id"], "decision": "退回",
                 "comment": "缺少现场照片"},
            ],
        })
        assert resp.status_code == 200, resp.text
        detail = resp.json()
        assert detail["status"] == "整改中"
        statuses = {r["id"]: r["status"] for r in detail["requirements"]}
        assert statuses[reqs[0]["id"]] == "复核通过"
        assert statuses[reqs[1]["id"]] == "已退回"

        # 专项台账净额：-15 + 7.5 = -7.5（只恢复通过项份额）
        result = calculate_compliance_score(rectification_institution.id, db_session)
        items = {d.item: d.actual_score for d in result.deductions}
        assert items[ScoreItem.NO_QUICK_TRAINING] == 7.5

    def test_partial_approval_requires_at_least_one_approved(
        self, client, db_session, rectification_institution
    ):
        case_id, reqs = self._two_req_case_with_evidence(
            client, db_session, rectification_institution
        )
        resp = client.post(f"/api/rectifications/cases/{case_id}/reviews", json={
            "decision": "部分通过",
            "reviewer": "复核员甲",
            "comment": "两项都退回",
            "item_decisions": [
                {"requirement_id": r["id"], "decision": "退回"} for r in reqs
            ],
        })
        assert resp.status_code == 400
        assert "至少应包含一条" in resp.json()["detail"]

    def test_case_closes_only_when_all_requirements_approved(
        self, client, db_session, rectification_institution
    ):
        case_id, reqs = self._two_req_case_with_evidence(
            client, db_session, rectification_institution
        )
        # 先部分通过
        client.post(f"/api/rectifications/cases/{case_id}/reviews", json={
            "decision": "部分通过",
            "reviewer": "复核员甲",
            "comment": "一项通过一项退回",
            "item_decisions": [
                {"requirement_id": reqs[0]["id"], "decision": "通过"},
                {"requirement_id": reqs[1]["id"], "decision": "退回"},
            ],
        })
        # 第二项补交后通过
        _submit_evidence(client, reqs[1]["id"], "v2")
        resp = client.post(f"/api/rectifications/cases/{case_id}/reviews", json={
            "decision": "部分通过",
            "reviewer": "复核员甲",
            "comment": "补正后通过",
            "item_decisions": [
                {"requirement_id": reqs[1]["id"], "decision": "通过"},
            ],
        })
        # 单项通过走“部分通过”但此时全部要求均已通过 → 案件关闭、分值恢复
        detail = resp.json()
        assert detail["status"] == "已关闭"
        assert detail["closed_at"] is not None

        result = calculate_compliance_score(rectification_institution.id, db_session)
        assert result.total_score == 100.0
        # 两个维度均已恢复到满分，不再出现在扣分项中
        assert ScoreItem.NO_QUICK_TRAINING not in {d.item for d in result.deductions}
        assert ScoreItem.NO_VERIFIED_VIOLATION not in {d.item for d in result.deductions}

    def test_full_approval_one_click_close(self, client, db_session, rectification_institution):
        case_id, _reqs = self._two_req_case_with_evidence(
            client, db_session, rectification_institution
        )
        resp = client.post(f"/api/rectifications/cases/{case_id}/reviews", json={
            "decision": "通过",
            "reviewer": "复核组长",
            "comment": "全部材料合格",
        })
        assert resp.status_code == 200
        detail = resp.json()
        assert detail["status"] == "已关闭"
        assert all(r["status"] == "复核通过" for r in detail["requirements"])
        # 关闭后评分完全恢复
        result = calculate_compliance_score(rectification_institution.id, db_session)
        assert result.total_score == 100.0

    def test_full_approval_blocked_without_evidence(
        self, client, db_session, rectification_institution
    ):
        clue = _make_clue(db_session, rectification_institution.id)
        case_id = _create_case_api(client, clue.id).json()["id"]
        resp = client.post(f"/api/rectifications/cases/{case_id}/reviews", json={
            "decision": "通过",
            "reviewer": "复核组长",
            "comment": "无材料",
        })
        assert resp.status_code == 400
        assert "尚未提交整改证据" in resp.json()["detail"]

    def test_recurrence_reopens_case_and_reapplies_penalty(
        self, client, db_session, rectification_institution
    ):
        case_id, reqs = self._two_req_case_with_evidence(
            client, db_session, rectification_institution
        )
        client.post(f"/api/rectifications/cases/{case_id}/reviews", json={
            "decision": "通过", "reviewer": "复核组长", "comment": "通过关闭"
        })
        assert client.get(f"/api/rectifications/cases/{case_id}").json()["status"] == "已关闭"

        resp = client.post(f"/api/rectifications/cases/{case_id}/reviews", json={
            "decision": "认定复发",
            "reviewer": "复核组长",
            "comment": "现场复查发现第一项问题反弹",
            "item_decisions": [
                {"requirement_id": reqs[0]["id"], "decision": "认定复发"},
            ],
        })
        assert resp.status_code == 200, resp.text
        detail = resp.json()
        assert detail["status"] == "复发重开"
        statuses = {r["id"]: r["status"] for r in detail["requirements"]}
        assert statuses[reqs[0]["id"]] == "认定复发"
        assert statuses[reqs[1]["id"]] == "复核通过"

        result = calculate_compliance_score(rectification_institution.id, db_session)
        items = {d.item: d.actual_score for d in result.deductions}
        # 复发项份额7.5重新扣：15-7.5=7.5；案件级边际4重新扣：10-4=6
        assert items[ScoreItem.NO_QUICK_TRAINING] == 7.5
        assert items[ScoreItem.NO_VERIFIED_VIOLATION] == 6.0

    def test_recurrence_does_not_double_penalize_never_restored_item(
        self, client, db_session, rectification_institution
    ):
        """未通过复核、分值从未恢复的要求，认定复发不能再扣一次。"""
        clue = _make_clue(db_session, rectification_institution.id)
        case_id = _create_case_api(client, clue.id).json()["id"]
        reqs = client.get(f"/api/rectifications/cases/{case_id}").json()["requirements"]
        _submit_evidence(client, reqs[0]["id"], "v1")
        # 第一项从未被通过（无恢复），直接认定复发
        resp = client.post(f"/api/rectifications/cases/{case_id}/reviews", json={
            "decision": "认定复发",
            "reviewer": "复核组长",
            "comment": "复发",
            "item_decisions": [
                {"requirement_id": reqs[0]["id"], "decision": "认定复发"},
            ],
        })
        assert resp.status_code == 200
        adjs = db_session.query(ScoreAdjustment).filter(
            ScoreAdjustment.requirement_id == reqs[0]["id"],
            ScoreAdjustment.adjustment_type == ScoreAdjustmentType.RECURRENCE_PENALTY,
        ).count()
        assert adjs == 0


# ---------------------------------------------------------------------------
# 撤销错误复核
# ---------------------------------------------------------------------------

class TestReviewRevocation:
    def test_revoke_approval_reopens_case_and_reverses_score(
        self, client, db_session, rectification_institution
    ):
        clue = _make_clue(db_session, rectification_institution.id)
        case_id = _create_case_api(client, clue.id, n_requirements=1).json()["id"]
        req_id = client.get(f"/api/rectifications/cases/{case_id}").json()["requirements"][0]["id"]
        _submit_evidence(client, req_id, "v1")
        client.post(f"/api/rectifications/cases/{case_id}/reviews", json={
            "decision": "通过", "reviewer": "复核员甲", "comment": "通过"
        })
        case = client.get(f"/api/rectifications/cases/{case_id}").json()
        assert case["status"] == "已关闭"
        approval_review_id = next(
            r["id"] for r in case["requirements"][0]["reviews"] if r["decision"] == "通过"
        )

        resp = client.post(f"/api/rectifications/reviews/{approval_review_id}/revoke", json={
            "revoked_by": "科长",
            "reason": "复核材料造假，原通过决定错误",
        })
        assert resp.status_code == 200, resp.text
        assert resp.json()["is_active"] is False
        assert resp.json()["revoked_by"] == "科长"

        detail = client.get(f"/api/rectifications/cases/{case_id}").json()
        assert detail["status"] == "复发重开"
        req = detail["requirements"][0]
        assert req["status"] == "待复核"  # 已有证据，回退到待复核而非待整改
        assert req["effective_decision"] is None

        result = calculate_compliance_score(rectification_institution.id, db_session)
        items = {d.item: d.actual_score for d in result.deductions}
        assert items[ScoreItem.NO_QUICK_TRAINING] == 0.0
        assert items[ScoreItem.NO_VERIFIED_VIOLATION] == 6.0

        # 台账完整保留：扣分→恢复→撤销重扣（案件级） 与 扣分→恢复→撤销重扣（专项）
        types = [a.adjustment_type for a in db_session.query(ScoreAdjustment).all()]
        assert ScoreAdjustmentType.VIOLATION_PENALTY in types
        assert ScoreAdjustmentType.RESTORE in types
        assert ScoreAdjustmentType.REVOKE_APPROVAL_PENALTY in types

    def test_cannot_revoke_inactive_review_twice(
        self, client, db_session, rectification_institution
    ):
        clue = _make_clue(db_session, rectification_institution.id)
        case_id = _create_case_api(client, clue.id, n_requirements=1).json()["id"]
        req_id = client.get(f"/api/rectifications/cases/{case_id}").json()["requirements"][0]["id"]
        _submit_evidence(client, req_id, "v1")
        client.post(f"/api/rectifications/cases/{case_id}/reviews", json={
            "decision": "通过", "reviewer": "甲", "comment": "通过"
        })
        approval_review_id = next(
            r["id"] for r in client.get(f"/api/rectifications/cases/{case_id}").json()["requirements"][0]["reviews"]
            if r["decision"] == "通过"
        )
        client.post(f"/api/rectifications/reviews/{approval_review_id}/revoke",
                    json={"revoked_by": "科长", "reason": "错误"})
        resp = client.post(f"/api/rectifications/reviews/{approval_review_id}/revoke",
                           json={"revoked_by": "科长", "reason": "再次撤销"})
        assert resp.status_code == 400

    def test_revoke_recurrence_returns_penalty(
        self, client, db_session, rectification_institution
    ):
        clue = _make_clue(db_session, rectification_institution.id)
        case_id = _create_case_api(client, clue.id, n_requirements=1).json()["id"]
        req_id = client.get(f"/api/rectifications/cases/{case_id}").json()["requirements"][0]["id"]
        _submit_evidence(client, req_id, "v1")
        client.post(f"/api/rectifications/cases/{case_id}/reviews", json={
            "decision": "通过", "reviewer": "甲", "comment": "通过"
        })
        rec_resp = client.post(f"/api/rectifications/cases/{case_id}/reviews", json={
            "decision": "认定复发", "reviewer": "甲", "comment": "复发",
        })
        recur_review_id = next(
            r["id"] for r in rec_resp.json()["requirements"][0]["reviews"]
            if r["decision"] == "认定复发"
        )
        resp = client.post(f"/api/rectifications/reviews/{recur_review_id}/revoke", json={
            "revoked_by": "科长", "reason": "复发认定依据不足"
        })
        assert resp.status_code == 200
        assert any(
            a.adjustment_type == ScoreAdjustmentType.REVOKE_RECUR_RESTORE
            for a in db_session.query(ScoreAdjustment).all()
        )
        # 撤销复发后要求恢复通过态，案件重新关闭、分值恢复
        detail = client.get(f"/api/rectifications/cases/{case_id}").json()
        assert detail["status"] == "已关闭"
        assert calculate_compliance_score(rectification_institution.id, db_session).total_score == 100.0


# ---------------------------------------------------------------------------
# 逾期升级
# ---------------------------------------------------------------------------

class TestOverdueEscalation:
    def test_overdue_scan_escalates_once_and_recovers_on_approval(
        self, client, db_session, rectification_institution
    ):
        clue = _make_clue(db_session, rectification_institution.id)
        # 期限设在过去
        case_id = _create_case_api(client, clue.id, due_days=-5, n_requirements=1).json()["id"]
        req_id = client.get(f"/api/rectifications/cases/{case_id}").json()["requirements"][0]["id"]

        result1 = client.post("/api/rectifications/overdue-scan")
        assert result1.status_code == 200
        data1 = result1.json()
        assert data1["overdue_requirements"] >= 1
        assert case_id in data1["escalated_case_ids"]

        case = client.get(f"/api/rectifications/cases/{case_id}").json()
        assert case["escalated"] is True
        assert case["priority"] == "高"

        overdue_count_1 = db_session.query(ScoreAdjustment).filter(
            ScoreAdjustment.adjustment_type == ScoreAdjustmentType.OVERDUE_PENALTY
        ).count()
        # 再次扫描：同一要求不重复扣分
        client.post("/api/rectifications/overdue-scan")
        overdue_count_2 = db_session.query(ScoreAdjustment).filter(
            ScoreAdjustment.adjustment_type == ScoreAdjustmentType.OVERDUE_PENALTY
        ).count()
        assert overdue_count_1 == overdue_count_2

        result = calculate_compliance_score(rectification_institution.id, db_session)
        items = {d.item: d.actual_score for d in result.deductions}
        # 边际-4 与 逾期-2 均在“无已核实违规”维度：10-4-2=4
        assert items[ScoreItem.NO_VERIFIED_VIOLATION] == 4.0

        # 提交材料并通过 → 逾期扣分自动冲回
        _submit_evidence(client, req_id, "v1")
        client.post(f"/api/rectifications/cases/{case_id}/reviews", json={
            "decision": "通过", "reviewer": "甲", "comment": "逾期但整改合格"
        })
        result2 = calculate_compliance_score(rectification_institution.id, db_session)
        assert result2.total_score == 100.0
        assert any(
            a.adjustment_type == ScoreAdjustmentType.OVERDUE_VOID
            for a in db_session.query(ScoreAdjustment).all()
        )

    def test_future_due_not_escalated(self, client, db_session, rectification_institution):
        clue = _make_clue(db_session, rectification_institution.id)
        case_id = _create_case_api(client, clue.id, due_days=30, n_requirements=1).json()["id"]
        data = client.post("/api/rectifications/overdue-scan").json()
        assert case_id not in data["escalated_case_ids"]


# ---------------------------------------------------------------------------
# 评分台账与追溯
# ---------------------------------------------------------------------------

class TestScoreLedgerAndTraceability:
    def test_adjustments_listing(self, client, db_session, rectification_institution):
        clue = _make_clue(db_session, rectification_institution.id)
        case_id = _create_case_api(client, clue.id, n_requirements=1).json()["id"]
        resp = client.get("/api/rectifications/adjustments",
                          params={"institution_id": rectification_institution.id})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert all(a["case_id"] == case_id for a in data)
        assert sum(a["score_delta"] for a in data) == -19.0

    def test_traceability_endpoint_full_chain(
        self, client, db_session, rectification_institution
    ):
        inst_id = rectification_institution.id
        clue = _make_clue(db_session, inst_id)
        case_id = _create_case_api(client, clue.id, n_requirements=1).json()["id"]
        req_id = client.get(f"/api/rectifications/cases/{case_id}").json()["requirements"][0]["id"]

        # 立案后评分（低）
        client.post(f"/api/compliance-score/calculate/{inst_id}?generate_plans=false")
        _submit_evidence(client, req_id, "v1")
        client.post(f"/api/rectifications/cases/{case_id}/reviews", json={
            "decision": "通过", "reviewer": "复核员甲", "comment": "通过"
        })
        # 整改关闭后再评分（恢复）
        client.post(f"/api/compliance-score/calculate/{inst_id}?generate_plans=false")

        resp = client.get(f"/api/rectifications/traceability/institution/{inst_id}")
        assert resp.status_code == 200
        data = resp.json()

        # 历次评分：先低后高，可追溯变化
        history = data["score_history"]
        assert len(history) == 2
        assert history[0]["total_score"] == 100.0
        assert history[1]["total_score"] == 81.0

        # 调整台账完整
        types = {a["adjustment_type"] for a in data["adjustments"]}
        assert "违规扣分" in types
        assert "整改复核恢复" in types

        # 案件、原违规线索均可追溯，且案件内含整改要求→证据版本→复核决定完整链路
        assert len(data["rectification_cases"]) == 1
        case_detail = data["rectification_cases"][0]
        assert case_detail["id"] == case_id
        req_chain = case_detail["requirements"][0]
        assert req_chain["latest_evidence_version"] == 1
        assert req_chain["evidence_versions"][0]["file_name"] == "整改证据v1.pdf"
        assert any(r["decision"] == "通过" and r["is_active"]
                   for r in req_chain["reviews"])
        assert len(data["verified_clues"]) == 1
        assert data["verified_clues"][0]["id"] == clue.id

    def test_traceability_404(self, client):
        resp = client.get("/api/rectifications/traceability/institution/999999")
        assert resp.status_code == 404
