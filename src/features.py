"""承灾体与应急资源：地质灾害点、积水道路、学校、校车线路、避险场所。

所有要素统一落到格点上；道路和校车线路可以跨多个格点，
因此天然可能横跨两个区县，相交判断逐格点进行。
"""

from dataclasses import dataclass, field
from datetime import datetime

from .geometry import Grid


@dataclass(frozen=True)
class Feature:
    feature_id: str
    name: str
    cells: frozenset
    districts: frozenset = field(default=frozenset(), compare=False)

    @property
    def is_cross_district(self) -> bool:
        # 跨区道路：一条线的格点落在多个区县
        return len(self.districts) > 1


def _make(grid: Grid, feature_id, name, cells, kind) -> Feature:
    cells = grid.require_cells(cells, f"{kind}{feature_id}")
    return Feature(feature_id, name, cells, grid.districts_of(cells))


def make_geohazard(grid, point_id: str, name: str, cells) -> Feature:
    """地质灾害隐患点（山区、河谷）。"""
    return _make(grid, point_id, name, cells, "地质灾害点")


def make_road(grid, road_id: str, name: str, cells) -> Feature:
    """道路；覆盖多个区县格点即为跨区道路。"""
    return _make(grid, road_id, name, cells, "道路")


def make_school(grid, school_id: str, name: str, cells) -> Feature:
    return _make(grid, school_id, name, cells, "学校")


def make_shelter(grid, shelter_id: str, name: str, cells) -> Feature:
    return _make(grid, shelter_id, name, cells, "避险场所")


@dataclass(frozen=True)
class BusRoute:
    route_id: str
    name: str
    cells: frozenset
    school_id: str
    districts: frozenset = field(default=frozenset(), compare=False)

    @property
    def is_cross_district(self) -> bool:
        return len(self.districts) > 1


def make_route(grid, route_id: str, name: str, cells, school_id: str) -> BusRoute:
    cells = grid.require_cells(cells, f"校车线路{route_id}")
    if not school_id:
        raise ValueError("校车线路必须关联学校")
    return BusRoute(route_id, name, cells, school_id, grid.districts_of(cells))


@dataclass(frozen=True)
class WaterlogRecord:
    """道路积水观测，observed_at 为原始时刻；误报保留但不参与解封判断。"""

    road_id: str
    observed_at: datetime
    depth_cm: float
    quality: str = "ok"

    def usable(self) -> bool:
        return self.quality == "ok"


def latest_usable_waterlog(records, road_id: str, at: datetime):
    """某道路截至 at 最近一条可用积水记录；没有记录返回 None。"""
    usable = [
        r
        for r in records
        if r.usable() and r.road_id == road_id and r.observed_at <= at
    ]
    if not usable:
        return None
    return max(usable, key=lambda r: r.observed_at)
