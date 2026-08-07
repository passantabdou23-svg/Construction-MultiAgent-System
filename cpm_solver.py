import networkx as nx

class CPMSolver:
    def __init__(self):
        self.graph = nx.DiGraph()
        self._build_site_schedule_network()

    def _build_site_schedule_network(self):
        """Constructs standard construction project DAG."""
        # Nodes: Task ID, Duration (days)
        tasks = [
            ("TASK-SITE-PREP", {"duration": 5, "name": "Site Excavation"}),
            ("TASK-FOUNDATION", {"duration": 10, "name": "Foundation Pour"}),
            ("TASK-COLUMNS", {"duration": 8, "name": "Column Structural Formwork"}),
            ("TASK-SLAB", {"duration": 12, "name": "Slab Reinforcement & Pour"}),
            ("TASK-FINISHING", {"duration": 7, "name": "Final Inspection"})
        ]
        self.graph.add_nodes_from(tasks)

        # Precedences (Edges)
        edges = [
            ("TASK-SITE-PREP", "TASK-FOUNDATION"),
            ("TASK-FOUNDATION", "TASK-COLUMNS"),
            ("TASK-COLUMNS", "TASK-SLAB"),
            ("TASK-SLAB", "TASK-FINISHING")
        ]
        self.graph.add_edges_from(edges)

    def calculate_cpm_impact(self, affected_task: str, lead_time_delay: int) -> dict:
        """Dynamically recalculates critical path and project completion date."""
        # Create a copy to perform forward pass calculations
        temp_graph = self.graph.copy()
        
        # Apply lead time delay to the affected task duration if present
        if affected_task in temp_graph.nodes:
            temp_graph.nodes[affected_task]["duration"] += lead_time_delay

        # Forward Pass: Compute Earliest Finish (EF)
        topological_order = list(nx.topological_sort(temp_graph))
        earliest_finish = {}
        
        for node in topological_order:
            preds = list(temp_graph.predecessors(node))
            max_pred_ef = max([earliest_finish[p] for p in preds], default=0)
            earliest_finish[node] = max_pred_ef + temp_graph.nodes[node]["duration"]

        project_duration = max(earliest_finish.values()) if earliest_finish else 0

        # Determine Critical Path (Longest Path in DAG)
        critical_path = nx.dag_longest_path(temp_graph, weight="duration")

        return {
            "critical_path_tasks": critical_path,
            "total_project_duration_days": project_duration,
            "is_critical": affected_task in critical_path,
            "delay_added": lead_time_delay
        }

# Quick test execution
if __name__ == "__main__":
    solver = CPMSolver()
    impact = solver.calculate_cpm_impact("TASK-FOUNDATION", lead_time_delay=5)
    print(f"⏱️ NetworkX CPM Impact Analysis:\n{impact}")