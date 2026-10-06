"""整改案件、版本化证据、复核决定、评分调整与合规追溯测试。"""
from datetime import date, datetime, timedelta

import pytest

from app.models import (
    ClueType, ClueStatus, CluePriority, ScoreItem, ViolationClue,
    ScoreAdjustment, ScoreAdjustmentStatus, ScoreAdjustmentType,
    RectificationCaseStatus, RectificationItemStatus,
    RectificationDecision, ReviewResult,
)
from app.rectification_service import build_compliance_trace
from app.score_service import natural_violation_key


def _make_verified_clue(db_session, institution, *, clue_type=ClueType.QUICK_TRAINING,
                        title="速成班违规", practitioner_id=None, procedure_id=None):
    clue = ViolationClue(
        clue_type=clue_type,
        title=title,
        description="测试用已核实违规线索",
        institution_id=institution.id,
        practitioner_id=practitioner_id,
        procedure_id=procedure_id,
        source="专项检查",
        priority=CluePriority.HIGH,
        status=ClueStatus.VERIFIED,
        assignee="稽查员A",
        assigned_at=datetime(2024, 2, 1),
        conclusion="违规属实",
        verified_at=datetime(2024, 2, 5),
    )
    db_session.add(clue)
    db_session.flush()
    return clue


def _case_payload(clue_id, due_days=30, two_items=False):
    violations = [{
        "violation_summary": "人员未持证上岗",
        "requirements": [
            {
                "requirement": "停止无资质人员独立操作",
                "acceptance_criteria": "提供排班调整记录",
            },
        ],
    }]
    if two_items:
        violations[0]["requirements"].append({
            "requirement": "完成人员资质补证并备案",
            "acceptance_criteria": "提供执业证扫描件与备案回执",
        })
    return {
        "clue_id": clue_id,
        "title": "无证上岗整改案件",
        "score_item": ScoreItem.NO_QUICK_TRAINING.value,
        "due_date": (date.today() + timedelta(days=due_days)).isoformat(),
        "violations": violations,
        "created_by": "监管员甲",
    }


class TestRectificationLifecycle:
    def test_create_case_requires_verified_clue(self, client, db_session, test_multi_clue_institution):
        pending = ViolationClue(
            clue_type=ClueType.QUICK_TRAINING,
            title="待核实线索",
            description="不能立案",
            institution_id=test_multi_clue_institution.id,
            status=ClueStatus.PENDING,
        )
        db_session.add(pending)
        db_session.flush()

        resp = client.post("/api/rectification/cases", json=_case_payload(pending.id))
        assert resp.status_code == 400

    def test_case_split_items_deduction_and_due_dates(
        self, client, db_session, test_multi_clue_institution
    ):
        clue = _make_verified_clue(db_session, test_multi_clue_institution)
        payload = _case_payload(clue.id, due_days=15, two_items=True)

        resp = client.post("/api/rectification/cases", json=payload)
        assert resp.status_code == 201, resp.text
        case = resp.json()
        assert case["status"] == RectificationCaseStatus.OPEN.value
        assert len(case["items"]) == 2
        assert all(it["status"] == RectificationItemStatus.PENDING.value for it in case["items"])
        # 未显式给期限的要求继承案件期限
        assert all(it["due_date"] == case["due_date"] for it in case["items"])
        assert case["events"][0]["event_type"] == "立案"

        # 立案即产生一次违规扣分调整
        adjustments = db_session.query(ScoreAdjustment).filter_by(case_id=case["id"]).all()
        deducts = [a for a in adjustments if a.adjustment_type == ScoreAdjustmentType.DEDUCTION]
        assert len(deducts) == 1
        assert deducts[0].points == -5.0
        assert deducts[0].status == ScoreAdjustmentStatus.EFFECTIVE

    def test_duplicate_case_for_same_violation_rejected(
        self, client, db_session, test_multi_clue_institution
    ):
        clue = _make_verified_clue(db_session, test_multi_clue_institution)
        r1 = client.post("/api/rectification/cases", json=_case_payload(clue.id))
        assert r1.status_code == 201
        r2 = client.post("/api/rectification/cases", json=_case_payload(clue.id))
        assert r2.status_code == 409
        # 失败的立案不能留下第二条扣分
        deducts = db_session.query(ScoreAdjustment).filter(
            ScoreAdjustment.violation_key == natural_violation_key(clue)
        ).all()
        assert len(deducts) == 1

    def test_versioned_evidence(self, client, db_session, test_multi_clue_institution):
        clue = _make_verified_clue(db_session, test_multi_clue_institution)
        case = client.post("/api/rectification/cases",
                           json=_case_payload(clue.id, two_items=True)).json()
        item_id = case["items"][0]["id"]

        r1 = client.post(f"/api/rectification/items/{item_id}/evidence", json={
            "material_name": "排班调整通知v1", "content": "初版材料", "submitted_by": "机构专员"
        })
        assert r1.status_code == 201
        assert r1.json()["version"] == 1

        r2 = client.post(f"/api/rectification/items/{item_id}/evidence", json={
            "material_name": "排班调整通知v2", "content": "补充盖章版", "submitted_by": "机构专员"
        })
        assert r2.json()["version"] == 2

        detail = client.get(f"/api/rectification/cases/{case['id']}").json()
        item = next(i for i in detail["items"] if i["id"] == item_id)
        assert item["current_evidence_id"] == r2.json()["id"]
        assert [e["version"] for e in item["evidences"]] == [1, 2]
        # 历史版本内容不可变
        assert item["evidences"][0]["content"] == "初版材料"


