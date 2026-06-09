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


def slugify(value: str, prefix: str = "id") -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9áàãâéêíóôõúç]+", "-", text, flags=re.IGNORECASE)
    text = re.sub(r"-+", "-", text).strip("-")
    text = text[:70] or uuid4().hex[:8]
    return f"{prefix}-{text}"


def format_sim_code(simulation_id: str) -> str:
    suffix = str(simulation_id or "sim").replace("sim_", "")[:6].upper()
    return f"SIM-{suffix}"


ONTOLOGY_TYPES: Dict[str, Dict[str, Any]] = {
    "Scenario": {"description": "Cenário ou hipótese analisada pela simulação.", "keywords": ["cenário", "cenario", "simulação", "simulacao", "hipótese", "hipotese", "avaliar", "decidir"], "attributes": [{"name": "objetivo", "type": "string", "description": "Objetivo executivo do cenário."}]},
    "Decision": {"description": "Decisão executiva, trade-off ou recomendação esperada.", "keywords": ["decisão", "decisao", "recomendar", "recomendação", "opção", "alternativa", "vender", "comprar", "priorizar"], "attributes": [{"name": "opcoes", "type": "array", "description": "Alternativas de decisão."}]},
    "FinancialMetric": {"description": "Indicador financeiro, valor monetário ou medida de caixa/orçamento.", "keywords": ["caixa", "saldo", "contas a pagar", "contas a receber", "orçamento", "orcamento", "receita", "despesa", "custo", "margem", "ebitda", "r$", "%"], "attributes": [{"name": "valor", "type": "decimal", "description": "Valor associado ao indicador."}]},
    "OperationalMetric": {"description": "Indicador operacional, volume, capacidade, produtividade ou execução.", "keywords": ["produtividade", "produção", "producao", "volume", "capacidade", "sacas", "hectare", "ha", "estoque", "operação", "operacao"], "attributes": [{"name": "quantidade", "type": "decimal", "description": "Quantidade ou volume operacional."}]},
    "Risk": {"description": "Risco financeiro, operacional, estratégico ou de execução.", "keywords": ["risco", "atraso", "estouro", "falta", "queda", "exposição", "exposicao", "incerteza", "impacto"], "attributes": [{"name": "impacto", "type": "string", "description": "Impacto esperado no negócio."}]},
    "KPI": {"description": "Indicador executivo ou métrica de acompanhamento para C-Levels.", "keywords": ["kpi", "indicador", "dashboard", "painel", "métrica", "metrica", "alerta", "insight"], "attributes": [{"name": "formula", "type": "string", "description": "Fórmula ou lógica de cálculo."}]},
    "Asset": {"description": "Ativo, recurso ou estoque que pode gerar valor, liquidez ou risco.", "keywords": ["estoque", "soja", "milho", "produto", "ativo", "máquina", "maquina", "recurso"], "attributes": [{"name": "quantidade", "type": "decimal", "description": "Quantidade disponível."}]},
    "ExternalFactor": {"description": "Fator externo econômico, climático, mercado ou geopolítico.", "keywords": ["dólar", "dolar", "selic", "clima", "chuva", "mercado", "commodity", "preço", "preco", "juros", "geopolítico", "geopolitico"], "attributes": [{"name": "tendencia", "type": "string", "description": "Tendência ou variação esperada."}]},
    "BusinessObject": {"description": "Objeto de negócio vindo do ERP ou da operação.", "keywords": ["fornecedor", "cliente", "pedido", "nota", "contrato", "centro de custo", "fazenda", "safra", "erp"], "attributes": [{"name": "origem", "type": "string", "description": "Sistema ou fonte de origem."}]},
}


def infer_type(text: str) -> str:
    value = str(text or "").lower()
    scores = {type_name: sum(1 for keyword in spec["keywords"] if keyword in value) for type_name, spec in ONTOLOGY_TYPES.items()}
    best_type, best_score = max(scores.items(), key=lambda item: item[1])
    return best_type if best_score > 0 else "BusinessObject"


