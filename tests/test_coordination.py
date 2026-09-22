"""覆盖需求叙述中的每个关键场景。

时间线（虚构）：
- 21:55 教育部门对云溪一小发停课；22:05 交通部门停运其校车
- 23:40 跨区盘山公路因积水+滑坡隐患封闭
- 雨带 20:00~次日02:00 跨午夜；之后东移到临江县
- 22:30 云溪2号传感器离线；23:10 人工上报 lj2 999mm 为误报
- 清晨 06:55 起凭写明的观测条件依次解封、恢复发车、恢复上课
"""

import unittest
from datetime import datetime
from pathlib import Path

from src.coordination import CoordinationCenter, PublishBlocked, _World
from src.features import (
    WaterlogRecord,
    make_geohazard,
    make_road,
    make_route,
    make_school,
    make_shelter,
)
from src.geometry import Grid
from src.orders import (
    EDUCATION,
    TRANSPORT,
    Order,
    OrderRegistry,
    ResumeCondition,
)
from src.rainfall import (
    QUALITY_FALSE_REPORT,
    QUALITY_OFFLINE,
    Observation,
    RainBand,
    latest_confirmation,
    level_at,
    window_sum,
)
from src.scenario import load_scenario


def dt(s):
    return datetime.fromisoformat(s)


def build_world():
    grid = Grid.build(
        {
            "yx1": "云溪区", "yx2": "云溪区", "yx3": "云溪区", "yx4": "云溪区",
            "lj1": "临江县", "lj2": "临江县", "lj3": "临江县",
        }
    )
    school_s1 = make_school(grid, "S1", "云溪一小", ["yx1", "yx2"])
    school_s2 = make_school(grid, "S2", "临江二小", ["lj2"])
    route_r1 = make_route(grid, "R1", "一小1号线", ["yx2", "yx3"], "S1")
    road_d1 = make_road(grid, "D1", "云溪大道", ["yx1", "yx2"])
    road_d2 = make_road(grid, "D2", "云临盘山公路", ["yx3", "lj1"])
    geo_g1 = make_geohazard(grid, "G1", "云临山口滑坡点", ["yx3"])
    shelter_h1 = make_shelter(grid, "H1", "云溪体育馆", ["yx1"])
    shelter_h2 = make_shelter(grid, "H2", "临江一中", ["lj1"])

    bands = [
        RainBand("B1", 3, frozenset({"yx1", "yx2", "yx3", "lj1"}),
                 dt("2026-09-21T20:00:00+08:00"), dt("2026-09-22T02:00:00+08:00"),
                 dt("2026-09-21T19:30:00+08:00")),
        RainBand("B2", 2, frozenset({"lj1", "lj2", "lj3"}),
                 dt("2026-09-22T01:30:00+08:00"), dt("2026-09-22T05:00:00+08:00"),
                 dt("2026-09-22T01:20:00+08:00")),
    ]
    observations = [
        Observation("ST-YX1", "yx1", dt("2026-09-21T21:50:00+08:00"), 22.0),
        Observation("ST-YX2", "yx2", dt("2026-09-21T21:55:00+08:00"), 18.0),
        Observation("ST-YX2", "yx2", dt("2026-09-21T22:30:00+08:00"), 0.0,
                    quality=QUALITY_OFFLINE),
        Observation("MAN-LJ2", "lj2", dt("2026-09-21T23:10:00+08:00"), 999.0,
                    source="manual", quality=QUALITY_FALSE_REPORT),
        Observation("ST-YX3", "yx3", dt("2026-09-21T23:35:00+08:00"), 35.0),
        Observation("ST-LJ1", "lj1", dt("2026-09-22T00:40:00+08:00"), 28.0),
        Observation("ST-YX3", "yx3", dt("2026-09-22T06:35:00+08:00"), 0.8),
        Observation("ST-YX1", "yx1", dt("2026-09-22T06:30:00+08:00"), 1.2),
        Observation("ST-LJ1", "lj1", dt("2026-09-22T06:40:00+08:00"), 0.6),
        Observation("ST-YX2", "yx2", dt("2026-09-22T06:40:00+08:00"), 0.5),
    ]
    waterlogs = [
        WaterlogRecord("D1", dt("2026-09-21T22:00:00+08:00"), 40.0,
                       quality=QUALITY_FALSE_REPORT),
        WaterlogRecord("D2", dt("2026-09-21T23:30:00+08:00"), 32.0),
        WaterlogRecord("D2", dt("2026-09-22T06:50:00+08:00"), 5.0),
    ]
    world = _World(
        bands=bands, observations=observations, waterlogs=waterlogs,
        schools={"S1": school_s1, "S2": school_s2},
        routes={"R1": route_r1},
        roads={"D1": road_d1, "D2": road_d2},
        shelters={"H1": shelter_h1, "H2": shelter_h2},
        geohazards={"G1": geo_g1},
        cell_district=dict(grid.cell_districts),
    )
    return grid, world


