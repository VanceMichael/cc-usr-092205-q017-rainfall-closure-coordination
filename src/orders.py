"""部门命令：停课、停运、封路，以及恢复条件。

三条职责边界（叙述中明确）：
- 教育部门对"停课"负责
- 交通部门对"停运"（校车/客运）和"封路"负责
- 应急部门负责统一发布与避险调度，不代行上面两权

命令一经创建，issued_at（原始时刻）与决定内容不可改写；
"撤销"是补发一条带 revoke 原因的新命令并指向原命令，
原始命令及其时刻仍然保留。恢复上课/通车/解封不能凭口头通知，
必须满足命令上写明的观测条件（由 coordination 用雨情/积水数据核验）。
"""

from dataclasses import dataclass
from datetime import datetime

EDUCATION = "education"
TRANSPORT = "transport"
EMERGENCY = "emergency"

ACTION_SUSPEND_CLASS = "suspend_class"      # 停课
ACTION_RESUME_CLASS = "resume_class"        # 恢复上课
ACTION_SUSPEND_BUS = "suspend_bus"          # 校车停运
ACTION_RESUME_BUS = "resume_bus"            # 恢复发车
ACTION_CLOSE_ROAD = "close_road"            # 封路
ACTION_OPEN_ROAD = "open_road"              # 道路解封

# 每种行动归哪个部门管，越权起草直接拒绝
RESPONSIBILITY = {
    ACTION_SUSPEND_CLASS: EDUCATION,
    ACTION_RESUME_CLASS: EDUCATION,
    ACTION_SUSPEND_BUS: TRANSPORT,
    ACTION_RESUME_BUS: TRANSPORT,
    ACTION_CLOSE_ROAD: TRANSPORT,
    ACTION_OPEN_ROAD: TRANSPORT,
}

# 互相对立的行动对：同一目标上同时存在生效的两方即"互相矛盾的决定"
OPPOSITES = (
    (frozenset({ACTION_SUSPEND_CLASS}), frozenset({ACTION_RESUME_CLASS})),
    (frozenset({ACTION_SUSPEND_BUS}), frozenset({ACTION_RESUME_BUS})),
    (frozenset({ACTION_CLOSE_ROAD}), frozenset({ACTION_OPEN_ROAD})),
)

# 撤销与被撤销的行动
REVOKE_OF = {
    ACTION_RESUME_CLASS: ACTION_SUSPEND_CLASS,
    ACTION_RESUME_BUS: ACTION_SUSPEND_BUS,
    ACTION_OPEN_ROAD: ACTION_CLOSE_ROAD,
}


@dataclass(frozen=True)
class ResumeCondition:
    """恢复行动必须满足的观测条件，全部满足才可执行。

    - max_rain_1h_mm：目标格点近 1 小时累计雨量不超过该值
    - max_water_cm：目标道路最近可用积水读数不超过该值（仅道路类）
    - sensor_fresh_minutes：要求近 N 分钟内有可用观测，
      传感器离线期间不能判定"已恢复"
    """

    max_rain_1h_mm: float
    max_water_cm: float | None = None
    sensor_fresh_minutes: int = 60


@dataclass(frozen=True)
class Order:
    order_id: str
    agency: str
    action: str
    target_id: str          # 学校 / 线路 / 道路的标识
    cells: frozenset        # 命令覆盖格点（落区变化只影响真正相交区域）
    issued_at: datetime     # 原始时刻，永不改写
    reason: str
    revokes: str | None = None     # 若为撤销命令，指向原命令 id
    condition: ResumeCondition | None = None  # 恢复类命令须写明观测条件
    status: str = "pending"        # pending -> published；可被 revoked

    def __post_init__(self):
        if self.agency not in (EDUCATION, TRANSPORT, EMERGENCY):
            raise ValueError("未知部门")
        owner = RESPONSIBILITY.get(self.action)
        if owner is not None and self.agency != owner:
            raise ValueError(f"行动{self.action}由{owner}部门负责，{self.agency}部门无权决定")
        if self.action in REVOKE_OF and not self.revokes:
            raise ValueError("恢复/撤销命令必须指向原始命令")
        is_resume = self.action in (ACTION_RESUME_CLASS, ACTION_RESUME_BUS, ACTION_OPEN_ROAD)
        if is_resume and self.condition is None:
            raise ValueError("恢复上课/发车/解封必须写明观测条件")
        if not self.cells:
            raise ValueError("命令必须覆盖至少一个格点")


class OrderRegistry:
    """保存全部命令草稿与原始时刻，按目标归集做矛盾检查。"""

    def __init__(self):
        self._orders: dict[str, Order] = {}

    def add(self, order: Order) -> Order:
        if order.order_id in self._orders:
            raise ValueError("命令标识重复")
        self._orders[order.order_id] = order
        return order

    def get(self, order_id: str) -> Order:
        return self._orders[order_id]

    def all_orders(self) -> tuple[Order, ...]:
        return tuple(self._orders.values())

    def find_contradictions(self) -> list[tuple[Order, Order]]:
        """同一目标上、落区相交且同时处于生效状态的对立行动。

        叙述场景：教育已发停课、交通系统仍提示正常发车——
        跨行动类型（停课 vs 发车）在 coordination 层按"同一学校"另行检查；
        这里先抓同行动线上的直接对立（如封路与解封、停运与恢复）。
        """
        live = [o for o in self._orders.values() if o.status == "published" and not self.is_revoked(o)]
        found = []
        for i, a in enumerate(live):
            for b in live[i + 1:]:
                if a.target_id != b.target_id:
                    continue
                if a.cells.isdisjoint(b.cells):
                    continue
                for side_x, side_y in OPPOSITES:
                    if (a.action in side_x and b.action in side_y) or (
                        b.action in side_x and a.action in side_y
                    ):
                        found.append((a, b))
        return found

    def is_revoked(self, order: Order) -> bool:
        """原命令是否已被一条已发布的撤销命令抵消；原始记录仍在。"""
        return any(
            o.status == "published"
            and o.revokes == order.order_id
            and not o.cells.isdisjoint(order.cells)
            for o in self._orders.values()
        )

    def mark_published(self, order_id: str) -> None:
        order = self._orders[order_id]
        object.__setattr__(order, "status", "published")

    def active_on_target(self, action_group: frozenset, target_id: str) -> list[Order]:
        return [
            o
            for o in self._orders.values()
            if o.status == "published"
            and o.action in action_group
            and o.target_id == target_id
            and not self.is_revoked(o)
        ]
