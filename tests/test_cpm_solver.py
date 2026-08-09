import unittest

from agent_pipeline import map_element_to_task
from cpm_solver import CPMSolver


class CPMSolverTests(unittest.TestCase):
    def test_foundation_delay_changes_project_duration(self):
        result = CPMSolver().calculate_cpm_impact("TASK-FOUNDATION", 5)
        self.assertEqual(result["baseline_project_duration_days"], 42)
        self.assertEqual(result["total_project_duration_days"], 47)
        self.assertEqual(result["delay_added"], 5)

    def test_invalid_task_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown schedule task"):
            CPMSolver().calculate_cpm_impact("TASK-UNKNOWN", 5)

    def test_negative_delay_is_rejected(self):
        with self.assertRaises(ValueError):
            CPMSolver().calculate_cpm_impact("TASK-SLAB", -1)

    def test_elements_map_to_specific_tasks(self):
        self.assertEqual(map_element_to_task("ground slab"), "TASK-SLAB")
        self.assertEqual(map_element_to_task("foundation footing"), "TASK-FOUNDATION")
        self.assertEqual(map_element_to_task("structural columns"), "TASK-COLUMNS")


if __name__ == "__main__":
    unittest.main()
