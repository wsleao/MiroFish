from typing import Any, Dict, List

from fastapi import Request
from fastapi.responses import JSONResponse

from backend.run import (
    app,
    response_ok,
    get_current_project_context,
    generate_dynamic_agents_from_context,
    get_context_text,
    AGENT_DEFAULT_COUNT,
    BACKEND_VERSION,
    utc_now,
)


def _node(uuid: str, name: str, label: str, summary: str = "", attributes: Dict[str, Any] | None = None) -> Dict[str, Any]:
    attrs = attributes or {}
    return {
        "uuid": uuid,
        "id": uuid,
        "name": name,
        "label": name,
        "type": label,
        "labels": ["Entity", label],
        "summary": summary,
        "attributes": attrs,
        "created_at": utc_now(),
    }


def _edge(source_uuid: str, target_uuid: str, name: str, fact: str, fact_type: str | None = None) -> Dict[str, Any]:
    edge_id = f"edge-{source_uuid}-{target_uuid}-{name}".replace(" ", "-").replace("/", "-")
    return {
        "uuid": edge_id,
        "id": edge_id,
        "source": source_uuid,
        "target": target_uuid,
        "source_node_uuid": source_uuid,
        "target_node_uuid": target_uuid,
        "name": name,
        "type": fact_type or name,
        "fact_type": fact_type or name,
        "fact": fact,
        "created_at": utc_now(),
        "valid_at": utc_now(),
        "episodes": [],
    }


def build_graph_payload(graph_id: str) -> Dict[str, Any]:
    project_id = graph_id.replace("graph-", "") if graph_id.startswith("graph-") else "render-project"
    context = get_current_project_context(project_id)
    agents = context.get("agents_preview") or generate_dynamic_agents_from_context(
        get_context_text(project_id),
        context.get("agent_count") or AGENT_DEFAULT_COUNT,
    )

    project_name = context.get("project_name") or "Projeto MiroFish Render"
    nodes: List[Dict[str, Any]] = [
        _node(
            project_id,
            project_name,
            "Project",
            "Projeto/cenário analisado pelo MiroFish.",
            {
                "source_type": context.get("source_type") or "unknown",
                "backend_version": BACKEND_VERSION,
            },
        )
    ]

    for agent in agents:
        nodes.append(
            _node(
                agent["id"],
                agent["name"],
                "Agent",
                agent.get("persona") or agent.get("role") or "Agente dinâmico de simulação.",
                {
                    "role": agent.get("role"),
                    "profession": agent.get("profession"),
                    "profile": agent.get("profile"),
                    "generation_mode": agent.get("generation_mode"),
                    "selection_reason": agent.get("selection_reason"),
                },
            )
        )

    edges: List[Dict[str, Any]] = []
    for agent in agents:
        edges.append(
            _edge(
                project_id,
                agent["id"],
                "USES_AGENT",
                f"{project_name} usa {agent['name']} para analisar o cenário.",
                "USES_AGENT",
            )
        )

    # Relações entre agentes para que o grafo mostre fluxo de colaboração, não apenas nós soltos.
    for idx in range(len(agents) - 1):
        source = agents[idx]
        target = agents[idx + 1]
        edges.append(
            _edge(
                source["id"],
                target["id"],
                "COLLABORATES_WITH",
                f"{source['name']} complementa a análise de {target['name']}.",
                "COLLABORATES_WITH",
            )
        )

    if len(agents) > 2:
        edges.append(
            _edge(
                agents[-1]["id"],
                agents[0]["id"],
                "FEEDS_BACK_TO",
                f"{agents[-1]['name']} devolve conclusões para {agents[0]['name']} fechar o ciclo analítico.",
                "FEEDS_BACK_TO",
            )
        )

    data = {
        "graph_id": graph_id,
        "project_id": project_id,
        "nodes": nodes,
        "edges": edges,
        "relationships": edges,
        "agents": agents,
        "status": "ready",
        "backend_version": BACKEND_VERSION,
    }
    return response_ok({
        "status": "ready",
        "graph_id": graph_id,
        "project_id": project_id,
        "nodes": nodes,
        "edges": edges,
        "relationships": edges,
        "data": data,
        "result": data,
        "graph": data,
    })


@app.middleware("http")
async def graph_contract_middleware(request: Request, call_next):
    path = request.url.path
    if request.method == "GET" and path.startswith("/api/graph/data/"):
        graph_id = path.rsplit("/", 1)[-1]
        return JSONResponse(build_graph_payload(graph_id))
    return await call_next(request)
