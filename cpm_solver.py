"""Deterministic Critical Path Method calculations for the demo schedule."""

from __future__ import annotations

import networkx as nx


class CPMSolver:
    def __init__(self):
        self.graph = nx.DiGraph()
        self._build_site_schedule_network()

    def _build_site_schedule_network(self) -> None:
        tasks = [
            ("TASK-SITE-PREP", {"duration": 5, "name": "Site excavation"}),
            ("TASK-FOUNDATION", {"duration": 10, "name": "Foundation pour"}),
            ("TASK-COLUMNS", {"duration": 8, "name": "Column structural work"}),
            ("TASK-SLAB", {"duration": 12, "name": "Slab reinforcement and pour"}),
            ("TASK-FINISHING", {"duration": 7, "name": "Final inspection"}),
        ]
        self.graph.add_nodes_from(tasks)
        self.graph.add_edges_from(
            [
                ("TASK-SITE-PREP", "TASK-FOUNDATION"),
                ("TASK-FOUNDATION", "TASK-COLUMNS"),
                ("TASK-COLUMNS", "TASK-SLAB"),
                ("TASK-SLAB", "TASK-FINISHING"),
            ]
        )

    @staticmethod
    def _critical_path(graph: nx.DiGraph) -> tuple[list[str], dict[str, int]]:
        """Return the node-duration longest path and earliest-finish values."""
        if not nx.is_directed_acyclic_graph(graph):
            raise ValueError("The project schedule must be a directed acyclic graph")

        earliest_finish: dict[str, int] = {}
        predecessor_on_longest_path: dict[str, str | None] = {}
        for node in nx.topological_sort(graph):
            predecessors = list(graph.predecessors(node))
            if predecessors:
                predecessor = max(predecessors, key=lambda item: earliest_finish[item])
                predecessor_finish = earliest_finish[predecessor]
            else:
                predecessor = None
                predecessor_finish = 0
            earliest_finish[node] = predecessor_finish + int(graph.nodes[node]["duration"])
            predecessor_on_longest_path[node] = predecessor

        if not earliest_finish:
            return [], {}

        current: str | None = max(earliest_finish, key=earliest_finish.get)
        path: list[str] = []
        while current is not None:
            path.append(current)
            current = predecessor_on_longest_path[current]
        path.reverse()
        return path, earliest_finish

    def calculate_cpm_impact(self, affected_task: str, lead_time_delay: int) -> dict:
        if affected_task not in self.graph:
            raise ValueError(f"Unknown schedule task: {affected_task}")
        if not isinstance(lead_time_delay, int) or lead_time_delay < 0:
            raise ValueError("lead_time_delay must be a non-negative integer")

        baseline_path, baseline_finish = self._critical_path(self.graph)
        baseline_duration = max(baseline_finish.values(), default=0)

        projected_graph = self.graph.copy()
        projected_graph.nodes[affected_task]["duration"] += lead_time_delay
        projected_path, projected_finish = self._critical_path(projected_graph)
        projected_duration = max(projected_finish.values(), default=0)

        return {
            "critical_path_tasks": projected_path,
            "baseline_critical_path_tasks": baseline_path,
            "baseline_project_duration_days": baseline_duration,
            "total_project_duration_days": projected_duration,
            "is_critical": affected_task in projected_path,
            "delay_added": projected_duration - baseline_duration,
            "earliest_finish_days": projected_finish,
        }


if __name__ == "__main__":
    solver = CPMSolver()
    print(solver.calculate_cpm_impact("TASK-FOUNDATION", lead_time_delay=5))
