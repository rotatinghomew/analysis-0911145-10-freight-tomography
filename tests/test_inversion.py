import unittest

from app.inversion import (Polytope, center, infer, is_feasible, value_range)
from app.store import load_case

import numpy as np


def standard():
    return infer(load_case('standard'))


class PolytopeEngineTests(unittest.TestCase):
    def _box_poly(self):
        poly = Polytope(2)
        poly.add(np.array([1.0, 0.0]), 4.0, '<=', {'id': 'x-cap'})
        poly.add(np.array([0.0, 1.0]), 6.0, '<=', {'id': 'y-cap'})
        return poly

    def test_extrema_return_exact_box_ranges(self):
        poly = self._box_poly()
        self.assertTrue(is_feasible(poly))
        self.assertEqual(value_range(poly, np.array([1.0, 0.0])), (0.0, 4.0))
        self.assertEqual(value_range(poly, np.array([0.0, 1.0])), (0.0, 6.0))
        self.assertEqual(value_range(poly, np.array([1.0, 1.0])), (0.0, 10.0))

    def test_equality_rows_are_enforced(self):
        poly = self._box_poly()
        poly.add(np.array([1.0, 1.0]), 5.0, '==', {'id': 'sum'})
        point = center(poly, 5.0)
        self.assertAlmostEqual(float(point[0] + point[1]), 5.0, places=6)
        low, high = value_range(poly, np.array([1.0, 0.0]))
        self.assertAlmostEqual(low, 0.0, places=6)
        self.assertAlmostEqual(high, 4.0, places=6)

    def test_infeasible_polytope_is_detected(self):
        poly = Polytope(1)
        poly.add(np.array([1.0]), 1.0, '<=', {'id': 'cap'})
        poly.add(np.array([1.0]), 2.0, '>=', {'id': 'floor'})
        self.assertFalse(is_feasible(poly))


class InversionEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.result = standard()
        self.od = {row['od']: row for row in self.result['od']}

    def test_od_points_are_inside_identified_intervals(self):
        for row in self.result['od']:
            self.assertLessEqual(row['identified']['lower'] - 1e-9, row['point'])
            self.assertLessEqual(row['point'], row['identified']['upper'] + 1e-9)

    def test_identified_ranges_honor_counter_and_business_bounds(self):
        # a-h box counter fixes the a-origin total at exactly 180.
        east = self.od['a-east']
        south = self.od['a-south']
        self.assertAlmostEqual(east['identified']['upper'], 180.0, places=5)
        self.assertAlmostEqual(south['identified']['upper'], 180.0, places=5)
        # The two a-origin ODs share the 180 budget, so their minima are zero.
        self.assertAlmostEqual(east['identified']['lower'], 0.0, places=5)
        self.assertAlmostEqual(south['identified']['lower'], 0.0, places=5)
        # b-h availability-scaled counter fixes the b-origin total at 150.
        self.assertAlmostEqual(self.od['b-east']['identified']['upper'], 150.0, places=5)
        self.assertAlmostEqual(self.od['b-south']['identified']['upper'], 150.0, places=5)
        # Total business bound 280..380 constrains the linear combination of
        # all OD flows, not the sum of their individual coordinate minima.
        from app.inversion import _active_paths, _build_hard_polytope
        case = load_case('standard')
        holdout = {counter['id'] for counter in case['counters'] if counter['holdout']}
        paths = _active_paths(case)
        poly = _build_hard_polytope(paths, case, [], holdout)
        total = np.ones(len(paths))
        low, high = value_range(poly, total)
        self.assertGreaterEqual(low, 280.0 - 1e-6)
        self.assertLessEqual(high, 380.0 + 1e-6)

    def test_truck_counter_becomes_a_box_interval_not_a_point(self):
        truck = next(row for row in self.result['fitted'] if row['counter'] == 'x')
        self.assertEqual(truck['box_interval'], [120.0, 260.0])
        low, high = truck['predicted_interval']
        self.assertGreaterEqual(low, 120.0 - 1e-6)
        self.assertLessEqual(high, 260.0 + 1e-6)

    def test_offline_counter_is_a_missing_observation_not_zero(self):
        offline = next(row for row in self.result['fitted'] if row['counter'] == 'y')
        self.assertEqual(offline['status'], 'offline')
        self.assertIsNone(offline['box_interval'])
        self.assertTrue(any(row['code'] == 'OFFLINE_COUNTER'
                            for row in self.result['diagnostics']))

    def test_sample_layer_only_narrows_the_assumption_range(self):
        for row in self.result['od']:
            self.assertLessEqual(row['identified']['lower'] - 1e-9,
                                 row['assumption']['lower'])
            self.assertGreaterEqual(row['identified']['upper'] + 1e-9,
                                    row['assumption']['upper'])
            self.assertLessEqual(row['assumption']['width'], row['identified']['width'] + 1e-9)
        # Sample bands per path: ae-x in [48,160], ae-y in [20,80], so the
        # a-east assumption floor is the sum of both path floors = 68.
        self.assertAlmostEqual(self.od['a-east']['assumption']['lower'], 68.0, places=5)
        self.assertTrue(any(row['code'] == 'SAMPLE_SELECTION_BIAS'
                            for row in self.result['diagnostics']))

    def test_unidentified_flows_are_not_claimed_as_identified(self):
        self.assertTrue(all(not row['identified_by_data'] for row in self.result['od']))
        self.assertTrue(any(row['code'] == 'POINT_NOT_IDENTIFIED'
                            for row in self.result['diagnostics']))

    def test_point_estimate_is_interior_not_a_zero_vertex(self):
        # The analytic centre keeps flows with a strictly positive feasible
        # range away from zero, instead of collapsing them onto the vertex.
        # ODs whose entire range touches zero (a-south, b-south) may stay at
        # zero; the two origin totals fixed by box counters are fully used.
        positive = {row['od']: row['point'] for row in self.result['od']
                    if row['identified']['lower'] == 0.0
                    and row['identified']['upper'] > 0.0
                    and any(sample['path'].startswith(row['od'][0])
                            for sample in load_case('standard')['samples'])}
        self.assertGreater(positive['a-east'], 1.0)
        self.assertGreater(positive['b-east'], 1.0)


class HoldoutAndEdgeCaseTests(unittest.TestCase):
    def test_holdout_counter_validates_but_never_fits(self):
        result = standard()
        holdout = next(row for row in result['validation'] if row['counter'] == 'e')
        self.assertFalse(holdout['used_in_fitting'])
        self.assertEqual(holdout['observed_box_interval'], [150.0, 150.0])
        self.assertTrue(holdout['covered'])
        self.assertFalse({row['counter'] for row in result['fitted']} & {'e'})

    def test_contradictory_case_is_relaxed_and_flagged(self):
        result = infer(load_case('edge'))
        self.assertTrue(result['relaxed_fit'])
        self.assertTrue(result['relaxed_constraints'])
        codes = {row['code'] for row in result['diagnostics']}
        self.assertIn('INFEASIBLE_EVIDENCE', codes)
        self.assertIn('RELAXED_CONSTRAINTS', codes)
        # Relaxed output still carries complete, finite intervals.
        for row in result['od']:
            self.assertGreaterEqual(row['identified']['upper'], row['identified']['lower'] - 1e-9)
        # Holdout validation still runs on the relaxed fit.
        self.assertTrue(all('used_in_fitting' in row for row in result['validation']))

    def test_edge_case_is_distinguishable_from_standard(self):
        self.assertNotEqual(standard()['snapshot'],
                            infer(load_case('edge'))['snapshot'])


