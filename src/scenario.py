"""从情景 JSON 构建统一指挥世界（格点、雨情、要素、命令）。

时间统一用带日期的 ISO 8601，跨午夜降雨靠自然时刻表达，
不再用"几点几分"这类会在午夜后歧义的本地记法。
"""

import json
from datetime import datetime
from pathlib import Path

from .coordination import CoordinationCenter
from .features import (
    WaterlogRecord,
    make_geohazard,
    make_road,
    make_route,
    make_school,
    make_shelter,
)
from .geometry import Grid
from .orders import Order, OrderRegistry, ResumeCondition
from .rainfall import Observation, RainBand


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def load_scenario(path: Path) -> tuple[Grid, CoordinationCenter]:
    """读取并校验情景资料，返回格点与挂好全部资料的指挥中心。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    grid = Grid.build({c["id"]: c["district"] for c in data["grid_cells"]})

    bands = [
        RainBand(
            band_id=b["id"],
            level=b["level"],
            cells=grid.require_cells(b["cells"], f"雨带{b['id']}"),
            valid_from=_dt(b["valid_from"]),
            valid_to=_dt(b["valid_to"]),
            observed_at=_dt(b["observed_at"]),
        )
        for b in data["rain_bands"]
    ]
    observations = []
    for o in data["observations"]:
        (cell,) = grid.require_cells([o["cell"]], f"站点{o['station']}")
        observations.append(
            Observation(
                station_id=o["station"],
                cell=cell,
                observed_at=_dt(o["observed_at"]),
                mm=o["mm"],
                source=o.get("source", "sensor"),
                quality=o.get("quality", "ok"),
            )
        )

    from .coordination import _World

    world = _World(bands=bands, observations=observations, cell_district=dict(grid.cell_districts))

    world.geohazards = {
        g["id"]: make_geohazard(grid, g["id"], g["name"], g["cells"]) for g in data.get("geohazards", [])
    }
    world.roads = {
        r["id"]: make_road(grid, r["id"], r["name"], r["cells"]) for r in data.get("roads", [])
    }
    world.schools = {
        s["id"]: make_school(grid, s["id"], s["name"], s["cells"]) for s in data.get("schools", [])
    }
    world.shelters = {
        s["id"]: make_shelter(grid, s["id"], s["name"], s["cells"]) for s in data.get("shelters", [])
    }
    world.routes = {
        r["id"]: make_route(grid, r["id"], r["name"], r["cells"], r["school_id"])
        for r in data.get("bus_routes", [])
    }
    world.waterlogs = [
        WaterlogRecord(
            road_id=w["road_id"],
            observed_at=_dt(w["observed_at"]),
            depth_cm=w["depth_cm"],
            quality=w.get("quality", "ok"),
        )
        for w in data.get("waterlogs", [])
    ]

    registry = OrderRegistry()
    for od in data.get("orders", []):
        cond = None
        if od.get("condition"):
            c = od["condition"]
            cond = ResumeCondition(
                max_rain_1h_mm=c["max_rain_1h_mm"],
                max_water_cm=c.get("max_water_cm"),
                sensor_fresh_minutes=c.get("sensor_fresh_minutes", 60),
            )
        registry.add(
            Order(
                order_id=od["id"],
                agency=od["agency"],
                action=od["action"],
                target_id=od["target_id"],
                cells=grid.require_cells(od["cells"], f"命令{od['id']}"),
                issued_at=_dt(od["issued_at"]),
                reason=od.get("reason", ""),
                revokes=od.get("revokes"),
                condition=cond,
                status=od.get("status", "pending"),
            )
        )

    return grid, CoordinationCenter(registry, world)