def detect_category(context: str, agents: Optional[List[Dict[str, Any]]] = None) -> str:
    text = (context or "").lower()
    agent_profiles = " ".join([str(a.get("profile", "")) for a in (agents or [])]).lower()
    base = f"{text} {agent_profiles}"
    rules = [
        ("Fiscal/NF-e", ["nf-e", "nfe", "nota fiscal", "icms", "sped", "sefaz", "funrural", "cfop", "cst"]),
        ("Financeiro", ["fluxo de caixa", "contas a pagar", "contas a receber", "orçamento", "orcamento", "financeiro", "capital", "crédito", "credito"]),
        ("Dados/BI", ["dashboard", "indicador", "bi", "datalake", "data lake", "postgres", "schema", "tabela", "sql"]),
        ("Integração/Arquitetura", ["integração", "integracao", "api", "pipeline", "cloud", "render", "vercel", "neo4j", "groq"]),
        ("Agro/Comercialização", ["soja", "milho", "saca", "safra", "produtor rural", "barter", "comercialização", "comercializacao", "fazenda"]),
        ("Produto", ["produto", "erp", "scadiagro", "módulo", "modulo", "jornada", "usuário", "usuario", "ux"]),
    ]
    for category, keywords in rules:
        if any(k in base for k in keywords):
            return category
    return "Cenário Geral"


def short_objective(context: str) -> str:
    text = clean_text(context, 500)
    if not text:
        return "Simulação sem descrição"
    match = re.search(r"(?:objetivo|resultado esperado|cenário|cenario)[:\-]?\s*(.{20,160})", text, re.IGNORECASE)
    if match:
        text = match.group(1)
    words = text.split()
    title = " ".join(words[:9])
    if len(words) > 9:
        title += "..."
    return title[:90]


def extract_numeric_value(text: str) -> Optional[str]:
    patterns = [r"R\$\s?[0-9\.]+(?:,[0-9]{2})?", r"[0-9]+(?:[\.,][0-9]+)?\s?%", r"[0-9\.]+(?:,[0-9]+)?\s?(?:sacas|sc|ha|hectares|dias|meses)"]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(0)
    return None


def add_entity(entities: Dict[str, Dict[str, Any]], name: str, source_text: str, entity_type: Optional[str] = None, value: Optional[str] = None) -> None:
    name = clean_text(name, 100).strip(" .:-")
    if not name or len(name) < 3:
        return
    ignored = {"dados", "contexto", "objetivo", "resultado esperado", "cenário", "cenario", "perguntas", "fonte", "arquivo"}
    if name.lower().strip(":") in ignored:
        return
    entity_type = entity_type or infer_type(f"{name} {source_text}")
    uuid = slugify(name, "entity")
    if uuid not in entities:
        entities[uuid] = {"uuid": uuid, "id": uuid, "name": name, "type": entity_type, "labels": ["Entity", entity_type], "summary": clean_text(source_text, 280), "attributes": {}, "examples": []}
    if value:
        entities[uuid]["attributes"]["valor_extraido"] = value
    numeric = extract_numeric_value(source_text)
    if numeric:
        entities[uuid]["attributes"].setdefault("valor_detectado", numeric)
    if source_text and len(entities[uuid]["examples"]) < 3:
        example = clean_text(source_text, 160)
        if example and example not in entities[uuid]["examples"]:
            entities[uuid]["examples"].append(example)


