import os
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import Request

from backend.run_graphfix import (
    app,
    cors_json,
    response_ok,
    parse_request_context,
    build_prompt_ontology,
    build_graph_components,
)
from backend.run import (
    AGENT_CATALOG,
    BUILD_TASKS,
    PROJECT_CONTEXTS,
    BACKEND_VERSION,
    utc_now,
    score_agent_catalog_item,
    build_agent_from_catalog,
)


EXECUTIVE_AGENT_MIN_COUNT = int(os.getenv("EXECUTIVE_AGENT_MIN_COUNT", os.getenv("CLEVEL_AGENT_MIN_COUNT", "4")))
EXECUTIVE_AGENT_DEFAULT_COUNT = int(os.getenv("EXECUTIVE_AGENT_DEFAULT_COUNT", os.getenv("CLEVEL_AGENT_DEFAULT_COUNT", "6")))
EXECUTIVE_AGENT_MAX_COUNT = int(os.getenv("EXECUTIVE_AGENT_MAX_COUNT", os.getenv("CLEVEL_AGENT_MAX_COUNT", "8")))

# Ordem de cobertura mínima para uma plataforma de decisão C-Level.
EXECUTIVE_COVERAGE_ORDER = [
    "risco",
    "financeiro",
    "dados_bi",
    "produto",
    "comercializacao",
    "arquitetura",
    "fiscal",
    "dba",
]


def _to_int(value: Any, default: int) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default


def resolve_executive_agent_count(fields: Dict[str, Any]) -> int:
    """
    Resolve a quantidade de agentes especialistas sem depender do AGENT_MAX_COUNT
    usado no scheduler operacional do Render.

    Isso separa:
    - agentes especialistas C-Level que aparecem/analisam o cenário;
    - capacidade de execução/rate limit do Render/Groq.
    """
    requested = (
        fields.get("executive_agent_count")
        or fields.get("executiveAgentCount")
        or fields.get("specialist_agent_count")
        or fields.get("specialistAgentCount")
        or fields.get("agent_count")
        or fields.get("agents_count")
        or EXECUTIVE_AGENT_DEFAULT_COUNT
    )
    parsed = _to_int(requested, EXECUTIVE_AGENT_DEFAULT_COUNT)
    effective_min = max(1, EXECUTIVE_AGENT_MIN_COUNT)
    effective_max = max(effective_min, min(EXECUTIVE_AGENT_MAX_COUNT, len(AGENT_CATALOG)))
    return max(effective_min, min(parsed, effective_max))


def generate_executive_agents(context: str, target_count: Optional[int] = None) -> List[Dict[str, Any]]:
    target = max(
        EXECUTIVE_AGENT_MIN_COUNT,
        min(target_count or EXECUTIVE_AGENT_DEFAULT_COUNT, EXECUTIVE_AGENT_MAX_COUNT, len(AGENT_CATALOG)),
    )
    normalized_context = context or ""

    ranked = []
    for item in AGENT_CATALOG:
        score = score_agent_catalog_item(normalized_context, item)
        if score > 0:
            ranked.append((score, item))
    ranked.sort(key=lambda pair: pair[0], reverse=True)

    selected: List[Dict[str, Any]] = []
    used_keys = set()

    for score, item in ranked:
        if item["key"] in used_keys:
            continue
        selected.append(
            build_agent_from_catalog(
                item,
                len(selected),
                f"Selecionado para cobertura C-Level por {score} evidência(s) no cenário.",
            )
        )
        used_keys.add(item["key"])
        if len(selected) >= target:
            break

    catalog_by_key = {item["key"]: item for item in AGENT_CATALOG}
    for key in EXECUTIVE_COVERAGE_ORDER:
        if len(selected) >= target:
            break
        item = catalog_by_key.get(key)
        if item and key not in used_keys:
            selected.append(
                build_agent_from_catalog(
                    item,
                    len(selected),
                    "Selecionado como cobertura executiva mínima C-Level.",
                )
            )
            used_keys.add(key)

    # Normaliza metadados para deixar explícito que são especialistas executivos.
    for agent in selected:
        agent["agent_mode"] = "executive_specialist"
        agent["execution_note"] = "Especialista C-Level. Execução LLM controlada por fila/rate limit do scheduler."

    return selected[:target]


def _project_id_from_fields(fields: Dict[str, Any]) -> str:
    return str(fields.get("project_id") or fields.get("projectId") or "render-project")


def _project_name_from_fields(fields: Dict[str, Any]) -> str:
    return str(fields.get("project_name") or fields.get("projectName") or "Projeto MiroFish Render")


