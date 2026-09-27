import unittest
from datetime import timedelta
from pathlib import Path

from src import bulletin, timeline
from src.decisions import (
    ACTION_AUTHORITY,
    evaluate_resume_conditions,
    file_manual_report,
    find_contradictions,
    issue_order,
    retract_manual_report,
    revoke_order,
)
from src.domain import load_domain
from src.errors import DomainError

DAY1 = "2026-07-12"
DAY2 = "2026-07-13"
TZ = "+08:00"


def t(day, hhmm):
    return f"{day}T{hhmm}:00{TZ}"


def load():
    return load_domain(Path("fixtures/domain.json"))


def state_at(domain, target, hhmm, day=DAY2):
    states = timeline.current_states(domain, timeline.parse_time(t(day, hhmm)))
    return states.get(target, {}).get("action")


class TimelineTest(unittest.TestCase):
    def test_warning_validity_crosses_midnight(self):
        domain = load()
        # W2 红色 22:00 生效，跨午夜到次日 02:00，23:00 覆盖 G1
        self.assertEqual(
            timeline.warning_at(domain, timeline.parse_time(t(DAY1, "23:00")), "G1")["level"], "红色"
        )
        # 雨带 23:30 移走后 G1 不再受预警；G3 跨午夜仍红色
        self.assertIsNone(timeline.warning_at(domain, timeline.parse_time(t(DAY2, "00:30")), "G1"))
        self.assertEqual(
            timeline.warning_at(domain, timeline.parse_time(t(DAY2, "00:30")), "G3")["level"], "红色"
        )
        # W3 04:00 到期后不再有效
        self.assertIsNone(timeline.warning_at(domain, timeline.parse_time(t(DAY2, "04:30")), "G3"))

    def test_warning_upgrade_and_rain_belt_movement(self):
        domain = load()
        # 22:30 W2 红色覆盖 G1、G2、G3；G4 无预警
        at = timeline.parse_time(t(DAY1, "22:30"))
        self.assertIsNone(timeline.warning_at(domain, at, "G4"))
        self.assertEqual(timeline.warning_at(domain, at, "G3")["level"], "红色")
        # 23:40 雨带移至 G3、G4，W3 取代 W2：G1 解除，G4 红色
        at = timeline.parse_time(t(DAY1, "23:40"))
        self.assertIsNone(timeline.warning_at(domain, at, "G1"))
        self.assertEqual(timeline.warning_at(domain, at, "G4")["level"], "红色")

    def test_sensor_offline_visible_in_series(self):
        domain = load()
        at = timeline.parse_time(t(DAY2, "02:30"))
        self.assertTrue(timeline.station_offline(domain, "P2", at))
        self.assertFalse(timeline.station_offline(domain, "P2", timeline.parse_time(t(DAY2, "04:30"))))
        self.assertTrue(timeline.rainfall_gap(domain, "P2", at - timedelta(hours=2), at))

    def test_events_append_only_with_original_times(self):
        domain = load()
        kinds = [(e["type"], e["id"]) for e in domain["events"]]
        # 误封命令 O6 与撤销 O6-revoked 都在历史中，各自时刻保留
        self.assertIn(("order", "O6"), kinds)
        self.assertIn(("order_revoked", "O6-revoked"), kinds)
        revoke = next(e for e in domain["events"] if e["id"] == "O6-revoked")
        original = next(e for e in domain["events"] if e["id"] == "O6")
        self.assertEqual(original["issued_at"], t(DAY1, "21:10"))
        self.assertEqual(revoke["time"], t(DAY1, "21:40"))
        # 撤销后 O6 不再产生状态
        self.assertIsNone(state_at(domain, "R3", "22:00", DAY1))
        # 误报 M1 与撤回记录都保留；撤回后上报不再生效
        self.assertTrue(any(e["id"] == "M1" for e in domain["events"]))
        self.assertFalse(
            any(r["id"] == "M1" for r in timeline.active_manual_reports(domain, timeline.parse_time(t(DAY2, "03:00"))))
        )

    def test_events_must_be_in_time_order(self):
        domain = load()
        with self.assertRaises(DomainError):
            timeline.append_event(domain, {"type": "warning", "id": "X", "time": t(DAY1, "01:00")})