def extract_entities_from_context(context: str) -> List[Dict[str, Any]]:
    entities: Dict[str, Dict[str, Any]] = {}
    text = context or ""
    lower = text.lower()
    keyword_entities = [
        ("Fluxo de caixa", "FinancialMetric"), ("Saldo atual em caixa", "FinancialMetric"), ("Contas a pagar", "FinancialMetric"),
        ("Contas a receber", "FinancialMetric"), ("Orçamento", "FinancialMetric"), ("Forecast", "FinancialMetric"),
        ("Margem projetada", "FinancialMetric"), ("Receita projetada", "FinancialMetric"), ("Custo financeiro", "FinancialMetric"),
        ("Crédito de curto prazo", "Decision"), ("Venda de soja", "Decision"), ("Estoque de soja", "Asset"),
        ("Preço atual", "ExternalFactor"), ("Preço esperado", "ExternalFactor"), ("Risco de caixa negativo", "Risk"),
        ("Dashboard executivo", "KPI"), ("Indicadores executivos", "KPI"), ("Plano de ação", "Decision"),
    ]
    for name, entity_type in keyword_entities:
        if name.lower() in lower or any(part in lower for part in name.lower().split() if len(part) > 5):
            add_entity(entities, name, text, entity_type)
    for raw_line in text.splitlines():
        line = raw_line.strip(" \t-•*|;")
        if not line or len(line) < 4:
            continue
        if ":" in line:
            left, right = line.split(":", 1)
            if 3 <= len(left.strip()) <= 90:
                add_entity(entities, left.strip(), line, value=right.strip())
        elif re.search(r"R\$|%|\b(sacas|sc|hectares|ha|dias)\b", line, re.IGNORECASE):
            name = re.split(r"[-–—]", line)[0].strip()[:80]
            add_entity(entities, name or line[:60], line)
    for question in re.findall(r"([^\n\?]{15,180}\?)", text):
        add_entity(entities, clean_text(question.replace("?", ""), 85), question, "Decision")
    if len(entities) < 6:
        sentences = re.split(r"(?<=[\.\?\!])\s+|\n+", text)
        for sentence in sentences:
            sentence = clean_text(sentence, 240)
            if len(sentence) < 25:
                continue
            candidate = re.sub(r"^(cenário|cenario|objetivo|dados|resultado esperado)[:\-]?\s*", "", sentence, flags=re.IGNORECASE)
            candidate_name = " ".join(candidate.split()[:8]).strip(" .:-")
            add_entity(entities, candidate_name, sentence)
            if len(entities) >= 10:
                break
    if not entities:
        add_entity(entities, "Cenário informado", text or "Cenário sem conteúdo capturado.", "Scenario")
    return list(entities.values())[:40]


def has_entity(entities: List[Dict[str, Any]], phrase: str) -> Optional[Dict[str, Any]]:
    phrase_lower = phrase.lower()
    for entity in entities:
        name = entity.get("name", "").lower()
        if phrase_lower in name or name in phrase_lower:
            return entity
    for entity in entities:
        if any(part in entity.get("name", "").lower() for part in phrase_lower.split() if len(part) > 5):
            return entity
    return None


def relation(source: Dict[str, Any], target: Dict[str, Any], name: str, fact: str) -> Dict[str, Any]:
    edge_id = f"rel-{source['uuid']}-{target['uuid']}-{name}".replace(" ", "-").replace("/", "-")
    return {"uuid": edge_id, "id": edge_id, "source": source["uuid"], "target": target["uuid"], "source_node_uuid": source["uuid"], "target_node_uuid": target["uuid"], "source_name": source.get("name"), "target_name": target.get("name"), "name": name, "type": name, "fact_type": name, "fact": fact, "created_at": utc_now(), "valid_at": utc_now(), "episodes": []}


