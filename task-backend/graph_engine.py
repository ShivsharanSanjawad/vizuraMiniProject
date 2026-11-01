import networkx as nx
from typing import List, Dict, Any

def analyze_task_graph(enriched_tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    G = nx.DiGraph()

    # 1. Add all standard tasks as nodes
    for task in enriched_tasks:
        G.add_node(task["task_id"], **task)

    # 2. Build the edges (Prerequisite -> Dependent Task)
    for task in enriched_tasks:
        task_id = task["task_id"]
        
        # Merge both dependency types for the graph logic
        all_prereqs = task.get("explicit_prerequisites", []) + task.get("inferred_prerequisites", [])
        
        for pre in all_prereqs:
            # If the prerequisite isn't a known task ID, it's an external blocker string.
            # We add it as a standalone node so it still shows up visually in the UI.
            if not G.has_node(pre):
                G.add_node(pre, is_external_blocker=True, description=pre, status="pending")
            
            # Direction: Prerequisite blocks the Task
            G.add_edge(pre, task_id)

    # 3. Detect Circular Dependencies (e.g., T1 -> T2 -> T1)
    # We catch the first cycle found to flag the schedule as infeasible.
    try:
        cycles = list(nx.find_cycle(G, orientation="original"))
        circular_dependencies = [{"from": u, "to": v} for u, v, _ in cycles]
        is_feasible = False
    except nx.NetworkXNoCycle:
        circular_dependencies = []
        is_feasible = True

    # 4. Identify Blockers and Bottlenecks
    # A node's out-degree is how many tasks it points to (blocks).
    # We sort them to bubble the biggest bottlenecks to the top.
    blockers = []
    for node in G.nodes:
        out_deg = G.out_degree(node)
        if out_deg > 0:
            node_data = G.nodes[node]
            blockers.append({
                "entity_id": node,
                "description": node_data.get("description", node),
                "blocks_count": out_deg,
                "is_external": node_data.get("is_external_blocker", False)
            })
    
    bottlenecks = sorted(blockers, key=lambda x: x["blocks_count"], reverse=True)

    # 5. Export Graph Data for Frontend Visualization (React Flow / D3)
    nodes_data = [{"id": n, "data": G.nodes[n]} for n in G.nodes]
    edges_data = [{"source": u, "target": v} for u, v in G.edges]

    return {
        "is_feasible": is_feasible,
        "circular_dependencies": circular_dependencies,
        "bottlenecks": bottlenecks,
        "visualization_graph": {
            "nodes": nodes_data,
            "edges": edges_data
        }
    }