@app.middleware("http")
async def clevel_agent_count_middleware(request: Request, call_next):
    path = request.url.path

    intercepted = path in {
        "/api/agents/capacity",
        "/api/graph/ontology/generate",
        "/api/graph/build",
    }

    if request.method == "OPTIONS" and intercepted:
        return cors_json({"ok": True})

    if request.method == "GET" and path == "/api/agents/capacity":
        payload = {
            "status": "ready",
            "agent_model": "executive_specialists",
            "executive_agent_min_count": EXECUTIVE_AGENT_MIN_COUNT,
            "executive_agent_default_count": EXECUTIVE_AGENT_DEFAULT_COUNT,
            "executive_agent_max_count": EXECUTIVE_AGENT_MAX_COUNT,
            "catalog_size": len(AGENT_CATALOG),
            "coverage_order": EXECUTIVE_COVERAGE_ORDER,
            "backend_version": BACKEND_VERSION,
        }
        return cors_json(response_ok({"status": "ready", "data": payload, "result": payload}))

    if request.method == "POST" and path == "/api/graph/ontology/generate":
        try:
            parsed = await parse_request_context(request)
            fields = parsed["fields"]
            project_id = _project_id_from_fields(fields)
            project_name = _project_name_from_fields(fields)
            source_type = str(fields.get("source_type") or fields.get("sourceType") or "text")
            context_text = parsed["context_text"] or "Cenário não informado. Solicitar objetivo, dados de entrada, restrições e resultado esperado."
            agent_count = resolve_executive_agent_count(fields)
            agents = generate_executive_agents(context_text, agent_count)
            ontology = build_prompt_ontology(context_text, agents)

            PROJECT_CONTEXTS[project_id] = {
                "project_id": project_id,
                "project_name": project_name,
                "prompt": context_text,
                "source_type": source_type,
                "agent_count": len(agents),
                "executive_agent_count": len(agents),
                "agent_model": "executive_specialists",
                "agents_preview": agents,
                "ontology": ontology,
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
                "message": "Ontology generation completed with executive specialist agents and prompt-derived ontology.",
                "agent_model": "executive_specialists",
                "agent_count": len(agents),
                "executive_agent_count": len(agents),
                "agents_preview": agents,
                "ontology": ontology,
                "context_loaded": bool(context_text.strip()),
                "context_size": len(context_text),
                "node_count": 1 + len(agents) + len(ontology.get("entities", [])),
                "edge_count": len(ontology.get("relations", [])),
                "schema_types_count": len(ontology.get("entity_types", [])),
                "created_at": utc_now(),
                "updated_at": utc_now(),
            }
            BUILD_TASKS[task_id] = task
            return cors_json(response_ok({"status": "completed", "state": "completed", "task_id": task_id, "data": task, "result": task, "task": task}))
        except Exception as exc:
            return cors_json({"success": False, "ok": False, "status": "error", "state": "error", "message": f"Falha ao gerar ontologia C-Level: {exc}"}, 500)

    if request.method == "POST" and path == "/api/graph/build":
        try:
            parsed = await parse_request_context(request)
            fields = parsed["fields"]
            project_id = _project_id_from_fields(fields)
            context = PROJECT_CONTEXTS.get(project_id) or PROJECT_CONTEXTS.get("render-project") or {}
            context_text = context.get("context_preview") or parsed.get("context_text") or ""

            current_agents = context.get("agents_preview") or []
            desired_count = resolve_executive_agent_count({**(context.get("raw_fields") or {}), **fields})
            if len(current_agents) < desired_count:
                current_agents = generate_executive_agents(context_text, desired_count)

            ontology = context.get("ontology") or build_prompt_ontology(context_text, current_agents)
            context.update({
                "agents_preview": current_agents,
                "agent_count": len(current_agents),
                "executive_agent_count": len(current_agents),
                "agent_model": "executive_specialists",
                "ontology": ontology,
                "graph_completed": True,
                "updated_at": utc_now(),
            })
            PROJECT_CONTEXTS[project_id] = context

            components = build_graph_components(project_id)
            task_id = f"graph_build_{uuid4().hex}"
            task = {
                "task_id": task_id,
                "id": task_id,
                "project_id": project_id,
                "graph_id": f"graph-{project_id}",
                "ontology_id": f"ontology-{project_id}",
                "status": "completed",
                "state": "completed",
                "progress": 100,
                "message": "GraphRAG build completed with executive specialist agents.",
                "agent_model": "executive_specialists",
                "agent_count": len(current_agents),
                "executive_agent_count": len(current_agents),
                "nodes_created": len(components["nodes"]),
                "relationships_created": len(components["edges"]),
                "schema_types_count": len(ontology.get("entity_types", [])),
                "entity_nodes": len(components["nodes"]),
                "relation_edges": len(components["edges"]),
                "schema_types": ontology.get("entity_types", []),
                "ontology": ontology,
                "created_at": utc_now(),
                "updated_at": utc_now(),
            }
            BUILD_TASKS[task_id] = task
            return cors_json(response_ok({"status": "completed", "state": "completed", "task_id": task_id, "data": task, "result": task, "task": task}))
        except Exception as exc:
            return cors_json({"success": False, "ok": False, "status": "error", "state": "error", "message": f"Falha ao construir GraphRAG C-Level: {exc}"}, 500)

    return await call_next(request)