def build_knowledge_relations(entities: List[Dict[str, Any]], context: str) -> List[Dict[str, Any]]:
    relations: List[Dict[str, Any]] = []
    def connect(source_phrase: str, target_phrase: str, rel_type: str, fact: str):
        source = has_entity(entities, source_phrase)
        target = has_entity(entities, target_phrase)
        if source and target and source["uuid"] != target["uuid"]:
            rel = relation(source, target, rel_type, fact)
            if not any(r["uuid"] == rel["uuid"] for r in relations):
                relations.append(rel)
    connect("Contas a pagar", "Fluxo de caixa", "PRESSURES", "Contas a pagar pressionam o fluxo de caixa projetado.")
    connect("Saldo atual em caixa", "Fluxo de caixa", "BASELINES", "Saldo atual em caixa é a base da projeção de fluxo de caixa.")
    connect("Contas a receber", "Fluxo de caixa", "IMPROVES", "Contas a receber reduzem a necessidade de caixa futuro.")
    connect("Estoque de soja", "Venda de soja", "ENABLES", "Estoque disponível viabiliza decisão de venda.")
    connect("Venda de soja", "Fluxo de caixa", "GENERATES_LIQUIDITY", "Venda de soja pode gerar liquidez imediata.")
    connect("Preço atual", "Venda de soja", "AFFECTS", "Preço atual influencia a atratividade da venda imediata.")
    connect("Preço esperado", "Venda de soja", "AFFECTS", "Preço esperado influencia a decisão de aguardar ou vender agora.")
    connect("Crédito de curto prazo", "Custo financeiro", "GENERATES", "Uso de crédito de curto prazo gera custo financeiro.")
    connect("Custo financeiro", "Margem projetada", "REDUCES", "Custo financeiro reduz a margem projetada.")
    connect("Risco de caixa negativo", "Plano de ação", "REQUIRES", "Risco de caixa negativo exige plano de ação executivo.")
    connect("Dashboard executivo", "Indicadores executivos", "DISPLAYS", "Dashboard executivo deve apresentar os indicadores executivos relevantes.")
    financial = [e for e in entities if e.get("type") == "FinancialMetric"]
    decisions = [e for e in entities if e.get("type") == "Decision"]
    risks = [e for e in entities if e.get("type") == "Risk"]
    kpis = [e for e in entities if e.get("type") == "KPI"]
    assets = [e for e in entities if e.get("type") == "Asset"]
    externals = [e for e in entities if e.get("type") == "ExternalFactor"]
    for source in financial[:5]:
        for target in decisions[:2]:
            relations.append(relation(source, target, "INFORMS", f"{source['name']} informa a decisão {target['name']}.") )
    for source in risks[:4]:
        for target in decisions[:2]:
            relations.append(relation(source, target, "CONSTRAINS", f"{source['name']} cria restrição para {target['name']}.") )
    for source in externals[:4]:
        for target in financial[:3]:
            relations.append(relation(source, target, "AFFECTS", f"{source['name']} afeta {target['name']}.") )
    for source in assets[:3]:
        for target in financial[:3]:
            relations.append(relation(source, target, "CAN_IMPACT", f"{source['name']} pode impactar {target['name']}.") )
    for source in kpis[:4]:
        for target in decisions[:2] or risks[:2]:
            relations.append(relation(source, target, "SUPPORTS", f"{source['name']} apoia análise de {target['name']}.") )
    unique = {rel["uuid"]: rel for rel in relations}
    return list(unique.values())[:80]