def night_orders():
    return [
        Order("O1", EDUCATION, "suspend_class", "S1", frozenset({"yx1", "yx2"}),
              dt("2026-09-21T21:55:00+08:00"), "暴雨橙色预警覆盖学校"),
        Order("O2", TRANSPORT, "suspend_bus", "R1", frozenset({"yx2", "yx3"}),
              dt("2026-09-21T22:05:00+08:00"), "学校停课且临近灾害点"),
        Order("O3", TRANSPORT, "close_road", "D2", frozenset({"yx3", "lj1"}),
              dt("2026-09-21T23:40:00+08:00"), "积水32厘米临滑坡点"),
    ]


RECOVER_COND_RAIN = ResumeCondition(max_rain_1h_mm=5.0, sensor_fresh_minutes=60)
RECOVER_COND_ROAD = ResumeCondition(max_rain_1h_mm=5.0, max_water_cm=10.0,
                                    sensor_fresh_minutes=60)


def morning_orders():
    return [
        Order("O4", TRANSPORT, "open_road", "D2", frozenset({"yx3", "lj1"}),
              dt("2026-09-22T06:55:00+08:00"), "雨带东移申请解封",
              revokes="O3", condition=RECOVER_COND_ROAD),
        Order("O5", TRANSPORT, "resume_bus", "R1", frozenset({"yx2", "yx3"}),
              dt("2026-09-22T07:05:00+08:00"), "道路解封申请恢复发车",
              revokes="O2", condition=RECOVER_COND_RAIN),
        Order("O6", EDUCATION, "resume_class", "S1", frozenset({"yx1", "yx2"}),
              dt("2026-09-22T07:10:00+08:00"), "雨情缓解申请复课",
              revokes="O1", condition=RECOVER_COND_RAIN),
    ]


class RainfallBasicsTest(unittest.TestCase):
    def test_cross_midnight_band_still_active(self):
        _, world = build_world()
        # 午夜 00:30，B1（20:00~次日02:00）仍覆盖云溪
        self.assertEqual(level_at(world.bands, frozenset({"yx2"}), dt("2026-09-22T00:30:00+08:00")), 3)

    def test_band_moves_east_only_intersecting_cells_change(self):
        _, world = build_world()
        # 01:40 云溪3号格点仍在旧雨带里
        self.assertEqual(level_at(world.bands, frozenset({"yx3"}), dt("2026-09-22T01:40:00+08:00")), 3)
        # 02:30 旧雨带结束、新雨带只在临江：云溪格点归零，临江2号为2级
        self.assertEqual(level_at(world.bands, frozenset({"yx2"}), dt("2026-09-22T02:30:00+08:00")), 0)
        self.assertEqual(level_at(world.bands, frozenset({"lj2"}), dt("2026-09-22T02:30:00+08:00")), 2)
        # 临江的新雨带与云溪一小格点不相交，学校处无预警
        self.assertEqual(level_at(world.bands, frozenset({"yx1", "yx2"}),
                                  dt("2026-09-22T02:00:00+08:00")), 0)

    def test_offline_and_false_report_kept_but_excluded(self):
        _, world = build_world()
        at = dt("2026-09-21T22:35:00+08:00")
        # 离线读数保留在记录中，但近1小时雨量只统计可用值（18mm，不含离线0）
        self.assertEqual(window_sum(world.observations, frozenset({"yx2"}), at, 1), 18.0)
        # 最新一条是离线状态 → 不能充当"雨已停"的确认
        self.assertIsNone(latest_confirmation(world.observations, "yx2",
                                              dt("2026-09-21T22:05:00+08:00"), at))
        # 人工误报 999mm 永不进入统计
        self.assertEqual(window_sum(world.observations, frozenset({"lj2"}),
                                    dt("2026-09-21T23:40:00+08:00"), 1), 0.0)
        # 离线/误报原始记录仍可追溯
        self.assertEqual(len(world.observations), 10)


class GeometryTest(unittest.TestCase):
    def test_cross_district_road(self):
        grid, world = build_world()
        self.assertTrue(world.roads["D2"].is_cross_district)
        self.assertEqual(world.roads["D2"].districts, frozenset({"云溪区", "临江县"}))
        self.assertFalse(world.roads["D1"].is_cross_district)
        self.assertTrue(world.routes["R1"].is_cross_district is False)

    def test_unknown_cell_rejected(self):
        grid, _ = build_world()
        with self.assertRaises(ValueError):
            grid.require_cells(["zz9"], "测试要素")


