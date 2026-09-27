"""生成 fixtures/domain.json：重放 2026-07-12 暴雨夜联合处置全过程。

运行：python tools/build_fixture.py
样例不含真实个人信息，几何为虚构格点坐标。
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.bulletin import try_publish
from src.decisions import (
    file_manual_report,
    issue_order,
    issue_warning,
    retract_manual_report,
    revoke_order,
)
from src.errors import DomainError

TZ = "+08:00"
DAY1 = "2026-07-12"
DAY2 = "2026-07-13"


def ts(day, hhmm):
    return f"{day}T{hhmm}:00{TZ}"


# 30 分钟一桶的雨量剖面，首桶 2026-07-12T19:30+08:00，共 30 桶（至次日 10:30）
RAIN_START = ts(DAY1, "19:30")
P1 = [0, 0, 1, 4, 8, 10, 9, 7, 5, 3, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
P2 = [0, 0, 1, 3, 7, 9, 10, 9, 8, 6, 4, 2, 1, None, None, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
P3 = [0, 0, 0, 0, 1, 2, 4, 6, 9, 11, 10, 10, 9, 8, 6, 4, 3, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
P4 = [0, 0, 0, 0, 0, 1, 2, 3, 4, 2, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]


def build():
    domain = {
        "domain": "rainfall-closure-coordination",
        "version": 2,
        "sample_id": "record-018",
        "actors": ["业务负责人", "一线执行人员", "审查人员"],
        "facts": [
            "重庆和湖北西部等地可能出现暴雨或大暴雨",
            "持续阴雨会增加山区和河谷地质灾害风险",
            "气象部门提醒公众注意交通安全并避开隐患点",
            "预警落区、实况雨量、地灾点、积水道路、学校、校车线路和避险场所统一映射到同一套预报格点",
            "预警升级或雨带移动只影响与落区真正相交的对象",
            "停课由教育部门签发，校车停运由交通部门签发，封路由应急部门签发",
            "恢复上课或道路解封必须满足写明的观测条件，传感器离线期间不得解封",
            "跨区道路、跨午夜降雨、人工误报与命令撤销都保留原始时刻",
        ],
        "constraints": [
            "多源雨情",
            "影响范围计算",
            "联合命令",
            "公众通知",
            "恢复条件核验",
            "矛盾发布拦截",
        ],
        "cells": [
            {"id": "G1", "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]]},
            {"id": "G2", "polygon": [[1, 0], [2, 0], [2, 1], [1, 1]]},
            {"id": "G3", "polygon": [[0, 1], [1, 1], [1, 2], [0, 2]]},
            {"id": "G4", "polygon": [[1, 1], [2, 1], [2, 2], [1, 2]]},
        ],
        "districts": [
            {"id": "D1", "name": "临江县", "polygon": [[0, 0], [1, 0], [1, 2], [0, 2]]},
            {"id": "D2", "name": "河谷区", "polygon": [[1, 0], [2, 0], [2, 2], [1, 2]]},
        ],
        "schools": [
            {"id": "S1", "name": "临江一小", "district": "D1", "point": [0.5, 0.5]},
            {"id": "S2", "name": "临江二小", "district": "D1", "point": [0.5, 1.5]},
            {"id": "S3", "name": "河谷小学", "district": "D2", "point": [1.5, 0.5]},
        ],
        "roads": [
            {"id": "R1", "name": "滨江路", "polyline": [[0.2, 0.5], [1.8, 0.5]]},
            {"id": "R2", "name": "山前公路", "polyline": [[1.5, 0.2], [1.5, 1.8]]},
            {"id": "R3", "name": "西环路", "polyline": [[0.5, 1.2], [0.5, 1.8]]},
        ],
        "bus_routes": [
            {
                "id": "B1",
                "name": "校车一号线",
                "serves": ["S1", "S3"],
                "uses_roads": ["R1"],
                "polyline": [[0.5, 0.5], [1.5, 0.5]],
            },
            {
                "id": "B2",
                "name": "校车二号线",
                "serves": ["S3"],
                "uses_roads": ["R2"],
                "polyline": [[1.5, 0.5], [1.5, 1.5]],
            },
        ],
        "hazard_sites": [
            {"id": "H1", "name": "老鹰岩滑坡隐患点", "point": [0.6, 0.7]},
            {"id": "H2", "name": "河谷泥石流隐患点", "point": [1.6, 0.4]},
        ],
        "shelters": [
            {"id": "K1", "name": "临江中学体育馆", "point": [0.4, 0.6]},
            {"id": "K2", "name": "河谷文化中心", "point": [1.6, 0.6]},
            {"id": "K3", "name": "北山社区中心", "point": [0.6, 1.6]},
        ],
        "rainfall_stations": [
            {"id": "P1", "cell": "G1", "point": [0.3, 0.3]},
            {"id": "P2", "cell": "G2", "point": [1.7, 0.3]},
            {"id": "P3", "cell": "G3", "point": [0.3, 1.7]},
            {"id": "P4", "cell": "G4", "point": [1.7, 1.7]},
        ],
        "rainfall_series": [
            {"station_id": "P1", "start": RAIN_START, "interval_minutes": 30, "values": P1},
            {"station_id": "P2", "start": RAIN_START, "interval_minutes": 30, "values": P2},
            {"station_id": "P3", "start": RAIN_START, "interval_minutes": 30, "values": P3},
            {"station_id": "P4", "start": RAIN_START, "interval_minutes": 30, "values": P4},
        ],
        "events": [],
    }

    met = {"name": "值班气象员", "authority": "meteorology"}
    edu1 = {"name": "教育值班员·临江县", "authority": "education", "district": "D1"}
    edu2 = {"name": "教育值班员·河谷区", "authority": "education", "district": "D2"}
    traffic = {"name": "交通值班员", "authority": "transport"}
    emergency = {"name": "应急值班员", "authority": "emergency"}
    patrol = {"name": "道路巡查员", "authority": "field"}

    def attempt(actor, action, target, issued, effective, order_id, note=None):
        """尝试签发；被拒绝的提案由系统记录后继续重放。"""
        try:
            return issue_order(domain, actor, action, target, issued, effective, order_id, note)
        except DomainError:
            return None

    # 19:50 橙色预警覆盖 G1、G2，有效期跨午夜到次日 02:00
    issue_warning(domain, met, "W1", "橙色", ["G1", "G2"], ts(DAY1, "19:50"), ts(DAY1, "20:00"), ts(DAY2, "02:00"))
    # 20:05 教育部门先停课，校车系统尚未联动——20:08 的对外发布被闸门拦截并留痕
    issue_order(domain, edu1, "suspend_classes", "S1", ts(DAY1, "20:05"), ts(DAY1, "20:10"), "O1")
    try:
        try_publish(domain, 1, ts(DAY1, "20:08"))
    except DomainError:
        pass
    issue_order(domain, traffic, "suspend_route", "B1", ts(DAY1, "20:15"), ts(DAY1, "20:20"), "O2")
    issue_order(domain, traffic, "suspend_route", "B2", ts(DAY1, "20:16"), ts(DAY1, "20:20"), "O3")
    issue_order(domain, edu2, "suspend_classes", "S3", ts(DAY1, "20:18"), ts(DAY1, "20:25"), "O4")
    issue_order(domain, emergency, "close_road", "R2", ts(DAY1, "20:20"), ts(DAY1, "20:30"), "O5")
    # 矛盾消除后发布第 2 版通告
    try_publish(domain, 2, ts(DAY1, "20:35"))
    # 21:10 值班员误把不相交的 R3 纳入封路，21:40 复核撤销，两条时刻都保留
    issue_order(domain, emergency, "close_road", "R3", ts(DAY1, "21:10"), ts(DAY1, "21:20"), "O6", "误封")
    revoke_order(domain, emergency, "O6", ts(DAY1, "21:40"), "复核发现 R3 与预警落区不相交，属误封")
    # 21:45 封闭跨区道路 R1（贯穿临江县与河谷区）
    issue_order(domain, emergency, "close_road", "R1", ts(DAY1, "21:45"), ts(DAY1, "21:50"), "O7")
    # 21:50 预警升级为红色且落区扩大到 G3，取代 W1，只影响新相交的区域
    issue_warning(domain, met, "W2", "红色", ["G1", "G2", "G3"], ts(DAY1, "21:50"), ts(DAY1, "22:00"), ts(DAY2, "02:00"), supersedes=["W1"])
    issue_order(domain, edu1, "suspend_classes", "S2", ts(DAY1, "22:10"), ts(DAY1, "22:20"), "O9")
    try_publish(domain, 3, ts(DAY1, "22:30"))
    # 23:20 雨带移动，红色落区转为 G3、G4，取代 W2，跨午夜到次日 04:00
    issue_warning(domain, met, "W3", "红色", ["G3", "G4"], ts(DAY1, "23:20"), ts(DAY1, "23:30"), ts(DAY2, "04:00"), supersedes=["W2"])
    # 次日 01:20 人工上报 R2 积水，02:30 巡查确认误报并撤回，原始时刻保留
    file_manual_report(domain, patrol, "M1", "R2", ts(DAY2, "01:20"), ts(DAY2, "04:00"), "山前公路低洼段积水")
    # 02:00 申请解封 R2：窗口雨量超标且人工上报仍有效，被拒绝
    attempt(emergency, "reopen_road", "R2", ts(DAY2, "02:00"), ts(DAY2, "02:00"), "O10")
    retract_manual_report(domain, patrol, "M1", ts(DAY2, "02:30"), "巡查确认未积水，属误报")
    # 02:45 再次申请解封 R2：雨量站 P2 离线，观测条件无法核验，被拒绝
    attempt(emergency, "reopen_road", "R2", ts(DAY2, "02:45"), ts(DAY2, "03:00"), "O11")
    # 04:50 传感器恢复且条件满足，R2 于 05:00 解封；05:15 R1 于 05:30 解封
    issue_order(domain, emergency, "reopen_road", "R2", ts(DAY2, "04:50"), ts(DAY2, "05:00"), "O12")
    issue_order(domain, emergency, "reopen_road", "R1", ts(DAY2, "05:15"), ts(DAY2, "05:30"), "O13")
    # 06:30 申请恢复上课：S2 窗口雨量超标、S3 雨量站离线，均被拒绝
    attempt(edu1, "resume_classes", "S2", ts(DAY2, "06:30"), ts(DAY2, "07:00"), "O14")
    attempt(edu2, "resume_classes", "S3", ts(DAY2, "06:35"), ts(DAY2, "07:00"), "O15")
    # 06:50 S1 满足写明的观测条件，07:00 恢复上课
    issue_order(domain, edu1, "resume_classes", "S1", ts(DAY2, "06:50"), ts(DAY2, "07:00"), "O16")
    # 08:15 申请恢复 B1：S3 仍在停课，矛盾，被拒绝
    attempt(traffic, "resume_route", "B1", ts(DAY2, "08:15"), ts(DAY2, "08:30"), "O17")
    # 08:45 S2 条件满足，09:00 恢复上课
    issue_order(domain, edu1, "resume_classes", "S2", ts(DAY2, "08:45"), ts(DAY2, "09:00"), "O18")
    # 09:30 S3 条件满足，10:00 恢复上课；随后两条校车线路恢复运营
    issue_order(domain, edu2, "resume_classes", "S3", ts(DAY2, "09:30"), ts(DAY2, "10:00"), "O19")
    issue_order(domain, traffic, "resume_route", "B1", ts(DAY2, "09:35"), ts(DAY2, "10:00"), "O20")
    issue_order(domain, traffic, "resume_route", "B2", ts(DAY2, "09:36"), ts(DAY2, "10:00"), "O21")

    return domain


def main():
    domain = build()
    out = Path(__file__).resolve().parent.parent / "fixtures" / "domain.json"
    out.write_text(json.dumps(domain, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已生成 {out}，事件 {len(domain['events'])} 条")


if __name__ == "__main__":
    main()