def build_schema_types(entities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    used_types = sorted({entity.get("type", "BusinessObject") for entity in entities})
    result = []
    for type_name in used_types:
        spec = ONTOLOGY_TYPES.get(type_name, ONTOLOGY_TYPES["BusinessObject"])
        examples = [entity["name"] for entity in entities if entity.get("type") == type_name][:5]
        result.append({"name": type_name, "description": spec["description"], "attributes": spec["attributes"], "examples": examples})
    return result


def build_edge_types(relations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_type: Dict[str, List[Dict[str, Any]]] = {}
    for rel in relations:
        by_type.setdefault(rel["fact_type"], []).append(rel)
    return [{"name": rel_type, "description": f"Relação do tipo {rel_type} extraída do cenário.", "source_targets": [{"source": item.get("source_name"), "target": item.get("target_name")} for item in items[:5]]} for rel_type, items in sorted(by_type.items())]


def build_prompt_ontology(context: str, agents: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    entities = extract_entities_from_context(context)
    relations = build_knowledge_relations(entities, context)
    return {"entity_types": build_schema_types(entities), "edge_types": build_edge_types(relations), "entities": entities, "relations": relations, "schema_types_count": len(build_schema_types(entities)), "entity_count": len(entities), "relation_count": len(relations), "context_size": len(context or ""), "generated_at": utc_now(), "backend_version": BACKEND_VERSION}


def auto_simulation_name(sim: Dict[str, Any]) -> str:
    simulation_id = sim.get("simulation_id") or sim.get("id") or "sim"
    created = str(sim.get("created_at") or sim.get("started_at") or "")
    date_code = created[:10].replace("-", "") if len(created) >= 10 else "SEM-DATA"
    context = sim.get("context_preview") or sim.get("prompt") or sim.get("project_context", {}).get("prompt") or ""
    agents = sim.get("agents") or sim.get("dynamic_agents") or []
    return f"{date_code} | {detect_category(context, agents)} | {short_objective(context)} | {format_sim_code(simulation_id)}"


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
    seen = set(); result = []
    for tag in tags:
        normalized = str(tag).strip()
        if normalized and normalized.lower() not in seen:
            seen.add(normalized.lower()); result.append(normalized)
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
    return {**sim, "simulation_id": simulation_id, "simulation_name": simulation_name, "display_name": simulation_name, "short_code": format_sim_code(simulation_id), "category": detect_category(context, sim.get("agents") or sim.get("dynamic_agents") or []), "tags": tags, "notes": metadata.get("notes", ""), "simulation_requirement": clean_text(context, 3000), "context_preview": context, "context_size": len(context or ""), "files": files_from_sim(sim), "report_id": report_id, "search_text": " ".join([simulation_name, simulation_id, clean_text(context, 2000), " ".join(tags)]).lower()}


def build_history(limit: int = 20, query: str = "") -> List[Dict[str, Any]]:
    items = [build_history_entry(sim) for sim in SIMULATIONS.values()]
    q = (query or "").strip().lower()
    if q:
        items = [item for item in items if q in item.get("search_text", "")]
    items.sort(key=lambda item: str(item.get("created_at") or item.get("started_at") or ""), reverse=True)
    return items[: max(1, min(int(limit or 20), 100))]


def extract_best_context(fields: Dict[str, Any], file_texts: List[str]) -> str:
    priority_keys = ["simulation_requirement", "simulationRequirement", "requirement", "requirements", "scenario", "prompt", "description", "project_description", "projectDescription", "context", "topic", "message", "objective", "goal"]
    parts: List[str] = []
    for key in priority_keys:
        value = fields.get(key)
        if value and str(value).strip() and str(value).strip().lower() not in {"undefined", "null", "none"}:
            parts.append(f"[{key}]\n{str(value).strip()}")
    ignored = {"project_id", "projectId", "project_name", "projectName", "graph_id", "graphId", "source_type", "sourceType", "schema", "table", "agent_count", "agents_count"}
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


async def parse_request_context(request: Request) -> Dict[str, Any]:
    fields: Dict[str, Any] = {}; file_texts: List[str] = []; file_names: List[str] = []
    content_type = request.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        body = await request.json()
        if isinstance(body, dict):
            fields = body
    else:
        form = await request.form()
        for key, value in form.multi_items():
            if hasattr(value, "filename"):
                filename = getattr(value, "filename", "") or "arquivo"
                file_names.append(filename)
                try:
                    raw = await value.read()
                    text = raw[:12000].decode("utf-8", errors="ignore")
                    file_texts.append(text if text.strip() else f"Arquivo recebido: {filename}. Conteúdo binário/PDF não extraído nesta etapa.")
                except Exception as exc:
                    file_texts.append(f"Arquivo recebido: {filename}. Falha ao ler preview: {exc}")
            else:
                fields[key] = value
    return {"fields": fields, "file_names": file_names, "context_text": extract_best_context(fields, file_texts)}


def _node(uuid: str, name: str, label: str, summary: str = "", attributes: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"uuid": uuid, "id": uuid, "name": name, "label": name, "type": label, "labels": ["Entity", label], "summary": summary, "attributes": attributes or {}, "created_at": utc_now()}


def _edge(source_uuid: str, target_uuid: str, name: str, fact: str, fact_type: Optional[str] = None, source_name: Optional[str] = None, target_name: Optional[str] = None) -> Dict[str, Any]:
    edge_id = f"edge-{source_uuid}-{target_uuid}-{name}".replace(" ", "-").replace("/", "-")
    return {"uuid": edge_id, "id": edge_id, "source": source_uuid, "target": target_uuid, "source_node_uuid": source_uuid, "target_node_uuid": target_uuid, "source_name": source_name, "target_name": target_name, "name": name, "type": fact_type or name, "fact_type": fact_type or name, "fact": fact, "created_at": utc_now(), "valid_at": utc_now(), "episodes": []}


def build_graph_components(project_id: str) -> Dict[str, Any]:
    context = get_current_project_context(project_id)
    context_text = context.get("context_preview") or get_context_text(project_id)
    agents = context.get("agents_preview") or generate_dynamic_agents_from_context(context_text, context.get("agent_count") or AGENT_DEFAULT_COUNT)
    ontology = context.get("ontology") or build_prompt_ontology(context_text, agents)
    project_name = context.get("project_name") or "Projeto MiroFish Render"
    nodes = [_node(project_id, project_name, "Project", "Projeto/cenário analisado pelo MiroFish.", {"source_type": context.get("source_type") or "unknown", "backend_version": BACKEND_VERSION, "context_size": len(context_text or "")})]
    for agent in agents:
        nodes.append(_node(agent["id"], agent["name"], "Agent", agent.get("persona") or agent.get("role") or "Agente dinâmico de simulação.", {"role": agent.get("role"), "profession": agent.get("profession"), "profile": agent.get("profile"), "generation_mode": agent.get("generation_mode"), "selection_reason": agent.get("selection_reason")}))
    for entity in ontology.get("entities", []):
        nodes.append(_node(entity["uuid"], entity["name"], entity.get("type", "BusinessObject"), entity.get("summary", ""), entity.get("attributes", {})))
    edges = []
    for agent in agents:
        edges.append(_edge(project_id, agent["id"], "USES_AGENT", f"{project_name} usa {agent['name']} para analisar o cenário.", "USES_AGENT", project_name, agent["name"]))
    for idx in range(len(agents) - 1):
        source = agents[idx]; target = agents[idx + 1]
        edges.append(_edge(source["id"], target["id"], "COLLABORATES_WITH", f"{source['name']} complementa a análise de {target['name']}.", "COLLABORATES_WITH", source["name"], target["name"]))
    if len(agents) > 2:
        edges.append(_edge(agents[-1]["id"], agents[0]["id"], "FEEDS_BACK_TO", f"{agents[-1]['name']} devolve conclusões para {agents[0]['name']} fechar o ciclo analítico.", "FEEDS_BACK_TO", agents[-1]["name"], agents[0]["name"]))
    for entity in ontology.get("entities", []):
        edges.append(_edge(project_id, entity["uuid"], "HAS_CONTEXT_ENTITY", f"{project_name} contém a entidade de negócio {entity['name']}.", "HAS_CONTEXT_ENTITY", project_name, entity["name"]))
    for rel in ontology.get("relations", []):
        edges.append(rel)
    entities = ontology.get("entities", [])
    for agent in agents:
        profile = str(agent.get("profile") or agent.get("profession") or "").lower()
        for entity in entities[:20]:
            entity_type = str(entity.get("type") or "").lower()
            should_connect = (("finance" in profile and entity_type == "financialmetric") or ("risco" in profile and entity_type == "risk") or ("dados" in profile and entity_type in {"kpi", "businessobject"}) or ("produto" in profile and entity_type in {"decision", "kpi", "scenario"}) or ("comercial" in profile and entity_type in {"asset", "externalfactor", "decision"}) or ("arquitetura" in profile and entity_type == "businessobject"))
            if should_connect:
                edges.append(_edge(agent["id"], entity["uuid"], "ANALYZES", f"{agent['name']} analisa {entity['name']} no cenário.", "ANALYZES", agent["name"], entity["name"]))
    unique_edges = {edge["uuid"]: edge for edge in edges}
    return {"project_id": project_id, "project_name": project_name, "context": context, "agents": agents, "ontology": ontology, "nodes": nodes, "edges": list(unique_edges.values())}


def build_graph_payload(graph_id: str) -> Dict[str, Any]:
    project_id = graph_id.replace("graph-", "") if graph_id.startswith("graph-") else "render-project"
    components = build_graph_components(project_id)
    data = {"graph_id": graph_id, "project_id": project_id, "nodes": components["nodes"], "edges": components["edges"], "relationships": components["edges"], "agents": components["agents"], "ontology": components["ontology"], "node_count": len(components["nodes"]), "edge_count": len(components["edges"]), "schema_types_count": len(components["ontology"].get("entity_types", [])), "status": "ready", "context_loaded": bool((components["context"].get("context_preview") or "").strip()), "context_size": len(components["context"].get("context_preview") or ""), "backend_version": BACKEND_VERSION}
    return response_ok({"status": "ready", "graph_id": graph_id, "project_id": project_id, "nodes": data["nodes"], "edges": data["edges"], "relationships": data["relationships"], "node_count": data["node_count"], "edge_count": data["edge_count"], "schema_types_count": data["schema_types_count"], "data": data, "result": data, "graph": data})


def project_payload(project_id: str) -> Dict[str, Any]:
    context = get_current_project_context(project_id)
    context_text = context.get("context_preview") or get_context_text(project_id)
    agents = context.get("agents_preview") or generate_dynamic_agents_from_context(context_text, context.get("agent_count") or AGENT_DEFAULT_COUNT)
    ontology = context.get("ontology") or build_prompt_ontology(context_text, agents)
    graph_id = f"graph-{project_id}"
    components = build_graph_components(project_id)
    return {"project_id": project_id, "id": project_id, "name": context.get("project_name") or "Projeto MiroFish Render", "status": "graph_completed" if context.get("graph_completed") else "ontology_generated", "state": "graph_completed" if context.get("graph_completed") else "ontology_generated", "graph_id": graph_id, "ontology_id": f"ontology-{project_id}", "ontology": ontology, "agents_preview": agents, "context_loaded": bool(context_text.strip()), "context_size": len(context_text or ""), "node_count": len(components["nodes"]), "edge_count": len(components["edges"]), "schema_types_count": len(ontology.get("entity_types", [])), "backend_version": BACKEND_VERSION}


@app.middleware("http")
async def graph_contract_middleware(request: Request, call_next):
    path = request.url.path
    intercepted = path.startswith("/api/graph/data/") or path.startswith("/api/graph/project/") or path == "/api/graph/ontology/generate" or path == "/api/graph/build" or path == "/api/simulation/history" or (path.startswith("/api/simulation/") and path.endswith("/metadata"))
    if request.method == "OPTIONS" and intercepted:
        return cors_json({"ok": True})
    if request.method == "GET" and path == "/api/simulation/history":
        try:
            query = request.query_params.get("query", ""); limit = int(request.query_params.get("limit", "20")); items = build_history(limit=limit, query=query)
            return cors_json(response_ok({"status": "completed", "state": "completed", "count": len(items), "query": query, "data": items, "result": items, "backend_version": BACKEND_VERSION}))
        except Exception as exc:
            return cors_json({"success": False, "ok": False, "status": "error", "message": f"Falha ao listar histórico: {exc}"}, 500)
    if request.method in {"POST", "PATCH"} and path.startswith("/api/simulation/") and path.endswith("/metadata"):
        try:
            simulation_id = path.split("/")[3]; sim = SIMULATIONS.get(simulation_id)
            if not sim:
                return cors_json({"success": False, "ok": False, "status": "not_found", "message": "Simulação não encontrada."}, 404)
            body = await request.json(); metadata = sim.setdefault("metadata", {})
            if "simulation_name" in body: metadata["simulation_name"] = str(body.get("simulation_name") or "").strip()
            if "name" in body: metadata["simulation_name"] = str(body.get("name") or "").strip()
            if "tags" in body: metadata["tags"] = body.get("tags") or []
            if "notes" in body: metadata["notes"] = str(body.get("notes") or "")
            metadata["updated_at"] = utc_now(); sim["updated_at"] = utc_now(); entry = build_history_entry(sim)
            return cors_json(response_ok({"status": "updated", "state": "updated", "data": entry, "result": entry}))
        except Exception as exc:
            return cors_json({"success": False, "ok": False, "status": "error", "message": f"Falha ao atualizar metadados: {exc}"}, 500)
    if request.method == "POST" and path == "/api/graph/ontology/generate":
        try:
            parsed = await parse_request_context(request); fields = parsed["fields"]
            project_id = str(fields.get("project_id") or fields.get("projectId") or "render-project")
            project_name = str(fields.get("project_name") or fields.get("projectName") or "Projeto MiroFish Render")
            source_type = str(fields.get("source_type") or fields.get("sourceType") or "text")
            agent_count = clamp_int(fields.get("agent_count") or fields.get("agents_count") or AGENT_DEFAULT_COUNT, AGENT_DEFAULT_COUNT, AGENT_MIN_COUNT, AGENT_MAX_COUNT)
            context_text = parsed["context_text"] or "Cenário não informado. Solicitar ao usuário o objetivo, dados de entrada, restrições e resultado esperado."
            agents = generate_dynamic_agents_from_context(context_text, agent_count); ontology = build_prompt_ontology(context_text, agents)
            PROJECT_CONTEXTS[project_id] = {"project_id": project_id, "project_name": project_name, "prompt": context_text, "source_type": source_type, "agent_count": agent_count, "agents_preview": agents, "ontology": ontology, "context_preview": context_text[:12000], "file_names": parsed["file_names"], "raw_fields": fields, "updated_at": utc_now()}
            task_id = f"ontology_generate_{uuid4().hex}"
            task = {"task_id": task_id, "id": task_id, "project_id": project_id, "graph_id": f"graph-{project_id}", "ontology_id": f"ontology-{project_id}", "status": "completed", "state": "completed", "message": "Ontology generation completed with prompt-derived entities, relations and schema types.", "ontology": ontology, "agents_preview": agents, "context_loaded": bool(context_text.strip()), "context_size": len(context_text), "node_count": 1 + len(agents) + len(ontology.get("entities", [])), "edge_count": len(ontology.get("relations", [])), "schema_types_count": len(ontology.get("entity_types", [])), "created_at": utc_now(), "updated_at": utc_now()}
            BUILD_TASKS[task_id] = task
            return cors_json(response_ok({"status": "completed", "state": "completed", "task_id": task_id, "data": task, "result": task, "task": task}))
        except Exception as exc:
            return cors_json({"success": False, "ok": False, "status": "error", "state": "error", "message": f"Falha ao gerar ontologia real do cenário: {exc}"}, 500)
    if request.method == "POST" and path == "/api/graph/build":
        try:
            parsed = await parse_request_context(request); fields = parsed["fields"]
            project_id = str(fields.get("project_id") or fields.get("projectId") or "render-project")
            context = PROJECT_CONTEXTS.get(project_id) or PROJECT_CONTEXTS.get("render-project") or {}
            context_text = context.get("context_preview") or parsed.get("context_text") or get_context_text(project_id)
            agents = context.get("agents_preview") or generate_dynamic_agents_from_context(context_text, context.get("agent_count") or AGENT_DEFAULT_COUNT)
            ontology = context.get("ontology") or build_prompt_ontology(context_text, agents)
            context.update({"agents_preview": agents, "ontology": ontology, "graph_completed": True, "updated_at": utc_now()}); PROJECT_CONTEXTS[project_id] = context
            components = build_graph_components(project_id); task_id = f"graph_build_{uuid4().hex}"
            task = {"task_id": task_id, "id": task_id, "project_id": project_id, "graph_id": f"graph-{project_id}", "ontology_id": f"ontology-{project_id}", "status": "completed", "state": "completed", "progress": 100, "message": "GraphRAG build completed from prompt ontology.", "nodes_created": len(components["nodes"]), "relationships_created": len(components["edges"]), "schema_types_count": len(ontology.get("entity_types", [])), "entity_nodes": len(components["nodes"]), "relation_edges": len(components["edges"]), "schema_types": ontology.get("entity_types", []), "ontology": ontology, "created_at": utc_now(), "updated_at": utc_now()}
            BUILD_TASKS[task_id] = task
            return cors_json(response_ok({"status": "completed", "state": "completed", "task_id": task_id, "data": task, "result": task, "task": task}))
        except Exception as exc:
            return cors_json({"success": False, "ok": False, "status": "error", "state": "error", "message": f"Falha ao construir GraphRAG real: {exc}"}, 500)
    if request.method == "GET" and path.startswith("/api/graph/project/"):
        project_id = path.rsplit("/", 1)[-1]; payload = project_payload(project_id)
        return cors_json(response_ok({"status": payload["status"], "state": payload["state"], "project_id": project_id, "graph_id": payload["graph_id"], "data": payload, "result": payload, "project": payload}))
    if request.method == "GET" and path.startswith("/api/graph/data/"):
        graph_id = path.rsplit("/", 1)[-1]
        return cors_json(build_graph_payload(graph_id))
    return await call_next(request)
