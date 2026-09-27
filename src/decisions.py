"""联合决策：分部门权限、恢复条件核验。

教育部门负责停课与恢复上课，交通部门负责校车停运与恢复运营，
应急部门负责封路与解封。各部门的决定各自进入只追加事件流，
是否互相矛盾在“对外发布”这道闸门统一检查（见 bulletin.try_publish），
发现矛盾的决定不会到达公众，且拦截时刻与原因同样留痕。
"""

from datetime import timedelta

from . import timeline
from .errors import DomainError
from .geo import target_cells

# 每类命令只允许对应部门签发
ACTION_AUTHORITY = {
    "suspend_classes": "education",
    "resume_classes": "education",
    "suspend_route": "transport",
    "resume_route": "transport",
    "close_road": "emergency",
    "reopen_road": "emergency",
}

# 恢复上课/解封/恢复运营前必须满足的观测条件，逐条写明
RESUME_CONDITIONS = {
    "resume_classes": {
        "rain_window_hours": 6,
        "max_rain_mm": 30.0,
        "max_warning_level": "黄色",
        "require_no_active_manual_report": True,
    },
    "reopen_road": {
        "rain_window_hours": 2,
        "max_rain_mm": 10.0,
        "max_warning_level": "黄色",
        "require_no_active_manual_report": True,
    },
    "resume_route": {
        "rain_window_hours": 3,
        "max_rain_mm": 15.0,
        "max_warning_level": "黄色",
        "require_no_active_manual_report": True,
    },
}

_RESUME_ACTION = {
    "school": "resume_classes",
    "road": "reopen_road",
    "bus_route": "resume_route",
}

_RESTRICT_ACTION = {
    "school": "suspend_classes",
    "road": "close_road",
    "bus_route": "suspend_route",
}


def _stations_for_cells(domain, cells):
    wanted = set(cells)
    return [s for s in domain["rainfall_stations"] if s["cell"] in wanted]


def _target_kind(domain, target_id):
    for kind, collection in (
        ("school", "schools"),
        ("road", "roads"),
        ("bus_route", "bus_routes"),
    ):
        if any(e["id"] == target_id for e in domain[collection]):
            return kind
    raise DomainError(f"找不到作用对象: {target_id}")


def evaluate_resume_conditions(domain, target_kind, target_id, at):
    """核验恢复条件，返回 {"ok": bool, "failures": [...]}。

    条件逐条写明：窗口累计雨量不超标、预警等级不高于上限、
    没有仍有效的相关人工上报，且窗口内观测完整（传感器离线即不满足）。
    """
    conditions = RESUME_CONDITIONS[_RESUME_ACTION[target_kind]]
    cells = target_cells(domain, target_kind, target_id)
    failures = []

    window_start = at - timedelta(hours=conditions["rain_window_hours"])
    stations = _stations_for_cells(domain, cells)
    if not stations:
        failures.append("相关格点没有雨量站，无法核验观测条件")
    for station in stations:
        since, until = window_start, at
        if timeline.rainfall_gap(domain, station["id"], since, until):
            failures.append(f"雨量站 {station['id']} 在观测窗口内离线或数据不足")
            continue
        total = timeline.rainfall_sum(domain, station["id"], since, until)
        if total > conditions["max_rain_mm"]:
            failures.append(
                f"雨量站 {station['id']} 窗口累计 {total:.1f} 毫米，"
                f"超过上限 {conditions['max_rain_mm']:.1f} 毫米"
            )

    warning = timeline.warning_at(domain, at)
    if warning is not None:
        covered = set(warning["cells"]) & set(cells)
        if covered and timeline.WARNING_RANK[warning["level"]] > timeline.WARNING_RANK[
            conditions["max_warning_level"]
        ]:
            failures.append(
                f"预警 {warning['id']}（{warning['level']}）仍覆盖相关格点，"
                f"高于允许等级 {conditions['max_warning_level']}"
            )

    if conditions["require_no_active_manual_report"]:
        for report in timeline.active_manual_reports(domain, at):
            if report.get("target") == target_id:
                failures.append(f"人工上报 {report['id']} 仍有效，未撤回")
    return {"ok": not failures, "failures": failures}


def _find_order(domain, order_id):
    for e in domain["events"]:
        if e["type"] == "order" and e["id"] == order_id:
            return e
    raise DomainError(f"找不到命令: {order_id}")


def _check_authority(domain, actor, action, target_id):
    required = ACTION_AUTHORITY[action]
    if actor.get("authority") != required:
        raise DomainError(f"{actor['name']} 无权签发 {action}，应由 {required} 部门签发")
    if required == "education":
        target_kind = _target_kind(domain, target_id)
        if target_kind == "school":
            school = next(s for s in domain["schools"] if s["id"] == target_id)
            if school["district"] != actor.get("district"):
                raise DomainError(
                    f"{actor['name']} 不能管辖其他区县的学校 {target_id}"
                )


