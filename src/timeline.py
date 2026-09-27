"""时间线：只追加事件、按时刻回放状态。

事件（预警、命令、人工上报、撤回、拒绝）一旦写入不再修改，
签发时刻、生效时刻、撤销时刻各自保留，跨午夜直接按 ISO 时刻比较。
"""

from datetime import datetime, timedelta, timezone

from .errors import DomainError

WARNING_RANK = {"蓝色": 1, "黄色": 2, "橙色": 3, "红色": 4}


def parse_time(text):
    """解析 ISO 8601 时刻，要求带时区。"""
    value = datetime.fromisoformat(text)
    if value.tzinfo is None:
        raise DomainError(f"时刻缺少时区: {text}")
    return value


def format_time(value):
    return value.isoformat()


def append_event(domain, event):
    """只追加：新事件的时刻不得早于已有最后一个事件。"""
    events = domain["events"]
    if events:
        last = parse_time(events[-1]["time"])
        current = parse_time(event["time"])
        if current < last:
            raise DomainError("事件必须按时间顺序追加，不允许回写历史")
    events.append(event)
    return event


def events_until(domain, at):
    """截至某时刻（含）已发生的全部事件，原始时刻原样保留。"""
    return [e for e in domain["events"] if parse_time(e["time"]) <= at]


def _active_interval(event, at, start_key, end_key):
    start = parse_time(event[start_key])
    end_text = event.get(end_key)
    if start > at:
        return False
    if end_text is None:
        return True
    return at < parse_time(end_text)


def active_warnings(domain, at):
    """某时刻处于有效期内的预警（valid_from <= at < valid_to，支持跨午夜）。

    被后一预警明确取代（supersedes）的旧预警不再生效；雨带移动后
    旧落区自然失效。取代关系只在新预警自身也处于有效期时成立。
    """
    active = [
        e
        for e in domain["events"]
        if e["type"] == "warning" and _active_interval(e, at, "valid_from", "valid_to")
    ]
    superseded = {old for w in active for old in w.get("supersedes", [])}
    return [w for w in active if w["id"] not in superseded]


def warning_at(domain, at, cell_id=None):
    """某时刻（某格点）的最高预警等级；无预警返回 None。"""
    best = None
    for w in active_warnings(domain, at):
        if cell_id is not None and cell_id not in w["cells"]:
            continue
        if best is None or WARNING_RANK[w["level"]] > WARNING_RANK[best["level"]]:
            best = w
    return best


def active_manual_reports(domain, at):
    """某时刻仍有效的人工上报；已撤回的保留在历史中但不再生效。"""
    retracted = {
        e["report_id"] for e in events_until(domain, at) if e["type"] == "report_retracted"
    }
    result = []
    for e in domain["events"]:
        if e["type"] != "manual_report" or e["id"] in retracted:
            continue
        if _active_interval(e, at, "reported_at", "valid_to"):
            result.append(e)
    return result


def manual_report(domain, report_id):
    for e in domain["events"]:
        if e["type"] == "manual_report" and e["id"] == report_id:
            return e
    raise DomainError(f"找不到人工上报: {report_id}")


def rainfall_series(domain, station_id):
    for series in domain["rainfall_series"]:
        if series["station_id"] == station_id:
            return series
    raise DomainError(f"找不到雨量序列: {station_id}")


def _bucket_time(series, index):
    return parse_time(series["start"]) + timedelta(minutes=series["interval_minutes"]) * index


def rainfall_readings(domain, station_id, since, until):
    """取 [since, until) 内的逐桶观测；value 为 None 表示该时段传感器离线。"""
    series = rainfall_series(domain, station_id)
    out = []
    for i, value in enumerate(series["values"]):
        t = _bucket_time(series, i)
        if since <= t < until:
            out.append({"time": t, "value": value})
    return out


def rainfall_sum(domain, station_id, since, until):
    """窗口累计雨量（毫米）；离线时段不计入，由调用方先检查空窗。"""
    return sum(r["value"] for r in rainfall_readings(domain, station_id, since, until) if r["value"] is not None)


def rainfall_gap(domain, station_id, since, until):
    """窗口内是否存在离线时段或覆盖不足（窗口两端超出已有观测也算空窗）。"""
    series = rainfall_series(domain, station_id)
    step = timedelta(minutes=series["interval_minutes"])
    covered_until = parse_time(series["start"]) + step * len(series["values"])
    if since < parse_time(series["start"]) or until > covered_until:
        return True
    return any(r["value"] is None for r in rainfall_readings(domain, station_id, since, until))


def station_offline(domain, station_id, at):
    """某时刻传感器是否离线：所在观测桶无数据，或该时刻已超出序列末端。"""
    series = rainfall_series(domain, station_id)
    start = parse_time(series["start"])
    step = timedelta(minutes=series["interval_minutes"])
    index = (at - start) // step
    if index < 0 or index >= len(series["values"]):
        return True
    return series["values"][index] is None


def order_events(domain, at=None):
    """命令类事件（含签发、撤销、恢复、被拒绝的提案），按时刻升序。"""
    kinds = {"order", "order_revoked", "order_rejected"}
    events = domain["events"] if at is None else events_until(domain, at)
    return [e for e in events if e["type"] in kinds]


def current_states(domain, at, basis="effective"):
    """回放至某时刻，得到每个作用对象的当前状态。

    basis="effective"：只计入已生效（effective_at <= at）的命令，用于对外展示；
    basis="issued"：计入已签发（issued_at <= at）的命令，即使生效时刻在未来，
    用于发布前矛盾检查——通知签发即可能被公众看到。
    返回 {target_id: {"action", "order_id", "issued_at", "effective_at", "cells"}}。
    被撤销的命令视为从未生效（历史仍保留在事件流中）；
    恢复上课/解封/恢复运营通过对应的恢复命令生效。
    """
    revoked = set()
    orders = {}
    sequence = []
    for e in order_events(domain, at):
        if e["type"] == "order_revoked":
            revoked.add(e["order_id"])
        elif e["type"] == "order":
            orders[e["id"]] = e
            sequence.append(e["id"])
    time_key = "effective_at" if basis == "effective" else "issued_at"
    states = {}
    for order_id in sequence:
        if order_id in revoked:
            continue
        order = orders[order_id]
        if parse_time(order[time_key]) > at:
            continue
        states[order["target"]] = {
            "action": order["action"],
            "order_id": order["id"],
            "issued_at": order["issued_at"],
            "effective_at": order["effective_at"],
            "cells": order["cells"],
        }
    return states
