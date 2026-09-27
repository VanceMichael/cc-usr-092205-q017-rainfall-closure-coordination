"""统一预报格点与空间相交计算。

所有部门共用同一套格点：预警落区、实况雨量、地灾点、积水道路、
学校、校车线路和避险场所都先映射到格点，再按“真正相交”判定影响范围，
避免各部门按各自落区各算各的。
"""

from .errors import DomainError


def _bbox(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _bbox_overlap(a, b):
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def _point_on_segment(px, py, ax, ay, bx, by, eps=1e-9):
    cross = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
    if abs(cross) > eps:
        return False
    return (
        min(ax, bx) - eps <= px <= max(ax, bx) + eps
        and min(ay, by) - eps <= py <= max(ay, by) + eps
    )


def _point_in_polygon(px, py, polygon):
    """射线法，边界上的点视为在多边形内。"""
    n = len(polygon)
    for i in range(n):
        ax, ay = polygon[i]
        bx, by = polygon[(i + 1) % n]
        if _point_on_segment(px, py, ax, ay, bx, by):
            return True
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > py) != (yj > py):
            x_cross = (xj - xi) * (py - yi) / (yj - yi) + xi
            if px < x_cross:
                inside = not inside
        j = i
    return inside


def _segments_intersect(a1, a2, b1, b2):
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    o1 = orient(a1, a2, b1)
    o2 = orient(a1, a2, b2)
    o3 = orient(b1, b2, a1)
    o4 = orient(b1, b2, a2)
    if o1 * o2 < 0 and o3 * o4 < 0:
        return True
    for p, q, r in ((a1, a2, b1), (a1, a2, b2), (b1, b2, a1), (b1, b2, a2)):
        if _point_on_segment(r[0], r[1], p[0], p[1], q[0], q[1]):
            return True
    return False


def _polygons_intersect(pa, pb):
    if not _bbox_overlap(_bbox(pa), _bbox(pb)):
        return False
    for i in range(len(pa)):
        for j in range(len(pb)):
            if _segments_intersect(pa[i], pa[(i + 1) % len(pa)], pb[j], pb[(j + 1) % len(pb)]):
                return True
    return _point_in_polygon(*pa[0], pb) or _point_in_polygon(*pb[0], pa)


def _polyline_intersects_polygon(line, polygon):
    if not _bbox_overlap(_bbox(line), _bbox(polygon)):
        return False
    for i in range(len(line) - 1):
        for j in range(len(polygon)):
            if _segments_intersect(line[i], line[i + 1], polygon[j], polygon[(j + 1) % len(polygon)]):
                return True
    return any(_point_in_polygon(x, y, polygon) for x, y in line)


def _entity_geometry(entity):
    """从资料对象中取出几何：point / polygon / polyline 三选一。"""
    for key in ("point", "polygon", "polyline"):
        if key in entity:
            return key, entity[key]
    raise DomainError("对象缺少几何信息")


def geometries_intersect(entity_a, entity_b):
    """判断两个带几何信息的资料对象是否真正相交。"""
    kind_a, geom_a = _entity_geometry(entity_a)
    kind_b, geom_b = _entity_geometry(entity_b)
    if kind_a == "point" and kind_b == "point":
        return geom_a == geom_b
    if kind_a == "point":
        return _point_in_geometry(geom_a, kind_b, geom_b)
    if kind_b == "point":
        return _point_in_geometry(geom_b, kind_a, geom_a)
    if kind_a == "polygon" and kind_b == "polygon":
        return _polygons_intersect(geom_a, geom_b)
    if kind_a == "polyline" and kind_b == "polyline":
        if not _bbox_overlap(_bbox(geom_a), _bbox(geom_b)):
            return False
        return any(
            _segments_intersect(geom_a[i], geom_a[i + 1], geom_b[j], geom_b[j + 1])
            for i in range(len(geom_a) - 1)
            for j in range(len(geom_b) - 1)
        )
    if kind_a == "polyline":
        return _polyline_intersects_polygon(geom_a, geom_b)
    return _polyline_intersects_polygon(geom_b, geom_a)


def _point_in_geometry(point, kind, geom):
    if kind == "polygon":
        return _point_in_polygon(point[0], point[1], geom)
    if kind == "polyline":
        return any(
            _point_on_segment(point[0], point[1], geom[i][0], geom[i][1], geom[i + 1][0], geom[i + 1][1])
            for i in range(len(geom) - 1)
        )
    return point == geom


def cells_covering(domain, entity):
    """返回与对象真正相交的格点编号列表（按资料顺序）。"""
    return [
        cell["id"]
        for cell in domain["cells"]
        if geometries_intersect({"polygon": cell["polygon"]}, entity)
    ]


def target_cells(domain, target_kind, target_id):
    """命令作用对象（学校/道路/校车线路）覆盖的格点。"""
    collection = {
        "school": "schools",
        "road": "roads",
        "bus_route": "bus_routes",
    }.get(target_kind)
    if collection is None:
        raise DomainError(f"未知作用对象类型: {target_kind}")
    for entity in domain[collection]:
        if entity["id"] == target_id:
            return cells_covering(domain, entity)
    raise DomainError(f"找不到作用对象: {target_kind}/{target_id}")


def affected_targets(domain, cells):
    """预警或雨带覆盖给定格点时，真正相交的学校、道路、校车线路等对象。"""
    selected = [c for c in domain["cells"] if c["id"] in set(cells)]
    result = {}
    for kind, collection in (
        ("schools", "schools"),
        ("roads", "roads"),
        ("bus_routes", "bus_routes"),
        ("hazard_sites", "hazard_sites"),
        ("shelters", "shelters"),
    ):
        hits = []
        for entity in domain[collection]:
            if any(geometries_intersect({"polygon": c["polygon"]}, entity) for c in selected):
                hits.append(entity["id"])
        result[kind] = hits
    return result
