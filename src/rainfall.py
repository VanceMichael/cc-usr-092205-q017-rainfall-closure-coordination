"""多源雨情：预警雨带与实况雨量。

- 预警雨带有生效时段（可跨午夜）和落区格点，升级或雨带移动就是
  新来一条雨带记录，按 observed_at 取最新生效的一条。
- 实况雨量保留观测原始时刻；传感器离线与人工误报都留在记录里，
  只是不计入阈值统计，事后可追溯。
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

SOURCE_SENSOR = "sensor"
SOURCE_MANUAL = "manual"

QUALITY_OK = "ok"
QUALITY_OFFLINE = "offline"  # 传感器离线：保留原始上报时刻，数值不可信
QUALITY_FALSE_REPORT = "false_report"  # 人工误报：保留记录，不计入统计

# 达到该预警级别即对公众发出避险提示（供指挥端参考）
ADVISORY_LEVEL = 2


@dataclass(frozen=True)
class RainBand:
    """一条预警雨带：某时段内某些格点处于某预警级别。"""

    band_id: str
    level: int
    cells: frozenset
    valid_from: datetime
    valid_to: datetime
    observed_at: datetime  # 预报发布时间，原始时刻

    def __post_init__(self):
        if not 1 <= self.level <= 4:
            raise ValueError("预警级别须在 1~4 之间")
        if not self.cells:
            raise ValueError("雨带落区不能为空")
        if not self.valid_from < self.valid_to:
            raise ValueError("雨带生效时段不合法")

    def active_at(self, moment: datetime) -> bool:
        # 半开区间，跨午夜由带日期的时刻自然表达
        return self.valid_from <= moment < self.valid_to

    def covers(self, cells: frozenset) -> bool:
        return not self.cells.isdisjoint(cells)


@dataclass(frozen=True)
class Observation:
    """一条实况雨量记录，observed_at 为原始观测时刻，永不改写。"""

    station_id: str
    cell: str
    observed_at: datetime
    mm: float
    source: str = SOURCE_SENSOR
    quality: str = QUALITY_OK

    def usable(self) -> bool:
        """离线或误报的读数不参与任何阈值统计。"""
        return self.quality == QUALITY_OK


def level_at(bands, cells: frozenset, moment: datetime) -> int:
    """某时刻一组格点上的最高生效预警级别；无生效雨带为 0。"""
    level = 0
    for band in bands:
        if band.active_at(moment) and band.covers(cells):
            level = max(level, band.level)
    return level


def window_sum(observations, cells: frozenset, end: datetime, hours: float) -> float:
    """截至 end 向前 hours 小时内、落在 cells 上的可用雨量合计（毫米）。"""
    start = end - timedelta(hours=hours)
    return sum(
        obs.mm
        for obs in observations
        if obs.usable() and obs.cell in cells and start <= obs.observed_at <= end
    )


def latest_confirmation(observations, cell: str, since: datetime, until: datetime):
    """窗口内某格点最近一次可用确认；传感器最新状态为离线时返回 None。

    离线读数保留在原始记录里，但不能充当"雨已停"的证据。
    """
    relevant = [
        obs for obs in observations if obs.cell == cell and since <= obs.observed_at <= until
    ]
    if not relevant:
        return None
    relevant.sort(key=lambda obs: obs.observed_at)
    if relevant[-1].quality == QUALITY_OFFLINE:
        return None
    usable = [obs for obs in relevant if obs.usable()]
    return usable[-1] if usable else None