class TestReviewAndScoring:
    def _open_case(self, client, db_session, inst, two_items=True):
        clue = _make_verified_clue(db_session, inst)
        case = client.post("/api/rectification/cases",
                           json=_case_payload(clue.id, two_items=two_items)).json()
        return clue, case

    def test_review_requires_evidence(self, client, db_session, test_multi_clue_institution):
        _, case = self._open_case(client, db_session, test_multi_clue_institution)
        resp = client.post(f"/api/rectification/cases/{case['id']}/reviews", json={
            "decision": RectificationDecision.PARTIAL_PASS.value,
            "reviewer": "复核人乙",
            "item_results": [{
                "item_id": case["items"][0]["id"],
                "result": ReviewResult.PASSED.value,
            }],
        })
        assert resp.status_code == 400

    def test_partial_pass_does_not_restore(self, client, db_session, test_multi_clue_institution):
        clue, case = self._open_case(client, db_session, test_multi_clue_institution)
        i1, i2 = case["items"]

        client.post(f"/api/rectification/items/{i1['id']}/evidence",
                    json={"material_name": "排班记录"})
        client.post(f"/api/rectification/items/{i2['id']}/evidence",
                    json={"material_name": "补证申请"})

        resp = client.post(f"/api/rectification/cases/{case['id']}/reviews", json={
            "decision": RectificationDecision.PARTIAL_PASS.value,
            "reviewer": "复核人乙",
            "comment": "第一条达标，第二条材料不全",
            "item_results": [
                {"item_id": i1["id"], "result": ReviewResult.PASSED.value},
                {"item_id": i2["id"], "result": ReviewResult.RETURNED.value,
                 "comment": "缺备案回执"},
            ],
        })
        assert resp.status_code == 201
        detail = client.get(f"/api/rectification/cases/{case['id']}").json()
        assert detail["status"] == RectificationCaseStatus.PARTIALLY_PASSED.value
        assert detail["items"][0]["status"] == RectificationItemStatus.PASSED.value
        assert detail["items"][1]["status"] == RectificationItemStatus.RETURNED.value

        restores = [a for a in db_session.query(ScoreAdjustment).filter_by(
            case_id=case["id"]).all()
            if a.adjustment_type == ScoreAdjustmentType.RESTORE]
        assert restores == []  # 未全部通过，不恢复分数

    def test_close_requires_all_passed(self, client, db_session, test_multi_clue_institution):
        _, case = self._open_case(client, db_session, test_multi_clue_institution)
        resp = client.post(f"/api/rectification/cases/{case['id']}/reviews", json={
            "decision": RectificationDecision.CLOSE.value,
            "reviewer": "复核人乙",
        })
        assert resp.status_code == 400

    def test_full_pass_close_restores_score(self, client, db_session, test_multi_clue_institution):
        inst = test_multi_clue_institution
        clue, case = self._open_case(client, db_session, inst, two_items=False)
        item = case["items"][0]

        before = client.post(f"/api/compliance-score/calculate/{inst.id}?generate_plans=false").json()
        assert before["total_score"] <= 95.0  # 违规扣 5 分

        client.post(f"/api/rectification/items/{item['id']}/evidence",
                    json={"material_name": "整改完成证明"})
        review = client.post(f"/api/rectification/cases/{case['id']}/reviews", json={
            "decision": RectificationDecision.PARTIAL_PASS.value,
            "reviewer": "复核人乙",
            "item_results": [{"item_id": item["id"], "result": ReviewResult.PASSED.value}],
        }).json()

        closed = client.post(f"/api/rectification/cases/{case['id']}/reviews", json={
            "decision": RectificationDecision.CLOSE.value,
            "reviewer": "复核组长",
            "comment": "现场复核通过",
        })
        assert closed.status_code == 201
        detail = client.get(f"/api/rectification/cases/{case['id']}").json()
        assert detail["status"] == RectificationCaseStatus.CLOSED.value
        assert detail["closed_at"] is not None

        after = client.post(f"/api/compliance-score/calculate/{inst.id}?generate_plans=false").json()
        assert after["total_score"] == 100.0
        # 评分说明里能看到独立的恢复调整
        assert any("整改恢复" in d["reason"] for d in after["deduction_list"])

    def test_recurrence_re_deducts_only_once(self, client, db_session, test_multi_clue_institution):
        inst = test_multi_clue_institution
        _, case = self._open_case(client, db_session, inst, two_items=False)
        item = case["items"][0]
        client.post(f"/api/rectification/items/{item['id']}/evidence",
                    json={"material_name": "整改证明"})
        client.post(f"/api/rectification/cases/{case['id']}/reviews", json={
            "decision": RectificationDecision.PARTIAL_PASS.value,
            "reviewer": "复核人乙",
            "item_results": [{"item_id": item["id"], "result": ReviewResult.PASSED.value}],
        })
        restored = client.post(
            f"/api/compliance-score/calculate/{inst.id}?generate_plans=false").json()
        assert restored["total_score"] == 100.0

        # 认定复发：恢复的 5 分重新扣回
        r1 = client.post(f"/api/rectification/cases/{case['id']}/reviews", json={
            "decision": RectificationDecision.RECURRENCE.value,
            "reviewer": "复核组长",
            "comment": "现场复查发现再次违规",
            "item_results": [{"item_id": item["id"], "result": ReviewResult.RECURRED.value}],
        })
        assert r1.status_code == 201
        recurred_score = client.post(
            f"/api/compliance-score/calculate/{inst.id}?generate_plans=false").json()
        assert recurred_score["total_score"] == 95.0

        # 再次认定复发不能重复扣分
        client.post(f"/api/rectification/cases/{case['id']}/reviews", json={
            "decision": RectificationDecision.RECURRENCE.value,
            "reviewer": "复核组长",
            "item_results": [{"item_id": item["id"], "result": ReviewResult.RECURRED.value}],
        })
        recur_adjustments = db_session.query(ScoreAdjustment).filter(
            ScoreAdjustment.case_id == case["id"],
            ScoreAdjustment.adjustment_type == ScoreAdjustmentType.RECURRENCE_DEDUCTION,
            ScoreAdjustment.status == ScoreAdjustmentStatus.EFFECTIVE,
        ).all()
        assert len(recur_adjustments) == 1

    def test_repeated_pass_review_no_double_restore(
        self, client, db_session, test_multi_clue_institution
    ):
        inst = test_multi_clue_institution
        _, case = self._open_case(client, db_session, inst, two_items=False)
        item = case["items"][0]
        client.post(f"/api/rectification/items/{item['id']}/evidence",
                    json={"material_name": "整改证明"})
        review_body = {
            "decision": RectificationDecision.PARTIAL_PASS.value,
            "reviewer": "复核人乙",
            "item_results": [{"item_id": item["id"], "result": ReviewResult.PASSED.value}],
        }
        client.post(f"/api/rectification/cases/{case['id']}/reviews", json=review_body)
        # 已通过后再次给出同样的通过复核，不应产生第二条恢复加分
        client.post(f"/api/rectification/cases/{case['id']}/reviews", json=review_body)
        restores = db_session.query(ScoreAdjustment).filter(
            ScoreAdjustment.case_id == case["id"],
            ScoreAdjustment.adjustment_type == ScoreAdjustmentType.RESTORE,
            ScoreAdjustment.status == ScoreAdjustmentStatus.EFFECTIVE,
        ).all()
        assert len(restores) == 1
        score = client.post(
            f"/api/compliance-score/calculate/{inst.id}?generate_plans=false").json()
        assert score["total_score"] == 100.0