class DecisionTest(unittest.TestCase):
    def test_authority_separation(self):
        self.assertEqual(ACTION_AUTHORITY["suspend_classes"], "education")
        self.assertEqual(ACTION_AUTHORITY["suspend_route"], "transport")
        self.assertEqual(ACTION_AUTHORITY["close_road"], "emergency")
        domain = load()
        traffic = {"name": "越权值班员", "authority": "transport"}
        with self.assertRaises(DomainError):
            issue_order(domain, traffic, "suspend_classes", "S1", t(DAY2, "11:00"), t(DAY2, "11:00"), "X1")
        # 教育部门只能管本区县学校
        edu1 = {"name": "教育值班员·临江县", "authority": "education", "district": "D1"}
        with self.assertRaises(DomainError):
            issue_order(domain, edu1, "suspend_classes", "S3", t(DAY2, "11:05"), t(DAY2, "11:10"), "X2")

    def test_contradiction_detected_before_publication(self):
        domain = load()
        # 20:08 S1 停课通知已签发，校车线路 B1 尚未停运：互相矛盾
        problems = find_contradictions(domain, timeline.parse_time(t(DAY1, "20:08")))
        self.assertTrue(any("S1" in p and "B1" in p for p in problems))
        # 20:25 全部联动后无矛盾
        self.assertEqual(find_contradictions(domain, timeline.parse_time(t(DAY1, "20:25"))), [])

    def test_reopen_blocked_by_rain_warning_and_false_report(self):
        domain = load()
        # O10 被拒绝的原因完整留痕：雨量超标、红色预警仍覆盖、人工上报未撤回
        rejected = next(e for e in domain["events"] if e["id"] == "O10-rejected")
        self.assertTrue(any("13.0 毫米" in r for r in rejected["reasons"]))
        self.assertTrue(any("M1" in r for r in rejected["reasons"]))
        self.assertTrue(any("红色" in r for r in rejected["reasons"]))
        # 解封被拒绝，R2 保持封闭
        self.assertEqual(state_at(domain, "R2", "02:05"), "close_road")

    def test_reopen_blocked_while_sensor_offline(self):
        domain = load()
        rejected = next(e for e in domain["events"] if e["id"] == "O11-rejected")
        self.assertTrue(any("离线" in r for r in rejected["reasons"]))
        # 传感器恢复且雨停后，R2 才于 05:00 解封
        self.assertEqual(state_at(domain, "R2", "04:55"), "close_road")
        self.assertEqual(state_at(domain, "R2", "05:00"), "reopen_road")

    def test_resume_conditions_written_and_evaluated(self):
        domain = load()
        # S2 在 07:00 的观测窗口累计 41 毫米，超过写明的 30 毫米上限
        result = evaluate_resume_conditions(domain, "school", "S2", timeline.parse_time(t(DAY2, "07:00")))
        self.assertFalse(result["ok"])
        self.assertTrue(any("41.0 毫米" in r for r in result["failures"]))
        # S1 条件满足后于 07:00 恢复
        self.assertEqual(state_at(domain, "S1", "06:55"), "suspend_classes")
        self.assertEqual(state_at(domain, "S1", "07:00"), "resume_classes")
        # S3 因 P2 离线被拒，数据补齐、条件满足后才于 10:00 恢复
        self.assertEqual(state_at(domain, "S3", "08:00"), "suspend_classes")
        self.assertEqual(state_at(domain, "S3", "10:00"), "resume_classes")

    def test_route_resume_blocked_while_school_still_suspended(self):
        domain = load()
        rejected = next(e for e in domain["events"] if e["id"] == "O17-rejected")
        self.assertTrue(any("S3" in r and "B1" in r for r in rejected["reasons"]))
        self.assertEqual(state_at(domain, "B1", "09:00"), "suspend_route")
        self.assertEqual(state_at(domain, "B1", "10:00"), "resume_route")

    def test_manual_report_lifecycle_preserves_times(self):
        domain = load()
        report = next(e for e in domain["events"] if e["id"] == "M1")
        retraction = next(e for e in domain["events"] if e["id"] == "M1-retracted")
        self.assertEqual(report["reported_at"], t(DAY2, "01:20"))
        self.assertEqual(retraction["time"], t(DAY2, "02:30"))
        self.assertEqual(retraction["reason"], "巡查确认未积水，属误报")
        at0130 = timeline.parse_time(t(DAY2, "01:30"))
        self.assertTrue(any(r["id"] == "M1" for r in timeline.active_manual_reports(domain, at0130)))