class AuthorityTest(unittest.TestCase):
    def test_each_action_has_responsible_agency(self):
        with self.assertRaises(ValueError):
            # 教育部门无权停运校车
            Order("X", EDUCATION, "suspend_bus", "R1", frozenset({"yx2"}),
                  dt("2026-09-21T22:00:00+08:00"), "越权")

    def test_resume_requires_written_condition_and_target(self):
        with self.assertRaises(ValueError):
            Order("X", EDUCATION, "resume_class", "S1", frozenset({"yx1"}),
                  dt("2026-09-22T07:00:00+08:00"), "无条件复课")
        with self.assertRaises(ValueError):
            Order("X", TRANSPORT, "open_road", "D2", frozenset({"yx3"}),
                  dt("2026-09-22T07:00:00+08:00"), "解封不指原命令",
                  condition=RECOVER_COND_ROAD)


class ContradictionTest(unittest.TestCase):
    def test_school_suspended_but_bus_still_running_blocks_publish(self):
        """叙述开场：停课已发，校车系统却提示正常发车 → 发布前整批拦截。"""
        _, world = build_world()
        center = CoordinationCenter(OrderRegistry(), world)
        o1 = night_orders()[0]
        center.registry.add(o1)
        with self.assertRaises(PublishBlocked) as ctx:
            center.publish(dt("2026-09-21T21:56:00+08:00"), [o1])
        self.assertIn("校车线路R1仍提示发车", str(ctx.exception))

    def test_route_through_closed_road_must_suspend(self):
        _, world = build_world()
        center = CoordinationCenter(OrderRegistry(), world)
        o1, _, o3 = night_orders()
        for o in (o1, o3):
            center.registry.add(o)
        with self.assertRaises(PublishBlocked) as ctx:
            center.publish(dt("2026-09-21T23:50:00+08:00"), [o1, o3])
        msg = str(ctx.exception)
        self.assertTrue("R1仍提示发车" in msg or "途经已封闭道路D2" in msg)

    def test_directly_opposite_decisions_blocked(self):
        _, world = build_world()
        center = CoordinationCenter(OrderRegistry(), world)
        o3 = night_orders()[2]
        # 夜间观测条件不满足时强行解封：既矛盾又不满足条件
        bad_open = Order("O3B", TRANSPORT, "open_road", "D2", frozenset({"yx3", "lj1"}),
                         dt("2026-09-21T23:45:00+08:00"), "夜间抢通",
                         revokes="O3", condition=RECOVER_COND_ROAD)
        center.registry.add(o3)
        center.registry.add(bad_open)
        with self.assertRaises(PublishBlocked):
            center.publish(dt("2026-09-21T23:46:00+08:00"), [o3, bad_open])


