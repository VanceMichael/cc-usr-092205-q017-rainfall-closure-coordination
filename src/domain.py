"""读取并检查项目共享资料。"""

import json
from pathlib import Path

from .errors import DomainError

_REQUIRED = {
    "domain",
    "version",
    "sample_id",
    "actors",
    "facts",
    "constraints",
    "cells",
    "districts",
    "schools",
    "roads",
    "bus_routes",
    "hazard_sites",
    "shelters",
    "rainfall_stations",
    "rainfall_series",
    "events",
}


def load_domain(path: Path) -> dict:
    """读取字段完整且带版本的业务资料，并做基本完整性校验。"""
    value = json.loads(path.read_text(encoding="utf-8"))
    missing = _REQUIRED - set(value)
    if missing:
        raise DomainError(f"共享资料缺少必要字段: {sorted(missing)}")
    if value["version"] < 1 or len(value["actors"]) < 2 or len(value["facts"]) < 2:
        raise DomainError("共享资料内容不完整")
    _check_integrity(value)
    return value


def _check_integrity(value):
    cell_ids = {c["id"] for c in value["cells"]}
    district_ids = {d["id"] for d in value["districts"]}
    station_ids = {s["id"] for s in value["rainfall_stations"]}
    target_ids = (
        {s["id"] for s in value["schools"]}
        | {r["id"] for r in value["roads"]}
        | {b["id"] for b in value["bus_routes"]}
    )

    for station in value["rainfall_stations"]:
        if station["cell"] not in cell_ids:
            raise DomainError(f"雨量站 {station['id']} 引用了未知格点 {station['cell']}")
    for series in value["rainfall_series"]:
        if series["station_id"] not in station_ids:
            raise DomainError(f"雨量序列引用了未知雨量站 {series['station_id']}")
    for school in value["schools"]:
        if school["district"] not in district_ids:
            raise DomainError(f"学校 {school['id']} 引用了未知区县 {school['district']}")
    for route in value["bus_routes"]:
        unknown = (set(route["serves"]) | set(route["uses_roads"])) - target_ids
        if unknown:
            raise DomainError(f"校车线路 {route['id']} 引用了未知对象 {sorted(unknown)}")

    order_ids = set()
    report_ids = set()
    for event in value["events"]:
        kind = event["type"]
        if kind == "warning":
            unknown = set(event["cells"]) - cell_ids
            if unknown:
                raise DomainError(f"预警 {event['id']} 引用了未知格点 {sorted(unknown)}")
        elif kind == "order":
            if event["target"] not in target_ids:
                raise DomainError(f"命令 {event['id']} 引用了未知对象 {event['target']}")
            order_ids.add(event["id"])
        elif kind == "order_rejected":
            if event["target"] not in target_ids:
                raise DomainError(f"被拒绝的提案 {event['id']} 引用了未知对象 {event['target']}")
        elif kind == "manual_report":
            if event["target"] not in target_ids:
                raise DomainError(f"人工上报 {event['id']} 引用了未知对象 {event['target']}")
            report_ids.add(event["id"])
    for event in value["events"]:
        if event["type"] == "order_revoked" and event["order_id"] not in order_ids:
            raise DomainError(f"撤销记录引用了未知命令 {event['order_id']}")
        if event["type"] == "report_retracted" and event["report_id"] not in report_ids:
            raise DomainError(f"撤回记录引用了未知上报 {event['report_id']}")
