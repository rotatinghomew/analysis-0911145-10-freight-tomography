import unittest
from app.counters import box_interval
from app.flows import incidence, project_path_flows
from app.samples import sample_intervals
from app.store import load_case


class TomographyTests(unittest.TestCase):
    def test_missing_counter_is_not_zero(self):
        counter = next(row for row in load_case('standard')['counters'] if row['id'] == 'y')
        self.assertIsNone(box_interval(counter))

    def test_truck_unit_is_interval_not_boxes(self):
        counter = next(row for row in load_case('standard')['counters'] if row['id'] == 'x')
        self.assertEqual(box_interval(counter), (120, 260))

    def test_shared_edges_sum_path_contributions(self):
        projected = project_path_flows(load_case('standard'), {'ae-x': 10, 'be-x': 20})
        self.assertEqual(projected['h-x'], 30)
        self.assertEqual(projected['a-h'], 10)

    def test_closure_does_not_create_demand_observation(self):
        reduced = incidence(load_case('standard'), ['h-x'])
        self.assertNotIn('ae-x', reduced['paths'])
        self.assertIn('ae-y', reduced['paths'])
