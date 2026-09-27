import unittest
from pathlib import Path

from src.domain import load_domain
from src.geo import affected_targets, cells_covering, geometries_intersect


def load():
    return load_domain(Path("fixtures/domain.json"))


class GeoTest(unittest.TestCase):
    def test_targets_map_to_unified_cells(self):
        domain = load()
        self.assertEqual(cells_covering(domain, _get(domain, "schools", "S1")), ["G1"])
        self.assertEqual(cells_covering(domain, _get(domain, "schools", "S2")), ["G3"])
        # 跨区道路贯穿两个格点
        self.assertEqual(cells_covering(domain, _get(domain, "roads", "R1")), ["G1", "G2"])
        self.assertEqual(cells_covering(domain, _get(domain, "bus_routes", "B2")), ["G2", "G4"])

    def test_cross_district_road_intersects_both_districts(self):
        domain = load()
        r1 = _get(domain, "roads", "R1")
        hit = [
            d["id"]
            for d in domain["districts"]
            if geometries_intersect({"polygon": d["polygon"]}, r1)
        ]
        self.assertEqual(hit, ["D1", "D2"])

    def test_warning_zone_affects_only_intersecting_targets(self):
        domain = load()
        # W1 落区 G1、G2：S2（G3）与 R3（G3）不相交，不受影响
        affected = affected_targets(domain, ["G1", "G2"])
        self.assertEqual(affected["schools"], ["S1", "S3"])
        self.assertEqual(affected["roads"], ["R1", "R2"])
        self.assertEqual(affected["bus_routes"], ["B1", "B2"])
        self.assertNotIn("S2", affected["schools"])
        self.assertNotIn("R3", affected["roads"])
        # 雨带移动后的 W3 落区 G3、G4：只影响新相交的对象
        moved = affected_targets(domain, ["G3", "G4"])
        self.assertEqual(moved["schools"], ["S2"])
        self.assertEqual(moved["bus_routes"], ["B2"])
        self.assertNotIn("S1", moved["schools"])


def _get(domain, collection, entity_id):
    return next(e for e in domain[collection] if e["id"] == entity_id)


if __name__ == "__main__":
    unittest.main()
