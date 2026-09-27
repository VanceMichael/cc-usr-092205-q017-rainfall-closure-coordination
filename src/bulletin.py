"""公众通告：单一版本、按当前位置与角色过滤。

每次对外发布生成一个带版本号的快照；家长、司机、救援人员
看到的是同一版本，只是按各自当前位置过滤出与其相交的当前有效行动，
不再依靠后续短信猜测哪条通知有效。
"""

from . import timeline
from .errors import DomainError
from .geo import cells_covering

# 各类角色关心的行动
ROLE_ACTIONS = {
    "parent": {"suspend_classes", "resume_classes", "suspend_route", "resume_route"},
    "driver": {"suspend_route", "resume_route", "close_road", "reopen_road"},
    "rescuer": {
        "suspend_classes",
        "resume_classes",
        "suspend_route",
        "resume_route",
        "close_road",
        "reopen_road",
    },
}

_ACTION_LABEL = {
    "suspend_classes": "停课",
    "resume_classes": "恢复上课",
    "suspend_route": "校车停运",
    "resume_route": "校车恢复运营",
    "close_road": "道路封闭",
    "reopen_road": "道路解封",
}

_TARGET_COLLECTION = {
    "school": "schools",
    "road": "roads",
    "bus_route": "bus_routes",
}


def build_bulletin(domain, version, at):
    """生成某时刻的对外通告快照（纯函数）。存在矛盾决定时抛出 DomainError。"""
    return try_publish(domain, version, at, persist=False)


def try_publish(domain, version, at, persist=True):
    """对外发布闸门：矛盾则记录 publish_blocked 事件并返回 None；
    一致则（可选）记录 bulletin_published 事件并返回该版本快照。

    矛盾按“已签发”决定判定；快照只展示当前“已生效”的行动。
    """
    from .decisions import find_contradictions

    at = timeline.parse_time(at) if isinstance(at, str) else at
    contradictions = find_contradictions(domain, at, basis="issued")
    if contradictions:
        if persist:
            timeline.append_event(
                domain,
                {
                    "type": "publish_blocked",
                    "id": f"PB-{version}",
                    "time": timeline.format_time(at),
                    "version": version,
                    "reasons": contradictions,
                },
            )
        raise DomainError("存在互相矛盾的决定，禁止对外发布: " + "；".join(contradictions))
    bulletin = _snapshot(domain, version, at)
    if persist:
        timeline.append_event(
            domain,
            {
                "type": "bulletin_published",
                "id": f"BP-{version}",
                "time": timeline.format_time(at),
                "version": version,
                "item_count": len(bulletin["items"]),
            },
        )
    return bulletin


def _snapshot(domain, version, at):
    states = timeline.current_states(domain, at)
    items = []
    for target_id, state in sorted(states.items()):
        order = None
        for e in domain["events"]:
            if e["type"] == "order" and e["id"] == state["order_id"]:
                order = e
                break
        items.append(
            {
                "target": target_id,
                "target_kind": order["target_kind"],
                "action": state["action"],
                "action_label": _ACTION_LABEL[state["action"]],
                "cells": state["cells"],
                "issued_at": state["issued_at"],
                "effective_at": state["effective_at"],
            }
        )
    warnings = [
        {
            "id": w["id"],
            "level": w["level"],
            "cells": w["cells"],
            "valid_from": w["valid_from"],
            "valid_to": w["valid_to"],
        }
        for w in timeline.active_warnings(domain, at)
    ]
    return {"version": version, "issued_at": timeline.format_time(at), "items": items, "warnings": warnings}


def _viewer_cells(domain, viewer):
    """按公众当前位置（所在格点）确定其关心的格点。"""
    cell_id = viewer.get("cell")
    if cell_id is None:
        raise DomainError("公众视图需要当前位置所在格点")
    if not any(c["id"] == cell_id for c in domain["cells"]):
        raise DomainError(f"未知格点: {cell_id}")
    return {cell_id}


def personalize(domain, bulletin, viewer):
    """从同一版本通告中过滤出与 viewer 当前位置相交、且其角色关心的内容。"""
    role = viewer.get("role")
    if role not in ROLE_ACTIONS:
        raise DomainError(f"未知角色: {role}")
    cells = _viewer_cells(domain, viewer)
    actions = ROLE_ACTIONS[role]

    items = [
        item
        for item in bulletin["items"]
        if item["action"] in actions and cells & set(item["cells"])
    ]
    warnings = [w for w in bulletin["warnings"] if cells & set(w["cells"])]

    # 附加与位置相交的避险场所，供所有角色参考
    shelters = []
    for shelter in domain["shelters"]:
        if cells & set(cells_covering(domain, shelter)):
            shelters.append(shelter["id"])

    return {
        "version": bulletin["version"],
        "issued_at": bulletin["issued_at"],
        "role": role,
        "cell": viewer["cell"],
        "items": items,
        "warnings": warnings,
        "shelters": shelters,
    }