class BulletinTest(unittest.TestCase):
    def test_publish_gate_blocked_and_logged(self):
        domain = load()
        # 20:08 存在矛盾决定，发布被闸门拦截，拦截时刻与原因留痕
        blocked = next(e for e in domain["events"] if e["id"] == "PB-1")
        self.assertEqual(blocked["time"], t(DAY1, "20:08"))
        self.assertTrue(any("S1" in r and "B1" in r for r in blocked["reasons"]))
        # 矛盾消除后第 2 版成功发布
        published = next(e for e in domain["events"] if e["id"] == "BP-2")
        self.assertEqual(published["time"], t(DAY1, "20:35"))
        # 同一时刻重新走闸门仍被拦截（纯查询，不写事件）
        with self.assertRaises(DomainError):
            bulletin.build_bulletin(domain, 99, timeline.parse_time(t(DAY1, "20:08")))

    def test_single_version_for_all_roles_filtered_by_position(self):
        domain = load()
        snap = bulletin.build_bulletin(domain, 4, timeline.parse_time(t(DAY2, "07:30")))
        parent = bulletin.personalize(domain, snap, {"role": "parent", "cell": "G1"})
        driver = bulletin.personalize(domain, snap, {"role": "driver", "cell": "G2"})
        rescuer = bulletin.personalize(domain, snap, {"role": "rescuer", "cell": "G3"})
        # 家长、司机、救援人员看到同一版本
        self.assertEqual({parent["version"], driver["version"], rescuer["version"]}, {4})
        self.assertEqual({parent["issued_at"], driver["issued_at"], rescuer["issued_at"]}, {snap["issued_at"]})
        # 家长只收到与当前位置相交的当前有效行动：S1 已恢复、B1 仍停运；B2 不经过 G1
        actions = {(i["target"], i["action"]) for i in parent["items"]}
        self.assertIn(("S1", "resume_classes"), actions)
        self.assertIn(("B1", "suspend_route"), actions)
        self.assertNotIn(("B2", "suspend_route"), actions)
        # 司机视角包含道路状态；R3 已撤销，不在任何视角中
        road_targets = {i["target"] for i in driver["items"] if i["target_kind"] == "road"}
        self.assertIn("R2", road_targets)
        self.assertNotIn("R3", road_targets)
        # 避险场所按位置附带
        self.assertEqual(parent["shelters"], ["K1"])
        self.assertEqual(rescuer["shelters"], ["K3"])

    def test_current_actions_only_no_guesswork_from_sms(self):
        domain = load()
        snap = bulletin.build_bulletin(domain, 5, timeline.parse_time(t(DAY2, "10:30")))
        parent = bulletin.personalize(domain, snap, {"role": "parent", "cell": "G2"})
        # 全部恢复：G2 家长看到 S3 恢复上课、B1/B2 恢复运营，没有任何停课/停运遗留
        mapping = {i["target"]: i["action"] for i in parent["items"]}
        self.assertEqual(mapping.get("S3"), "resume_classes")
        self.assertEqual(mapping.get("B1"), "resume_route")
        self.assertFalse(
            any(i["action"] in ("suspend_classes", "suspend_route", "close_road") for i in parent["items"])
        )

    def test_position_outside_affected_zone_gets_nothing_restrictive(self):
        domain = load()
        # 20:35 只有 G1、G2 有行动；G3 位置的家长不收到任何限制类通知
        snap = bulletin.build_bulletin(domain, 6, timeline.parse_time(t(DAY1, "20:35")))
        parent = bulletin.personalize(domain, snap, {"role": "parent", "cell": "G3"})
        self.assertEqual(parent["items"], [])
        self.assertEqual(parent["warnings"], [])
        self.assertEqual(parent["shelters"], ["K3"])