class TestRevoke:
    def test_revoke_review_rolls_back_item_and_score(
        self, client, db_session, test_multi_clue_institution
    ):
        inst = test_multi_clue_institution
        clue = _make_verified_clue(db_session, inst)
        case = client.post("/api/rectification/cases",
                           json=_case_payload(clue.id, two_items=False)).json()
        item = case["items"][0]
        client.post(f"/api/rectification/items/{item['id']}/evidence",
                    json={"material_name": "整改证明"})
        review = client.post(f"/api/rectification/cases/{case['id']}/reviews", json={
            "decision": RectificationDecision.PARTIAL_PASS.value,
            "reviewer": "复核人乙",
            "item_results": [{"item_id": item["id"], "result": ReviewResult.PASSED.value}],
        }).json()
        client.post(f"/api/rectification/cases/{case['id']}/reviews", json={
            "decision": RectificationDecision.CLOSE.value,
            "reviewer": "复核组长",
        })

        close_reviews = [r for r in client.get(
            f"/api/rectification/cases/{case['id']}").json()["reviews"]
            if r["decision"] == RectificationDecision.CLOSE.value]
        # 撤销关闭 → 案件重开
        client.post(f"/api/rectification/reviews/{close_reviews[0]['id']}/revoke",
                    json={"reason": "关闭流程误操作"})
        detail = client.get(f"/api/rectification/cases/{case['id']}").json()
        assert detail["status"] != RectificationCaseStatus.CLOSED.value
        assert detail["closed_at"] is None

        # 撤销错误的通过复核 → 要求回滚、恢复调整作废
        resp = client.post(f"/api/rectification/reviews/{review['id']}/revoke",
                           json={"reason": "证据造假，撤销通过", "reviewer": "纪检"})
        assert resp.status_code == 200
        detail = client.get(f"/api/rectification/cases/{case['id']}").json()
        assert detail["items"][0]["status"] == RectificationItemStatus.SUBMITTED.value

        restore = db_session.query(ScoreAdjustment).filter_by(review_id=review["id"]).one()
        assert restore.status == ScoreAdjustmentStatus.REVOKED
        assert restore.revoked_at is not None

        score = client.post(
            f"/api/compliance-score/calculate/{inst.id}?generate_plans=false").json()
        assert score["total_score"] == 95.0

        # 不能重复撤销
        again = client.post(f"/api/rectification/reviews/{review['id']}/revoke",
                            json={"reason": "再次撤销"})
        assert again.status_code == 400


    def test_recurrence_after_close_reopens_and_deducts(
        self, client, db_session, test_multi_clue_institution
    ):
        inst = test_multi_clue_institution
        clue = _make_verified_clue(db_session, inst)
        case = client.post("/api/rectification/cases",
                           json=_case_payload(clue.id, two_items=False)).json()
        item = case["items"][0]
        client.post(f"/api/rectification/items/{item['id']}/evidence",
                    json={"material_name": "整改证明"})
        client.post(f"/api/rectification/cases/{case['id']}/reviews", json={
            "decision": RectificationDecision.PARTIAL_PASS.value,
            "reviewer": "复核人乙",
            "item_results": [{"item_id": item["id"], "result": ReviewResult.PASSED.value}],
        })
        client.post(f"/api/rectification/cases/{case['id']}/reviews", json={
            "decision": RectificationDecision.CLOSE.value,
            "reviewer": "复核组长",
        })
        assert client.get(f"/api/rectification/cases/{case['id']}").json()["status"] == \
            RectificationCaseStatus.CLOSED.value

        # 关闭后认定复发：案件自动重开并重新扣分
        resp = client.post(f"/api/rectification/cases/{case['id']}/reviews", json={
            "decision": RectificationDecision.RECURRENCE.value,
            "reviewer": "复核组长",
            "comment": "举报复查确认复发",
            "item_results": [{"item_id": item["id"], "result": ReviewResult.RECURRED.value}],
        })
        assert resp.status_code == 201
        detail = client.get(f"/api/rectification/cases/{case['id']}").json()
        assert detail["status"] == RectificationCaseStatus.RECURRED.value
        assert detail["closed_at"] is None
        score = client.post(
            f"/api/compliance-score/calculate/{inst.id}?generate_plans=false").json()
        assert score["total_score"] == 95.0


