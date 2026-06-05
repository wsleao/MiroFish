from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import Request
from fastapi.responses import JSONResponse

from backend.run import (
    app,
    response_ok,
    get_current_project_context,
    generate_dynamic_agents_from_context,
    get_context_text,
    AGENT_DEFAULT_COUNT,
    AGENT_MIN_COUNT,
    AGENT_MAX_COUNT,
    BACKEND_VERSION,
    PROJECT_CONTEXTS,
    BUILD_TASKS,
    utc_now,
    clamp_int,
)


def cors_headers() -> Dict[str, str]:
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": "*",
        "Access-Control-Max-Age": "86400",
    }


def cors_json(payload: Dict[str, Any], status_code: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status_code, headers=cors_headers())


def extract_best_context(fields: Dict[str, Any], file_texts: List[str]) -> str:
    priority_keys = [
        "simulation_requirement",
        "simulationRequirement",
        "requirement",
        "requirements",
        "scenario",
        "prompt",
        "description",
        "project_description",
        "projectDescription",
        "context",
        "topic",
        "message",
        "objective",
        "goal",
    ]

    parts: List[str] = []
    for key in priority_keys:
        value = fields.get(key)
        if value and str(value).strip() and str(value).strip().lower() not in {"undefined", "null", "none"}:
            parts.append(f"[{key}]\n{str(value).strip()}")

    ignored = {
        "project_id", "projectId", "project_name", "projectName", "graph_id", "graphId",
        "source_type", "sourceType", "schema", "table", "agent_count", "agents_count",
    }
    for key, value in fields.items():
        if key in priority_keys or key in ignored:
            continue
        text = str(value).strip()
        if text and text.lower() not in {"undefined", "null", "none"} and len(text) > 3:
            parts.append(f"[{key}]\n{text}")

    for idx, file_text in enumerate(file_texts, start=1):
        if file_text.strip():
            parts.append(f"[arquivo_{idx}]\n{file_text.strip()[:12000]}")

    return "\n\n".join(parts).strip()


async def parse_multipart_context(request: Request) -> Dict[str, Any]:
    fields: Dict[str, Any] = {}
    file_texts: List[str] = []
    file_names: List[str] = []

    form = await request.form()
    for key, value in form.multi_items():
        if hasattr(value, "filename"):
            filename = getattr(value, "filename", "") or "arquivo"
            file_names.append(filename)
            try:
                raw = await value.read()
                text = raw[:12000].decode("utf-8", errors="ignore")
                if text.strip():
                    file_texts.append(text)
                else:
                    file_texts.append(f"Arquivo recebido: {filename}. Conteúdo binário/PDF não extraído nesta etapa.")
            except Exception as exc:
                file_texts.append(f"Arquivo recebido: {filename}. Falha ao ler preview: {exc}")
        else:
            fields[key] = value

    context_text = extract_best_context(fields, file_texts)
    return {
        "fields": fields,
        "file_names": file_names,
        "context_text": context_text,
    }


def _node(uuid: str, name: str, label: str, summary: str = "", attributes: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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


def _edge(source_uuid: str, target_uuid: str, name: str, fact: str, fact_type: Optional[str] = None) -> Dict[str, Any]:
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
        edges.append(_edge(project_id, agent["id"], "USES_AGENT", f"{project_name} usa {agent['name']} para analisar o cenário.", "USES_AGENT"))

    for idx in range(len(agents) - 1):
        source = agents[idx]
        target = agents[idx + 1]
        edges.append(_edge(source["id"], target["id"], "COLLABORATES_WITH", f"{source['name']} complementa a análise de {target['name']}.", "COLLABORATES_WITH"))

    if len(agents) > 2:
        edges.append(_edge(agents[-1]["id"], agents[0]["id"], "FEEDS_BACK_TO", f"{agents[-1]['name']} devolve conclusões para {agents[0]['name']} fechar o ciclo analítico.", "FEEDS_BACK_TO"))

    data = {
        "graph_id": graph_id,
        "project_id": project_id,
        "nodes": nodes,
        "edges": edges,
        "relationships": edges,
        "agents": agents,
        "status": "ready",
        "context_loaded": bool((context.get("context_preview") or "").strip()),
        "context_size": len(context.get("context_preview") or ""),
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

    if request.method == "OPTIONS" and (path.startswith("/api/graph/data/") or path == "/api/graph/ontology/generate"):
        return cors_json({"ok": True})

    if request.method == "POST" and path == "/api/graph/ontology/generate":
        try:
            parsed = await parse_multipart_context(request)
            fields = parsed["fields"]
            project_id = str(fields.get("project_id") or fields.get("projectId") or "render-project")
            project_name = str(fields.get("project_name") or fields.get("projectName") or "Projeto MiroFish Render")
            source_type = str(fields.get("source_type") or fields.get("sourceType") or "text")
            agent_count = clamp_int(fields.get("agent_count") or fields.get("agents_count") or AGENT_DEFAULT_COUNT, AGENT_DEFAULT_COUNT, AGENT_MIN_COUNT, AGENT_MAX_COUNT)
            context_text = parsed["context_text"]

            if not context_text:
                context_text = "Cenário não informado. Solicitar ao usuário o objetivo, dados de entrada, restrições e resultado esperado."

            agents = generate_dynamic_agents_from_context(context_text, agent_count)
            PROJECT_CONTEXTS[project_id] = {
                "project_id": project_id,
                "project_name": project_name,
                "prompt": context_text,
                "source_type": source_type,
                "agent_count": agent_count,
                "agents_preview": agents,
                "context_preview": context_text[:12000],
                "file_names": parsed["file_names"],
                "raw_fields": fields,
                "updated_at": utc_now(),
            }

            task_id = f"ontology_generate_{uuid4().hex}"
            task = {
                "task_id": task_id,
                "id": task_id,
                "project_id": project_id,
                "graph_id": f"graph-{project_id}",
                "ontology_id": f"ontology-{project_id}",
                "status": "completed",
                "state": "completed",
                "message": "Ontology generation completed with captured scenario context.",
                "agents_preview": agents,
                "context_loaded": bool(context_text.strip()),
                "context_size": len(context_text),
                "created_at": utc_now(),
                "updated_at": utc_now(),
            }
            BUILD_TASKS[task_id] = task
            return cors_json(response_ok({"status": "completed", "state": "completed", "task_id": task_id, "data": task, "result": task, "task": task}))
        except Exception as exc:
            return cors_json({"success": False, "ok": False, "status": "error", "state": "error", "message": f"Falha ao capturar contexto do cenário: {exc}"}, 500)

    if path.startswith("/api/graph/data/"):
        if request.method == "GET":
            graph_id = path.rsplit("/", 1)[-1]
            return cors_json(build_graph_payload(graph_id))

    return await call_next(request)
