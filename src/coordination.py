"""区县应急联合指挥：发布前矛盾拦截、恢复条件核验、统一版本与公众视图。

核心规则（对应需求叙述）：
1. 教育管停课、交通管停运/封路；任何恢复决定必须满足写明的观测条件。
2. 发布前对三方决定做一致性检查，发现矛盾整批拦下，不允许互相矛盾
   的决定先后到达公众（家长先收到停课、校车系统却提示发车）。
3. 每次成功发布生成一个只增版本；撤销是新命令，原始命令和原始时刻保留。
4. 家长、司机、救援人员都从同一版本快照取数，公众端按当前位置只收到
   当前仍有效（未被撤销、落区覆盖所在格点）的行动。
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import MappingProxyType

from .features import latest_usable_waterlog
from .orders import (
    ACTION_CLOSE_ROAD,
    ACTION_OPEN_ROAD,
    ACTION_RESUME_BUS,
    ACTION_RESUME_CLASS,
    ACTION_SUSPEND_BUS,
    ACTION_SUSPEND_CLASS,
    Order,
    OrderRegistry,
)
from .rainfall import ADVISORY_LEVEL, level_at, latest_confirmation, window_sum


class PublishBlocked(Exception):
    """对外发布前发现矛盾决定或恢复条件不满足。"""

    def __init__(self, contradictions=None, failed_conditions=None):
        self.contradictions = contradictions or []
        self.failed_conditions = failed_conditions or []
        parts = []
        if self.contradictions:
            parts.append("互相矛盾的决定: " + "; ".join(self.contradictions))
        if self.failed_conditions:
            parts.append("恢复条件未满足: " + "; ".join(self.failed_conditions))
        super().__init__(" | ".join(parts))


@dataclass(frozen=True)
class ConditionReport:
    order_id: str
    ok: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class Release:
    """一次对外发布：版本号、时刻、纳入的命令、格点行动快照。"""

    version: int
    published_at: datetime
    order_ids: tuple[str, ...]
    cell_actions: MappingProxyType  # cell -> tuple[Order]
    revoked_order_ids: tuple[str, ...]
    def actions_at(self, cell: str) -> tuple[Order, ...]:
        return self.cell_actions.get(cell, ())


@dataclass
class _World:
    """指挥中心需要核验时取用的全部统一资料。"""

    bands: list = field(default_factory=list)
    observations: list = field(default_factory=list)
    waterlogs: list = field(default_factory=list)
    schools: dict = field(default_factory=dict)       # id -> Feature
    routes: dict = field(default_factory=dict)        # id -> BusRoute
    roads: dict = field(default_factory=dict)         # id -> Feature
    shelters: dict = field(default_factory=dict)      # id -> Feature
    geohazards: dict = field(default_factory=dict)    # id -> Feature
    cell_district: dict = field(default_factory=dict)


def _is_live(order: Order, live_ids: set[str], revoked_ids: set[str]) -> bool:
    return order.order_id in live_ids and order.order_id not in revoked_ids


class CoordinationCenter:
    def __init__(self, registry: OrderRegistry, world: _World):
        self.registry = registry
        self.world = world
        self.releases: list[Release] = []

    # ---- 恢复条件核验 -------------------------------------------------

    def evaluate_condition(self, order: Order, at: datetime) -> ConditionReport:
        """用统一雨情/积水资料核验恢复类命令上写明的观测条件。"""
        cond = order.condition
        if cond is None:
            return ConditionReport(order.order_id, False, ("恢复命令未写明观测条件",))
        reasons = []
        cells = order.cells

        rain_1h = window_sum(self.world.observations, cells, at, 1)
        if rain_1h > cond.max_rain_1h_mm:
            reasons.append(f"近1小时雨量{rain_1h:g}毫米超过{cond.max_rain_1h_mm:g}毫米")

        # 目标格点上不能仍挂着高级别生效预警（雨带未移出）
        if level_at(self.world.bands, cells, at) >= ADVISORY_LEVEL:
            reasons.append("目标格点仍有高级别生效预警")

        # 每个目标格点近 N 分钟内必须有可用观测；最新状态是离线则不算
        for cell in sorted(cells):
            fresh = latest_confirmation(
                self.world.observations,
                cell,
                at - timedelta(minutes=cond.sensor_fresh_minutes),
                at,
            )
            if fresh is None:
                reasons.append(f"格点{cell}近{cond.sensor_fresh_minutes}分钟无可用观测（可能离线）")

        if order.action == ACTION_OPEN_ROAD:
            if cond.max_water_cm is None:
                reasons.append("解封条件未写明积水阈值")
            else:
                rec = latest_usable_waterlog(self.world.waterlogs, order.target_id, at)
                if rec is None:
                    reasons.append("道路无可用积水复核记录")
                elif rec.depth_cm > cond.max_water_cm:
                    reasons.append(
                        f"最近积水{rec.depth_cm:g}厘米超过{cond.max_water_cm:g}厘米"
                    )

        return ConditionReport(order.order_id, not reasons, tuple(reasons))

    # ---- 发布前一致性检查 ---------------------------------------------

    def _decision_sets(self, candidates: list[Order]):
        """合并已发布命令与本批候选，算出当前有效集合与被撤销集合。"""
        selected = {o.order_id for o in candidates}
        orders = [o for o in self.registry.all_orders() if o.status == "published"] + candidates
        by_id = {o.order_id: o for o in orders}
        revoked = {
            o.revokes
            for o in orders
            if o.revokes and o.revokes in by_id and not o.cells.isdisjoint(by_id[o.revokes].cells)
        }
        live = {oid for oid in by_id if oid not in revoked}
        return orders, by_id, live, revoked

    def pre_publish_check(self, candidates: list[Order], at: datetime):
        """返回 (矛盾描述列表, 未通过的恢复条件列表)；均为空才可发布。"""
        orders, by_id, live, revoked = self._decision_sets(candidates)
        contradictions = []

        def live_orders(action):
            return [o for o in orders if o.action == action and _is_live(o, live, revoked)]

        suspended_schools = {o.target_id for o in live_orders(ACTION_SUSPEND_CLASS)}
        closed_roads = {o.target_id for o in live_orders(ACTION_CLOSE_ROAD)}
        suspended_routes = {o.target_id for o in live_orders(ACTION_SUSPEND_BUS)}
        resumed_routes = {o.target_id for o in live_orders(ACTION_RESUME_BUS)}

        # 规则一：同一目标上的直接对立（封路 vs 解封、停运 vs 恢复）
        for a in orders:
            if not _is_live(a, live, revoked):
                continue
            for opposite_action in _OPPOSITE_OF.get(a.action, ()):
                for b in live_orders(opposite_action):
                    if a.target_id == b.target_id and not a.cells.isdisjoint(b.cells):
                        contradictions.append(
                            f"{a.target_id} 同时存在{a.action}({a.order_id})与{b.action}({b.order_id})"
                        )

        # 规则二：学校停课，关联校车却仍按正常/恢复状态发车
        for route in self.world.routes.values():
            if route.school_id in suspended_schools:
                if route.route_id in resumed_routes or route.route_id not in suspended_routes:
                    contradictions.append(
                        f"学校{route.school_id}已停课，但校车线路{route.route_id}仍提示发车"
                    )

        # 规则三：线路经过任何当前封闭（且落区相交）的道路，必须停运
        for road_id in closed_roads:
            road = self.world.roads[road_id]
            for route in self.world.routes.values():
                if not route.cells.isdisjoint(road.cells) and route.route_id not in suspended_routes:
                    contradictions.append(
                        f"校车线路{route.route_id}途经已封闭道路{road_id}但未停运"
                    )

        # 恢复类命令：观测条件逐条核验
        failed = []
        for o in candidates:
            if o.action in (ACTION_RESUME_CLASS, ACTION_RESUME_BUS, ACTION_OPEN_ROAD):
                report = self.evaluate_condition(o, at)
                if not report.ok:
                    failed.append(f"{o.order_id}: " + "、".join(report.reasons))
        return contradictions, failed

    # ---- 发布 ---------------------------------------------------------

    def publish(self, at: datetime, candidates: list[Order] | None = None) -> Release:
        candidates = list(candidates) if candidates is not None else [
            o for o in self.registry.all_orders() if o.status == "pending"
        ]
        contradictions, failed = self.pre_publish_check(candidates, at)
        if contradictions or failed:
            raise PublishBlocked(contradictions, failed)

        for o in candidates:
            if o.status == "pending":
                self.registry.mark_published(o.order_id)

        orders, by_id, live, revoked = self._decision_sets([])
        cell_actions: dict[str, list[Order]] = {}
        for oid in live:
            for cell in by_id[oid].cells:
                cell_actions.setdefault(cell, []).append(by_id[oid])

        release = Release(
            version=len(self.releases) + 1,
            published_at=at,
            order_ids=tuple(o.order_id for o in candidates),
            cell_actions=MappingProxyType({c: tuple(v) for c, v in cell_actions.items()}),
            revoked_order_ids=tuple(sorted(revoked)),
        )
        self.releases.append(release)
        return release

    # ---- 多端同一版本的公众视图 --------------------------------------

    def public_view(self, cell: str, at: datetime, role: str, release: Release | None = None):
        """按当前位置格点返回当前有效行动；三端共用同一 release 快照。"""
        release = release or (self.releases[-1] if self.releases else None)
        if release is None:
            raise ValueError("尚未发布任何版本")
        district = self.world.cell_district.get(cell)
        orders_here = list(release.actions_at(cell))

        items = []
        for o in orders_here:
            if role == "parent":
                if o.action in (ACTION_SUSPEND_CLASS, ACTION_RESUME_CLASS) and o.target_id in {
                    s.feature_id for s in self.world.schools.values() if cell in s.cells
                }:
                    items.append(_item(o))
            elif role == "driver":
                if o.action in (
                    ACTION_SUSPEND_BUS, ACTION_RESUME_BUS, ACTION_CLOSE_ROAD, ACTION_OPEN_ROAD
                ):
                    items.append(_item(o))
            elif role == "rescue":
                items.append(_item(o))
            else:
                raise ValueError("未知公众角色")

        shelters = [
            f.feature_id
            for f in self.world.shelters.values()
            if district is not None and district in f.districts
        ]
        geohazards = [
            f.feature_id
            for f in self.world.geohazards.values()
            if not f.cells.isdisjoint({cell})
        ] if role == "rescue" else []

        return {
            "version": release.version,
            "published_at": release.published_at.isoformat(),
            "as_of": at.isoformat(),
            "cell": cell,
            "role": role,
            "actions": items,
            "shelters_in_district": sorted(shelters),
            "geohazards_here": sorted(geohazards),
        }


_OPPOSITE_OF = {
    ACTION_SUSPEND_CLASS: (ACTION_RESUME_CLASS,),
    ACTION_RESUME_CLASS: (ACTION_SUSPEND_CLASS,),
    ACTION_SUSPEND_BUS: (ACTION_RESUME_BUS,),
    ACTION_RESUME_BUS: (ACTION_SUSPEND_BUS,),
    ACTION_CLOSE_ROAD: (ACTION_OPEN_ROAD,),
    ACTION_OPEN_ROAD: (ACTION_CLOSE_ROAD,),
}


def _item(o: Order) -> dict:
    return {
        "order_id": o.order_id,
        "action": o.action,
        "target_id": o.target_id,
        "issued_at": o.issued_at.isoformat(),
        "reason": o.reason,
        "revokes": o.revokes,
    }
