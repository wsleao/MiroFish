import re
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
    SIMULATIONS,
    REPORTS,
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


def clean_text(value: Any, limit: int = 4000) -> str:
    text = str(value or "")
    text = re.sub(r"\[[A-Za-z0-9_ -]+\]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def detect_category(context: str, agents: Optional[List[Dict[str, Any]]] = None) -> str:
    text = (context or "").lower()
    agent_profiles = " ".join([str(a.get("profile", "")) for a in (agents or [])]).lower()
    base = f"{text} {agent_profiles}"
    rules = [
        ("Fiscal/NF-e", ["nf-e", "nfe", "nota fiscal", "icms", "sped", "sefaz", "funrural", "cfop", "cst"]),
        ("Financeiro", ["fluxo de caixa", "contas a pagar", "contas a receber", "orçamento", "financeiro", "capital", "crédito"]),
        ("Dados/BI", ["dashboard", "indicador", "bi", "datalake", "data lake", "postgres", "schema", "tabela", "sql"]),
        ("Integração/Arquitetura", ["integração", "integracao", "api", "pipeline", "cloud", "render", "vercel", "neo4j", "groq"]),
        ("Agro/Comercialização", ["soja", "milho", "saca", "safra", "produtor rural", "barter", "comercialização", "fazenda"]),
        ("Produto", ["produto", "erp", "scadiagro", "módulo", "modulo", "jornada", "usuário", "ux"]),
    ]
    for category, keywords in rules:
        if any(k in base for k in keywords):
            return category
    return "Cenário Geral"


def short_objective(context: str) -> str:
    text = clean_text(context, 500)
    if not text:
        return "Simulação sem descrição"
    # Prefere a frase de objetivo quando existir.
    match = re.search(r"(?:objetivo|resultado esperado|cenário|cenario)[:\-]?\s*(.{20,160})", text, re.IGNORECASE)
    if match:
        text = match.group(1)
    words = text.split()
    title = " ".join(words[:9])
    if len(words) > 9:
        title += "..."
    return title[:90]


def format_sim_code(simulation_id: str) -> str:
    suffix = str(simulation_id or "sim").replace("sim_", "")[:6].upper()
    return f"SIM-{suffix}"


def auto_simulation_name(sim: Dict[str, Any]) -> str:
    simulation_id = sim.get("simulation_id") or sim.get("id") or "sim"
    created = str(sim.get("created_at") or sim.get("started_at") or "")
    date_code = created[:10].replace("-", "") if len(created) >= 10 else "SEM-DATA"
    context = sim.get("context_preview") or sim.get("prompt") or sim.get("project_context", {}).get("prompt") or ""
    agents = sim.get("agents") or sim.get("dynamic_agents") or []
    category = detect_category(context, agents)
    objective = short_objective(context)
    return f"{date_code} | {category} | {objective} | {format_sim_code(simulation_id)}"


def build_simulation_tags(sim: Dict[str, Any]) -> List[str]:
    metadata = sim.get("metadata") or {}
    custom_tags = metadata.get("tags") or []
    if isinstance(custom_tags, str):
        custom_tags = [t.strip() for t in custom_tags.split(",") if t.strip()]
    context = sim.get("context_preview") or sim.get("prompt") or ""
    agents = sim.get("agents") or sim.get("dynamic_agents") or []
    tags = [detect_category(context, agents), sim.get("status", "created")]
    if sim.get("context_source"):
        tags.append(str(sim.get("context_source")))
    for agent in agents[:5]:
        profile = agent.get("profile")
        if profile:
            tags.append(str(profile))
    tags.extend(custom_tags)
    # Remove duplicados preservando ordem.
    seen = set()
    result = []
    for tag in tags:
        normalized = str(tag).strip()
        if normalized and normalized.lower() not in seen:
            seen.add(normalized.lower())
            result.append(normalized)
    return result[:10]


def files_from_sim(sim: Dict[str, Any]) -> List[Dict[str, str]]:
    project_context = sim.get("project_context") or {}
    names = project_context.get("file_names") or sim.get("file_names") or []
    files = sim.get("files") or []
    result = []
    for item in files:
        if isinstance(item, dict) and item.get("filename"):
            result.append({"filename": item.get("filename")})
        elif isinstance(item, str):
            result.append({"filename": item})
    for name in names:
        result.append({"filename": str(name)})
    return result


def build_history_entry(sim: Dict[str, Any]) -> Dict[str, Any]:
    simulation_id = sim.get("simulation_id") or sim.get("id") or ""
    metadata = sim.get("metadata") or {}
    context = sim.get("context_preview") or sim.get("prompt") or sim.get("project_context", {}).get("prompt") or ""
    report = sim.get("report") or {}
    report_id = metadata.get("report_id") or report.get("report_id") or f"report_{simulation_id}"
    tags = build_simulation_tags(sim)
    simulation_name = metadata.get("simulation_name") or metadata.get("name") or auto_simulation_name(sim)
    entry = {
        **sim,
        "simulation_id": simulation_id,
        "simulation_name": simulation_name,
        "display_name": simulation_name,
        "short_code": format_sim_code(simulation_id),
        "category": detect_category(context, sim.get("agents") or sim.get("dynamic_agents") or []),
        "tags": tags,
        "notes": metadata.get("notes", ""),
        "simulation_requirement": clean_text(context, 3000),
        "context_preview": context,
        "context_size": len(context or ""),
        "files": files_from_sim(sim),
        "report_id": report_id,
        "search_text": " ".join([simulation_name, simulation_id, clean_text(context, 2000), " ".join(tags)]).lower(),
    }
    return entry


def build_history(limit: int = 20, query: str = "") -> List[Dict[str, Any]]:
    items = [build_history_entry(sim) for sim in SIMULATIONS.values()]
    q = (query or "").strip().lower()
    if q:
        items = [item for item in items if q in item.get("search_text", "")]
    items.sort(key=lambda item: str(item.get("created_at") or item.get("started_at") or ""), reverse=True)
    return items[: max(1, min(int(limit or 20), 100))]


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

    if request.method == "OPTIONS" and (
        path.startswith("/api/graph/data/")
        or path == "/api/graph/ontology/generate"
        or path == "/api/simulation/history"
        or path.startswith("/api/simulation/")
    ):
        return cors_json({"ok": True})

    if request.method == "GET" and path == "/api/simulation/history":
        try:
            query = request.query_params.get("query", "")
            limit = int(request.query_params.get("limit", "20"))
            items = build_history(limit=limit, query=query)
            payload = {
                "status": "completed",
                "state": "completed",
                "count": len(items),
                "query": query,
                "data": items,
                "result": items,
                "backend_version": BACKEND_VERSION,
            }
            return cors_json(response_ok(payload))
        except Exception as exc:
            return cors_json({"success": False, "ok": False, "status": "error", "message": f"Falha ao listar histórico: {exc}"}, 500)

    if request.method in {"POST", "PATCH"} and path.startswith("/api/simulation/") and path.endswith("/metadata"):
        try:
            simulation_id = path.split("/")[3]
            sim = SIMULATIONS.get(simulation_id)
            if not sim:
                return cors_json({"success": False, "ok": False, "status": "not_found", "message": "Simulação não encontrada."}, 404)
            body = await request.json()
            metadata = sim.setdefault("metadata", {})
            if "simulation_name" in body:
                metadata["simulation_name"] = str(body.get("simulation_name") or "").strip()
            if "name" in body:
                metadata["simulation_name"] = str(body.get("name") or "").strip()
            if "tags" in body:
                metadata["tags"] = body.get("tags") or []
            if "notes" in body:
                metadata["notes"] = str(body.get("notes") or "")
            metadata["updated_at"] = utc_now()
            sim["updated_at"] = utc_now()
            entry = build_history_entry(sim)
            return cors_json(response_ok({"status": "updated", "state": "updated", "data": entry, "result": entry}))
        except Exception as exc:
            return cors_json({"success": False, "ok": False, "status": "error", "message": f"Falha ao atualizar metadados: {exc}"}, 500)

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