def find_contradictions(domain, at, extra_orders=(), basis="issued"):
    """检查截至某时刻的决定之间是否互相矛盾。

    basis="issued"（默认）：按已签发命令判定——通知一经签发即可能被公众看到，
    发布闸门必须拦住“学校已停课、校车仍正常”这类组合；
    basis="effective"：只按已生效命令判定，用于回答“此刻实际状态是否一致”。

    规则：
    1. 学校停课期间，服务该校的校车线路不得处于正常运营；
    2. 道路封闭期间，途经该道路的校车线路不得处于正常运营；
    3. 校车恢复运营时，其服务的学校不得仍在停课、途经道路不得仍封闭。
    """
    states = timeline.current_states(domain, at, basis=basis)
    for order in extra_orders:
        states[order["target"]] = {
            "action": order["action"],
            "order_id": order["id"],
            "issued_at": order["issued_at"],
            "effective_at": order["effective_at"],
            "cells": order["cells"],
        }

    def state_of(target_id):
        return states.get(target_id, {}).get("action")

    problems = []
    for route in domain["bus_routes"]:
        if state_of(route["id"]) != "suspend_route":
            for school_id in route["serves"]:
                if state_of(school_id) == "suspend_classes":
                    problems.append(
                        f"学校 {school_id} 已停课，但校车线路 {route['id']} 仍显示正常运营"
                    )
            for road_id in route["uses_roads"]:
                if state_of(road_id) == "close_road":
                    problems.append(
                        f"道路 {road_id} 已封闭，但途经的校车线路 {route['id']} 仍显示正常运营"
                    )
    return problems


def issue_order(domain, actor, action, target_id, issued_at, effective_at, order_id, note=None):
    """签发命令。权限不符、恢复条件不满足或与其他决定矛盾时拒绝并记录提案。"""
    issued = timeline.parse_time(issued_at)
    effective = timeline.parse_time(effective_at)
    if effective < issued:
        raise DomainError("生效时刻不得早于签发时刻")
    _check_authority(domain, actor, action, target_id)
    target_kind = _target_kind(domain, target_id)
    cells = target_cells(domain, target_kind, target_id)
    order = {
        "type": "order",
        "id": order_id,
        "time": issued_at,
        "issued_at": issued_at,
        "effective_at": effective_at,
        "actor": actor["name"],
        "authority": actor["authority"],
        "action": action,
        "target": target_id,
        "target_kind": target_kind,
        "cells": cells,
        "note": note,
    }

    def reject(reasons):
        timeline.append_event(
            domain,
            {
                "type": "order_rejected",
                "id": f"{order_id}-rejected",
                "time": issued_at,
                "actor": actor["name"],
                "action": action,
                "target": target_id,
                "proposed_effective_at": effective_at,
                "reasons": list(reasons),
            },
        )
        raise DomainError("命令被拒绝: " + "；".join(reasons))

    if action in RESUME_CONDITIONS:
        # 恢复类命令：必须满足写明的观测条件，且不得与仍在生效的限制矛盾
        evaluation = evaluate_resume_conditions(domain, target_kind, target_id, effective)
        if not evaluation["ok"]:
            reject(evaluation["failures"])
        contradictions = find_contradictions(domain, effective, extra_orders=[order])
        if contradictions:
            reject(contradictions)
    # 限制类命令（停课/停运/封路）是安全方向，签发时不做矛盾检查；
    # 各部门决定之间的矛盾由对外发布闸门统一拦截（见 bulletin.try_publish）

    timeline.append_event(domain, order)
    return order


def revoke_order(domain, actor, order_id, revoked_at, reason):
    """撤销已签发的命令（如人工误报引发的错误封路）。原命令与撤销时刻都保留。"""
    order = _find_order(domain, order_id)
    if actor.get("authority") != order["authority"]:
        raise DomainError(f"{actor['name']} 无权撤销 {order['authority']} 部门签发的命令")
    event = {
        "type": "order_revoked",
        "id": f"{order_id}-revoked",
        "time": revoked_at,
        "order_id": order_id,
        "actor": actor["name"],
        "reason": reason,
    }
    timeline.append_event(domain, event)
    return event


def issue_warning(domain, actor, warning_id, level, cells, issued_at, valid_from, valid_to, note=None, supersedes=None):
    """发布预警：落区以统一格点给出，有效期可跨午夜。

    supersedes 列出被取代的旧预警（升级或雨带移动），旧预警保留在
    事件流中但不再生效。
    """
    if actor.get("authority") != "meteorology":
        raise DomainError("预警只能由气象部门签发")
    event = {
        "type": "warning",
        "id": warning_id,
        "time": issued_at,
        "issued_at": issued_at,
        "level": level,
        "cells": list(cells),
        "valid_from": valid_from,
        "valid_to": valid_to,
        "supersedes": list(supersedes or []),
        "note": note,
    }
    timeline.append_event(domain, event)
    return event


def file_manual_report(domain, actor, report_id, target_id, reported_at, valid_to, summary):
    """登记人工上报（如道路积水）。上报可能误报，撤回前按有效处理。"""
    event = {
        "type": "manual_report",
        "id": report_id,
        "time": reported_at,
        "reported_at": reported_at,
        "valid_to": valid_to,
        "reporter": actor["name"],
        "target": target_id,
        "summary": summary,
    }
    timeline.append_event(domain, event)
    return event


def retract_manual_report(domain, actor, report_id, retracted_at, reason):
    """撤回人工上报（确认误报）。原上报与撤回时刻都保留。"""
    timeline.manual_report(domain, report_id)
    event = {
        "type": "report_retracted",
        "id": f"{report_id}-retracted",
        "time": retracted_at,
        "report_id": report_id,
        "actor": actor["name"],
        "reason": reason,
    }
    timeline.append_event(domain, event)
    return event
