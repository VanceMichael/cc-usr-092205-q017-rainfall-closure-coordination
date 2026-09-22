"""统一预报格点与落区相交计算。

区县应急指挥只维护一份格点：每个格点归属一个区县。
预警雨带、实况雨量、学校、道路、校车线路、地质灾害点和避险场所
全部换算到格点上比较，"是否受影响"等价于"格点集合是否相交"。
"""

from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class Grid:
    """统一预报格点，cell -> 所属区县，加载后不可再改。"""

    cell_districts: MappingProxyType

    @staticmethod
    def build(cell_districts: dict) -> "Grid":
        if not cell_districts:
            raise ValueError("格点表不能为空")
        for cell, district in cell_districts.items():
            if not cell or not district:
                raise ValueError("格点与区县标识不能为空")
        return Grid(MappingProxyType(dict(cell_districts)))

    def require_cells(self, cells, what: str) -> frozenset:
        """校验一组格点都存在，返回 frozenset 便于相交计算。"""
        cells = frozenset(cells)
        if not cells:
            raise ValueError(f"{what}至少覆盖一个格点")
        unknown = cells - self.cell_districts.keys()
        if unknown:
            raise ValueError(f"{what}引用了未知格点: {sorted(unknown)}")
        return cells

    def districts_of(self, cells) -> frozenset:
        """一组格点覆盖到的区县集合（跨区判断的依据）。"""
        return frozenset(self.cell_districts[c] for c in cells)


def intersects(a: frozenset, b: frozenset) -> bool:
    """落区相交：只有真正共享格点才算互相影响。"""
    return not a.isdisjoint(b)