class FullNightToMorningTest(unittest.TestCase):
    def setUp(self):
        self.grid, self.world = build_world()
        self.center = CoordinationCenter(OrderRegistry(), self.world)
        for o in night_orders() + morning_orders():
            self.center.registry.add(o)

    def _ids(self, orders):
        return {o["order_id"] for o in orders}

    def test_night_release_then_recovery_gated_by_observations(self):
        c = self.center
        o1, o2, o3 = night_orders()
        r1 = c.publish(dt("2026-09-21T23:50:00+08:00"), [o1, o2, o3])
        self.assertEqual(r1.version, 1)

        # 凌晨传感器离线+雨仍大时，恢复命令被观测条件拦下
        o4, o5, o6 = morning_orders()
        early = Order("O4E", TRANSPORT, "open_road", "D2", frozenset({"yx3", "lj1"}),
                      dt("2026-09-21T23:55:00+08:00"), "抢通",
                      revokes="O3",
                      condition=ResumeCondition(max_rain_1h_mm=5.0, max_water_cm=10.0,
                                                sensor_fresh_minutes=30))
        c.registry.add(early)
        with self.assertRaises(PublishBlocked) as ctx:
            c.publish(dt("2026-09-21T23:56:00+08:00"), [early])
        self.assertIn("恢复条件未满足", str(ctx.exception))

        # 清晨：雨带移出、近1小时小雨、有新观测、积水复核5cm → 依次满足
        r2 = c.publish(dt("2026-09-22T06:56:00+08:00"), [o4])
        self.assertEqual(r2.version, 2)
        self.assertIn("O3", r2.revoked_order_ids)
        # 校车恢复与学校复课必须同批联合发布：先于复课恢复发车会被判矛盾
        with self.assertRaises(PublishBlocked):
            c.publish(dt("2026-09-22T07:06:00+08:00"), [o5])
        r3 = c.publish(dt("2026-09-22T07:11:00+08:00"), [o5, o6])
        self.assertEqual(r3.version, 3)

        # 原始命令与原始时刻全部保留，只是处于被撤销状态
        self.assertEqual(c.registry.get("O3").issued_at, dt("2026-09-21T23:40:00+08:00"))
        self.assertTrue(c.registry.is_revoked(o3))
        self.assertTrue(c.registry.is_revoked(o1))

    def test_three_roles_see_same_version_and_only_effective_actions(self):
        c = self.center
        o1, o2, o3 = night_orders()
        night = c.publish(dt("2026-09-21T23:50:00+08:00"), [o1, o2, o3])

        parent = c.public_view("yx2", dt("2026-09-21T23:55:00+08:00"), "parent", night)
        driver = c.public_view("yx3", dt("2026-09-21T23:55:00+08:00"), "driver", night)
        rescue = c.public_view("yx3", dt("2026-09-21T23:55:00+08:00"), "rescue", night)
        # 三端同一版本，不靠后续短信猜
        self.assertEqual({parent["version"], driver["version"], rescue["version"]}, {1})
        self.assertEqual(self._ids(parent["actions"]), {"O1"})
        self.assertEqual(self._ids(driver["actions"]), {"O2", "O3"})
        self.assertEqual(self._ids(rescue["actions"]), {"O2", "O3"})
        # 救援端能看到本格点地质灾害点和同区县避险场所
        self.assertEqual(rescue["geohazards_here"], ["G1"])
        self.assertIn("H1", rescue["shelters_in_district"])
        # 家长按当前位置取数：人在临江格点收不到云溪的停课
        parent_lj = c.public_view("lj2", dt("2026-09-21T23:55:00+08:00"), "parent", night)
        self.assertEqual(parent_lj["actions"], [])

        # 清晨：解封单独成版，校车恢复与复课同批联合发布
        o4, o5, o6 = morning_orders()
        c.publish(dt("2026-09-22T06:56:00+08:00"), [o4])
        latest = c.publish(dt("2026-09-22T07:11:00+08:00"), [o5, o6])
        p2 = c.public_view("yx2", dt("2026-09-22T07:15:00+08:00"), "parent", latest)
        d2 = c.public_view("yx3", dt("2026-09-22T07:15:00+08:00"), "driver", latest)
        r2v = c.public_view("yx3", dt("2026-09-22T07:15:00+08:00"), "rescue", latest)
        r2v_2 = c.public_view("yx2", dt("2026-09-22T07:15:00+08:00"), "rescue", latest)
        self.assertEqual({p2["version"], d2["version"], r2v["version"]}, {3})
        self.assertEqual(self._ids(p2["actions"]), {"O6"})
        self.assertEqual(self._ids(d2["actions"]), {"O4", "O5"})
        # 救援端按格点看到与该位置相交的全部行动
        self.assertEqual(self._ids(r2v["actions"]), {"O4", "O5"})
        self.assertEqual(self._ids(r2v_2["actions"]), {"O5", "O6"})
        # 旧版本快照保持不变：历史可回放，不再猜"哪条通知有效"
        old_parent = c.public_view("yx2", dt("2026-09-21T23:55:00+08:00"), "parent", night)
        self.assertEqual(self._ids(old_parent["actions"]), {"O1"})

    def test_waterlog_false_report_does_not_block_road(self):
        # D1 的 40cm 积水是误报：无可用积水记录时不允许凭误报封路判断，
        # 而 D2 解封只认真实复核的 5cm 记录
        from src.features import latest_usable_waterlog
        self.assertIsNone(latest_usable_waterlog(self.world.waterlogs, "D1",
                                                 dt("2026-09-22T07:00:00+08:00")))
        rec = latest_usable_waterlog(self.world.waterlogs, "D2",
                                     dt("2026-09-22T07:00:00+08:00"))
        self.assertEqual(rec.depth_cm, 5.0)


class FixtureScenarioTest(unittest.TestCase):
    def test_fixture_loads_and_runs_full_flow(self):
        grid, center = load_scenario(Path("fixtures/scenario.json"))
        self.assertEqual(len(grid.cell_districts), 7)
        ids = [o.order_id for o in center.registry.all_orders()]
        self.assertEqual(ids, ["O1", "O2", "O3", "O4", "O5", "O6"])

        def get(oid):
            return center.registry.get(oid)

        night = center.publish(dt("2026-09-21T23:50:00+08:00"),
                               [get("O1"), get("O2"), get("O3")])
        self.assertEqual(night.version, 1)
        center.publish(dt("2026-09-22T06:56:00+08:00"), [get("O4")])
        latest = center.publish(dt("2026-09-22T07:11:00+08:00"),
                                [get("O5"), get("O6")])
        view = center.public_view("yx2", dt("2026-09-22T07:15:00+08:00"),
                                  "parent", latest)
        self.assertEqual([a["order_id"] for a in view["actions"]], ["O6"])


if __name__ == "__main__":
    unittest.main()