class TestOverdue:
    def test_overdue_escalation_idempotent(self, client, db_session, test_multi_clue_institution):
        inst = test_multi_clue_institution
        clue = _make_verified_clue(db_session, inst)
        # 期限已过
        payload = _case_payload(clue.id, two_items=False)
        payload["due_date"] = (date.today() - timedelta(days=1)).isoformat()
        payload["violations"][0]["requirements"][0]["due_date"] = payload["due_date"]
        case = client.post("/api/rectification/cases", json=payload).json()

        r1 = client.post("/api/rectification/escalate-overdue")
        assert r1.status_code == 200
        assert case["id"] in r1.json()["escalated_cases"]
        detail = client.get(f"/api/rectification/cases/{case['id']}").json()
        assert detail["status"] == RectificationCaseStatus.OVERDUE.value
        assert detail["escalated"] is True

        # 逾期处罚 -3 一次
        penalties = db_session.query(ScoreAdjustment).filter(
            ScoreAdjustment.case_id == case["id"],
            ScoreAdjustment.adjustment_type == ScoreAdjustmentType.OVERDUE_PENALTY,
        ).all()
        assert len(penalties) == 1 and penalties[0].points == -3.0

        # 再次执行升级不重复扣分
        r2 = client.post("/api/rectification/escalate-overdue")
        assert r2.json()["escalated_cases"] == []
        assert db_session.query(ScoreAdjustment).filter(
            ScoreAdjustment.case_id == case["id"],
            ScoreAdjustment.adjustment_type == ScoreAdjustmentType.OVERDUE_PENALTY,
        ).count() == 1