class ScenarioTests(unittest.TestCase):
    def test_closure_does_not_refit_and_marks_unserved_od(self):
        result = standard()
        scenario = result['scenarios']
        self.assertEqual(scenario['closed_edges'], ['h-x'])
        self.assertNotIn('ae-x', scenario['surviving_paths'])
        unserved = next(row for row in scenario['demand_unchanged']['od']
                        if row['od'] == 'a-south')
        self.assertTrue(unserved['unserved'])
        self.assertEqual(unserved['served_interval']['upper'], 0.0)
        self.assertTrue(any(row['code'] == 'SCENARIO_SEPARATION'
                            for row in result['diagnostics']))

    def test_reroute_band_is_separate_from_demand_unchanged(self):
        result = standard()
        reroute = result['scenarios']['reroute_band']
        self.assertEqual(reroute['band'], [0.4, 1.0])
        a_east = next(row for row in reroute['od'] if row['od'] == 'a-east')
        point = next(row for row in result['od'] if row['od'] == 'a-east')['point']
        self.assertAlmostEqual(a_east['served_interval']['lower'], 0.4 * point, places=5)
        self.assertAlmostEqual(a_east['served_interval']['upper'], point, places=5)

    def test_closure_override_without_reroute_band(self):
        result = infer(load_case('standard'),
                       closure={'edges': ['h-y'], 'reroute_band': None})
        scenario = result['scenarios']
        self.assertEqual(scenario['closed_edges'], ['h-y'])
        self.assertIsNone(scenario['reroute_band'])
        # b-south only uses bs-y through h-y, so it becomes unserved.
        unserved = {row['od'] for row in scenario['demand_unchanged']['od']
                    if row['unserved']}
        self.assertEqual(unserved, {'b-south'})

    def test_robust_findings_include_closed_edge_and_unserved_od(self):
        kinds = {(row['kind'], row.get('edge', row.get('od')))
                 for row in standard()['scenarios']['robust_findings']}
        self.assertIn(('closed_edge', 'h-x'), kinds)
        self.assertIn(('unserved_od', 'a-south'), kinds)

    def test_invalid_closure_is_rejected(self):
        with self.assertRaises(ValueError):
            infer(load_case('standard'),
                  closure={'edges': ['no-such-edge'], 'reroute_band': None})
        with self.assertRaises(ValueError):
            infer(load_case('standard'),
                  closure={'edges': ['h-x'], 'reroute_band': [0.8, 0.2]})


class InformationValueTests(unittest.TestCase):
    def test_probes_exclude_holdout_and_precise_box_edges(self):
        result = standard()
        edges = {row['edge'] for row in result['information_value']['candidates']}
        self.assertNotIn('x-e', edges)       # holdout edge
        self.assertNotIn('a-h', edges)       # already a precise box counter
        self.assertIn('h-x', edges)          # truck-only: a box probe adds value
        self.assertIn('h-y', edges)          # offline device

    def test_probe_ranking_orders_uncertainty_reduction(self):
        candidates = standard()['information_value']['candidates']
        reductions = [row['total_od_width_reduction'] for row in candidates]
        self.assertEqual(reductions, sorted(reductions, reverse=True))
        self.assertEqual(candidates[0]['rank'], 1)
        for row in candidates:
            self.assertGreaterEqual(row['total_od_width_reduction'], 0.0)

    def test_probe_is_labelled_counterfactual(self):
        self.assertTrue(any(row['code'] == 'VOI_SYNTHETIC_PROBE'
                            for row in standard()['diagnostics']))

    def test_probe_reports_joint_allocation_uncertainty(self):
        # The truck-only h-x counter leaves the x/y routing split wide open:
        # a box probe there removes path-level allocation uncertainty even
        # though each marginal OD interval is unchanged.
        top = standard()['information_value']['candidates'][0]
        self.assertEqual(top['edge'], 'h-x')
        self.assertEqual(sum(top['od_width_reduction'].values()), 0.0)
        self.assertGreater(top['joint_allocation_spread_reduction'], 0.0)


if __name__ == '__main__':
    unittest.main()