class FreshScenarioTest(unittest.TestCase):
    """在空白资料上验证：越权撤销、误报撤回后解封的完整回路。"""

    def _mini(self):
        return {
            "cells": [
                {"id": "C1", "polygon": [[0, 0], [10, 0], [10, 10], [0, 10]]},
                {"id": "C2", "polygon": [[10, 0], [20, 0], [20, 10], [10, 10]]},
            ],
            "districts": [
                {"id": "D1", "name": "甲区", "polygon": [[0, 0], [10, 0], [10, 10], [0, 10]]},
                {"id": "D2", "name": "乙区", "polygon": [[10, 0], [20, 0], [20, 10], [10, 10]]},
            ],
            "schools": [{"id": "SC", "name": "甲区学校", "district": "D1", "point": [5, 5]}],
            "roads": [{"id": "RD", "name": "跨区干道", "polyline": [[2, 5], [18, 5]]}],
            "bus_routes": [
                {"id": "BR", "name": "校车线", "serves": ["SC"], "uses_roads": ["RD"], "polyline": [[5, 5], [15, 5]]}
            ],
            "hazard_sites": [],
            "shelters": [],
            "rainfall_stations": [
                {"id": "ST1", "cell": "C1", "point": [5, 5]},
                {"id": "ST2", "cell": "C2", "point": [15, 5]},
            ],
            "rainfall_series": [
                {"station_id": "ST1", "start": "2026-07-12T18:00:00+08:00", "interval_minutes": 60, "values": [0.0] * 12},
                {"station_id": "ST2", "start": "2026-07-12T18:00:00+08:00", "interval_minutes": 60, "values": [0.0] * 12},
            ],
            "events": [],
        }

    def test_wrong_authority_cannot_revoke(self):
        d = self._mini()
        emergency = {"name": "应急", "authority": "emergency"}
        traffic = {"name": "交通", "authority": "transport"}
        issue_order(d, emergency, "close_road", "RD", t(DAY1, "20:00"), t(DAY1, "20:05"), "Q1")
        with self.assertRaises(DomainError):
            revoke_order(d, traffic, "Q1", t(DAY1, "20:30"), "无权撤销")

    def test_false_report_then_retract_allows_reopen(self):
        d = self._mini()
        emergency = {"name": "应急", "authority": "emergency"}
        patrol = {"name": "巡查", "authority": "field"}
        issue_order(d, emergency, "close_road", "RD", t(DAY1, "20:00"), t(DAY1, "20:05"), "Q1")
        file_manual_report(d, patrol, "MR", "RD", t(DAY1, "21:00"), t(DAY2, "06:00"), "积水")
        # 误报未撤回时解封被拒
        with self.assertRaises(DomainError):
            issue_order(d, emergency, "reopen_road", "RD", t(DAY2, "05:00"), t(DAY2, "05:00"), "Q2")
        retract_manual_report(d, patrol, "MR", t(DAY2, "05:20"), "确认误报")
        order = issue_order(d, emergency, "reopen_road", "RD", t(DAY2, "05:30"), t(DAY2, "05:30"), "Q3")
        self.assertEqual(order["action"], "reopen_road")
        self.assertEqual(
            timeline.current_states(d, timeline.parse_time(t(DAY2, "06:00")))["RD"]["action"],
            "reopen_road",
        )


if __name__ == "__main__":
    unittest.main()