class TestMultiClueDedup:
    def test_linked_clue_not_double_deducted(self, client, db_session, test_multi_clue_institution):
        inst = test_multi_clue_institution
        clue1 = _make_verified_clue(db_session, inst, title="违规问题A")
        case = client.post("/api/rectification/cases",
                           json=_case_payload(clue1.id, two_items=False)).json()
        vkey = natural_violation_key(clue1)

        # 另一条不同自然键的已核实线索引用同一违规结论
        clue2 = _make_verified_clue(
            db_session, inst, title="违规问题A的补充线索", practitioner_id=999)
        assert natural_violation_key(clue2) != vkey

        score_before_link = client.post(
            f"/api/compliance-score/calculate/{inst.id}?generate_plans=false").json()
        # 未关联前第二条违规会被单独扣分（该项 0 分）
        assert score_before_link["total_score"] <= 90.0

        link = client.post(f"/api/rectification/cases/{case['id']}/link-clue", json={
            "clue_id": clue2.id,
            "violation_key": vkey,
            "violation_summary": "同一问题的另一条线索",
        })
        assert link.status_code == 201

        score_after_link = client.post(
            f"/api/compliance-score/calculate/{inst.id}?generate_plans=false").json()
        # 关联后只按一项违规扣 5 分
        assert score_after_link["total_score"] == 95.0

        # 重复引用被拒绝
        dup = client.post(f"/api/rectification/cases/{case['id']}/link-clue", json={
            "clue_id": clue2.id, "violation_key": vkey,
        })
        assert dup.status_code == 400

        # 不能引用到别的案件的违规结论
        clue3 = _make_verified_clue(db_session, inst, title="违规问题B", procedure_id=777)
        other = client.post("/api/rectification/cases",
                            json=_case_payload(clue3.id, two_items=False)).json()
        bad = client.post(f"/api/rectification/cases/{other['id']}/link-clue", json={
            "clue_id": clue2.id, "violation_key": vkey,
        })
        assert bad.status_code == 400


class TestComplianceTrace:
    def test_trace_score_to_violation_evidence_review_history(
        self, client, db_session, test_multi_clue_institution
    ):
        inst = test_multi_clue_institution
        clue = _make_verified_clue(db_session, inst)

        client.post(f"/api/compliance-score/calculate/{inst.id}?generate_plans=false")

        case = client.post("/api/rectification/cases",
                           json=_case_payload(clue.id, two_items=False)).json()
        item = case["items"][0]
        client.post(f"/api/rectification/items/{item['id']}/evidence",
                    json={"material_name": "v1材料"})
        client.post(f"/api/rectification/items/{item['id']}/evidence",
                    json={"material_name": "v2材料"})
        client.post(f"/api/rectification/cases/{case['id']}/reviews", json={
            "decision": RectificationDecision.PARTIAL_PASS.value,
            "reviewer": "复核人乙",
            "comment": "验收通过",
            "item_results": [{"item_id": item["id"], "result": ReviewResult.PASSED.value}],
        })
        client.post(f"/api/compliance-score/calculate/{inst.id}?generate_plans=false")

        resp = client.get(f"/api/institutions/{inst.id}/compliance-trace")
        assert resp.status_code == 200
        trace = resp.json()

        assert trace["institution_name"] == inst.name
        assert len(trace["violations"]) >= 1
        v = next(v for v in trace["violations"] if v["case_id"] == case["id"])
        # 原违规线索可追溯
        assert clue.id in v["clue_ids"]
        # 整改要求与版本化证据
        req = v["requirements"][0]
        assert req["current_evidence_version"] == 2
        assert req["current_evidence_name"] == "v2材料"
        assert len(req["evidence_versions"]) == 2
        # 复核决定
        assert req["last_review_result"] == ReviewResult.PASSED.value
        assert req["last_reviewer"] == "复核人乙"
        # 独立调整记录：扣分 + 恢复
        types = {a["adjustment_type"] for a in v["adjustments"]}
        assert "违规扣分" in types and "整改恢复" in types
        # 历次评分变化至少两条
        assert len(trace["score_history"]) >= 2
        scores = [s["total_score"] for s in trace["score_history"]]
        assert scores[-1] == 100.0
        assert any(s <= 95.0 for s in scores)
        # 历史评分工时点的调整记录可回溯
        first = trace["score_history"][0]
        assert first["deduction_list"] is not None
