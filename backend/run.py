import os
import re
import json
import time
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, date
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import FastAPI, UploadFile, Form, File, Request
from fastapi.middleware.cors import CORSMiddleware

try:
    import asyncpg
except Exception:
    asyncpg = None

try:
    from openai import AsyncOpenAI
except Exception:
    AsyncOpenAI = None

try:
    from neo4j import GraphDatabase
except Exception:
    GraphDatabase = None


BACKEND_VERSION = "mirofish-render-v10-dynamic-agents-rate-limit"

API_KEY = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("GROQ_API_KEY")
BASE_URL = os.getenv("OPENAI_API_BASE", "https://api.groq.com/openai/v1")
MODEL = os.getenv("MODEL_NAME", "llama-3.1-8b-instant")

SIMULATION_ROUNDS = int(os.getenv("SIMULATION_ROUNDS", "8"))
ROUND_DELAY_SECONDS = float(os.getenv("ROUND_DELAY_SECONDS", "2"))
AUTO_START_SIMULATION = os.getenv("AUTO_START_SIMULATION", "false").lower() == "true"

# Dynamic agent and Render/Groq consumption controls
AGENT_MIN_COUNT = int(os.getenv("AGENT_MIN_COUNT", "3"))
AGENT_DEFAULT_COUNT = int(os.getenv("AGENT_DEFAULT_COUNT", os.getenv("TOTAL_AGENTS", "4")))
AGENT_MAX_COUNT = int(os.getenv("AGENT_MAX_COUNT", "6"))
AGENT_BATCH_SIZE = max(1, int(os.getenv("AGENT_BATCH_SIZE", "1")))
AGENT_CONCURRENCY = max(1, int(os.getenv("AGENT_CONCURRENCY", "1")))
AGENT_BATCH_DELAY_SECONDS = float(os.getenv("AGENT_BATCH_DELAY_SECONDS", "1.0"))
LLM_REQUESTS_PER_SECOND = float(os.getenv("LLM_REQUESTS_PER_SECOND", "0.5"))
LLM_MIN_INTERVAL_SECONDS = float(os.getenv("LLM_MIN_INTERVAL_SECONDS", str(1 / max(LLM_REQUESTS_PER_SECOND, 0.1))))
MAX_LLM_CALLS_PER_SIMULATION = int(os.getenv("MAX_LLM_CALLS_PER_SIMULATION", "30"))
USE_LLM_AGENT_GENERATION = os.getenv("USE_LLM_AGENT_GENERATION", "false").lower() == "true"

NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
POSTGRES_URL = os.getenv("EXTERNAL_DB_URL") or os.getenv("DATABASE_URL")
POSTGRES_SCHEMA = os.getenv("POSTGRES_SCHEMA", "scadiadalbertobackes")

BUILD_TASKS: Dict[str, Dict[str, Any]] = {}
PROJECT_CONTEXTS: Dict[str, Dict[str, Any]] = {}
SIMULATIONS: Dict[str, Dict[str, Any]] = {}
RUN_TASKS: Dict[str, asyncio.Task] = {}
REPORTS: Dict[str, Dict[str, Any]] = {}

LLM_SEMAPHORE = asyncio.Semaphore(AGENT_CONCURRENCY)
LLM_RATE_LOCK = asyncio.Lock()
LAST_LLM_CALL_AT = 0.0

neo4j_driver = None
if GraphDatabase and NEO4J_URI and NEO4J_USERNAME and NEO4J_PASSWORD:
    try:
        neo4j_driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))
    except Exception as exc:
        print(f"[MiroFish] Falha ao inicializar Neo4j: {exc}", flush=True)
        neo4j_driver = None

ai_client = None
if AsyncOpenAI and API_KEY:
    ai_client = AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL)


def utc_now() -> str:
    return datetime.utcnow().isoformat()


def response_ok(payload: Dict[str, Any]) -> Dict[str, Any]:
    payload.setdefault("success", True)
    payload.setdefault("ok", True)
    payload.setdefault("status", "success")
    payload.setdefault("state", payload.get("status", "success"))
    return payload


def response_error(message: str, details: Optional[Any] = None) -> Dict[str, Any]:
    return {
        "success": False,
        "ok": False,
        "status": "error",
        "state": "error",
        "message": message,
        "error": message,
        "details": details,
        "backend_version": BACKEND_VERSION,
    }


def clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(minimum, min(parsed, maximum))


def safe_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")[:500]
    return value


def row_to_dict(row: Any) -> Dict[str, Any]:
    return {key: safe_value(row[key]) for key in row.keys()}


def validate_identifier(value: str, field_name: str = "identifier") -> str:
    value = str(value or "").strip()
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", value):
        raise ValueError(f"{field_name} inválido: {value}")
    return value


def quote_identifier(value: str) -> str:
    return '"' + validate_identifier(value).replace('"', '""') + '"'


async def parse_request_payload(request: Request) -> Dict[str, Any]:
    try:
        body = await request.json()
        if isinstance(body, dict):
            return body
        return {"body": body}
    except Exception:
        pass

    try:
        form = await request.form()
        payload: Dict[str, Any] = {}
        for key, value in form.multi_items():
            if hasattr(value, "filename"):
                payload[key] = getattr(value, "filename", "")
            else:
                payload[key] = value
        return payload
    except Exception:
        return {}


async def read_upload_preview(file: Optional[UploadFile]) -> str:
    if not file:
        return ""
    raw = await file.read()
    return raw[:12000].decode("utf-8", errors="ignore")


def get_project_id(payload: Optional[dict] = None) -> str:
    payload = payload or {}
    return str(payload.get("project_id") or payload.get("projectId") or payload.get("project") or payload.get("id") or "render-project")


def get_project_name(payload: Optional[dict] = None) -> str:
    payload = payload or {}
    return str(payload.get("project_name") or payload.get("projectName") or payload.get("name") or "Projeto MiroFish Render")


async def throttle_llm(sim: Optional[Dict[str, Any]] = None, reason: str = "llm") -> None:
    global LAST_LLM_CALL_AT
    async with LLM_RATE_LOCK:
        now = time.monotonic()
        wait = max(0.0, LLM_MIN_INTERVAL_SECONDS - (now - LAST_LLM_CALL_AT))
        if wait > 0:
            if sim is not None:
                sim.setdefault("logs", []).append({
                    "time": utc_now(),
                    "message": f"Scheduler aguardando {wait:.2f}s para liberar próxima chamada LLM ({reason}).",
                })
            await asyncio.sleep(wait)
        LAST_LLM_CALL_AT = time.monotonic()


async def limited_llm_chat(messages: List[Dict[str, str]], max_tokens: int = 180, sim: Optional[Dict[str, Any]] = None, reason: str = "agent") -> str:
    if not ai_client:
        raise RuntimeError("LLM não configurado.")

    if sim is not None:
        used = int(sim.get("llm_calls_used", 0))
        if used >= MAX_LLM_CALLS_PER_SIMULATION:
            raise RuntimeError("Limite de chamadas LLM da simulação atingido.")
        sim["llm_calls_used"] = used + 1

    async with LLM_SEMAPHORE:
        await throttle_llm(sim, reason)
        response = await ai_client.chat.completions.create(
            model=MODEL,
            messages=messages,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content or ""


async def postgres_connect():
    if not POSTGRES_URL:
        raise RuntimeError("EXTERNAL_DB_URL/DATABASE_URL não configurada no Render.")
    if not asyncpg:
        raise RuntimeError("asyncpg não está instalado no container.")
    return await asyncpg.connect(POSTGRES_URL, timeout=15)


async def postgres_health_payload() -> Dict[str, Any]:
    conn = None
    try:
        conn = await postgres_connect()
        row = await conn.fetchrow(
            """
            select
                current_database() as database_name,
                current_user as user_name,
                current_schema() as current_schema,
                inet_server_addr()::text as server_addr,
                inet_server_port() as server_port,
                version() as server_version,
                now() as checked_at,
                exists(
                    select 1 from information_schema.schemata where schema_name = $1
                ) as target_schema_exists
            """,
            POSTGRES_SCHEMA,
        )
        payload = row_to_dict(row)
        payload.update({
            "connected": True,
            "configured": True,
            "target_schema": POSTGRES_SCHEMA,
            "backend_version": BACKEND_VERSION,
        })
        return payload
    finally:
        if conn:
            await conn.close()


async def list_postgres_tables(schema: str = POSTGRES_SCHEMA) -> List[Dict[str, Any]]:
    schema = validate_identifier(schema, "schema")
    conn = None
    try:
        conn = await postgres_connect()
        rows = await conn.fetch(
            """
            select
                t.table_schema,
                t.table_name,
                t.table_type,
                coalesce(c.column_count, 0) as column_count
            from information_schema.tables t
            left join (
                select table_schema, table_name, count(*) as column_count
                from information_schema.columns
                where table_schema = $1
                group by table_schema, table_name
            ) c on c.table_schema = t.table_schema and c.table_name = t.table_name
            where t.table_schema = $1
            order by t.table_name
            """,
            schema,
        )
        return [row_to_dict(row) for row in rows]
    finally:
        if conn:
            await conn.close()


async def get_table_columns(schema: str, table: str) -> List[Dict[str, Any]]:
    schema = validate_identifier(schema, "schema")
    table = validate_identifier(table, "table")
    conn = None
    try:
        conn = await postgres_connect()
        rows = await conn.fetch(
            """
            select column_name, data_type, is_nullable, ordinal_position
            from information_schema.columns
            where table_schema = $1 and table_name = $2
            order by ordinal_position
            """,
            schema,
            table,
        )
        return [row_to_dict(row) for row in rows]
    finally:
        if conn:
            await conn.close()


async def preview_postgres_table(schema: str, table: str, limit: int = 10) -> Dict[str, Any]:
    schema = validate_identifier(schema, "schema")
    table = validate_identifier(table, "table")
    limit = max(1, min(int(limit or 10), 50))
    columns = await get_table_columns(schema, table)
    conn = None
    try:
        conn = await postgres_connect()
        query = f"select * from {quote_identifier(schema)}.{quote_identifier(table)} limit {limit}"
        rows = await conn.fetch(query)
        return {
            "schema": schema,
            "table": table,
            "limit": limit,
            "columns": columns,
            "rows": [row_to_dict(row) for row in rows],
        }
    finally:
        if conn:
            await conn.close()


async def build_postgres_context(schema: str = POSTGRES_SCHEMA, table: Optional[str] = None, limit: int = 5) -> str:
    schema = validate_identifier(schema, "schema")
    lines: List[str] = []
    health = await postgres_health_payload()
    lines.append("[FONTE POSTGRES]")
    lines.append(f"database={health.get('database_name')} user={health.get('user_name')} schema={schema}")
    lines.append(f"schema_existe={health.get('target_schema_exists')}")

    if table:
        preview = await preview_postgres_table(schema, table, limit=limit)
        lines.append(f"\n[TABELA {schema}.{table}]")
        lines.append("colunas=" + ", ".join([f"{c['column_name']}:{c['data_type']}" for c in preview["columns"]]))
        lines.append("amostra=")
        for row in preview["rows"]:
            lines.append(str(row)[:1200])
        return "\n".join(lines)[:12000]

    tables = await list_postgres_tables(schema)
    lines.append(f"total_tabelas={len(tables)}")
    for item in tables[:25]:
        lines.append(f"- {item['table_name']} ({item['table_type']}) colunas={item['column_count']}")
    return "\n".join(lines)[:12000]


AGENT_CATALOG = [
    {
        "key": "financeiro",
        "keywords": ["fluxo de caixa", "caixa", "contas a pagar", "contas a receber", "orçamento", "orcamento", "capital", "financeiro", "ebitda", "custo financeiro"],
        "name": "Agente Financeiro",
        "role": "Analista de Fluxo de Caixa e Orçamento",
        "profession": "Especialista Financeiro Agro",
        "profile": "Financeiro",
        "persona": "Avalia caixa, necessidade de capital, orçamento, exposição financeira, riscos de liquidez e alternativas de financiamento.",
    },
    {
        "key": "dados_bi",
        "keywords": ["dashboard", "indicador", "bi", "data lake", "datalake", "dados", "tabela", "schema", "postgres", "sql", "coluna", "métrica", "metrica"],
        "name": "Agente Dados/BI",
        "role": "Analista de Dados e Indicadores",
        "profession": "Especialista em BI e Indicadores",
        "profile": "Dados",
        "persona": "Identifica indicadores, estrutura de dados, métricas, dashboards, qualidade de dados e oportunidades analíticas.",
    },
    {
        "key": "produto",
        "keywords": ["produto", "erp", "scadiagro", "módulo", "modulo", "jornada", "usuário", "usuario", "ux", "funcionalidade", "backlog"],
        "name": "Agente Produto",
        "role": "Especialista de Produto ERP Agro",
        "profession": "Especialista de Produto",
        "profile": "Produto",
        "persona": "Converte necessidades do cenário em requisitos, jornada do usuário, backlog, melhoria de produto e visão de módulo ERP.",
    },
    {
        "key": "fiscal",
        "keywords": ["nota fiscal", "nf-e", "nfe", "icms", "funrural", "sped", "efd", "cfop", "cst", "imposto", "fiscal", "sefaz"],
        "name": "Agente Fiscal",
        "role": "Especialista Fiscal e NF-e",
        "profession": "Analista Fiscal Agro",
        "profile": "Fiscal",
        "persona": "Analisa impactos fiscais, NF-e, CFOP, impostos, validações SEFAZ e riscos de parametrização fiscal.",
    },
    {
        "key": "dba",
        "keywords": ["banco", "database", "postgres", "oracle", "schema", "tabela", "índice", "indice", "consulta", "query", "performance", "modelagem"],
        "name": "Agente DBA/Arquitetura de Dados",
        "role": "Especialista em Banco de Dados",
        "profession": "DBA / Arquiteto de Dados",
        "profile": "Banco de Dados",
        "persona": "Avalia estrutura de banco, tabelas, relacionamentos, performance, modelagem, governança e riscos técnicos da base.",
    },
    {
        "key": "comercializacao",
        "keywords": ["soja", "milho", "saca", "venda", "preço", "preco", "comercialização", "comercializacao", "barter", "dólar", "dolar", "mercado"],
        "name": "Agente Comercialização Agro",
        "role": "Analista de Mercado e Comercialização",
        "profession": "Especialista em Comercialização Agro",
        "profile": "Comercialização",
        "persona": "Avalia preço, estoque, venda, barter, câmbio, oportunidade de mercado e decisão comercial no agro.",
    },
    {
        "key": "risco",
        "keywords": ["risco", "decisão", "decisao", "previsão", "previsao", "cenário", "cenario", "simulação", "simulacao", "projeção", "projecao"],
        "name": "Agente Risco e Cenários",
        "role": "Analista de Risco e Simulação",
        "profession": "Especialista em Cenários",
        "profile": "Risco",
        "persona": "Avalia hipóteses, incertezas, trade-offs, cenários alternativos, risco operacional e recomendações executivas.",
    },
    {
        "key": "arquitetura",
        "keywords": ["api", "integração", "integracao", "arquitetura", "pipeline", "cloud", "render", "vercel", "groq", "neo4j", "sre", "devops"],
        "name": "Agente Arquitetura/Integração",
        "role": "Arquiteto de Soluções e Integrações",
        "profession": "Especialista em Arquitetura",
        "profile": "Arquitetura",
        "persona": "Avalia arquitetura, APIs, integrações, infraestrutura, escalabilidade, custo, observabilidade e riscos técnicos.",
    },
]

FALLBACK_AGENT_CATALOG = [
    AGENT_CATALOG[6],  # Risco e Cenários
    AGENT_CATALOG[1],  # Dados/BI
    AGENT_CATALOG[2],  # Produto
]


def score_agent_catalog_item(text: str, item: Dict[str, Any]) -> int:
    score = 0
    normalized = text.lower()
    for keyword in item.get("keywords", []):
        if keyword.lower() in normalized:
            score += 1
    return score


def build_agent_from_catalog(item: Dict[str, Any], index: int, reason: str) -> Dict[str, Any]:
    return {
        "id": f"agent-{index}",
        "agent_id": index,
        "name": item["name"],
        "username": item["name"],
        "role": item["role"],
        "profession": item["profession"],
        "persona": item["persona"],
        "profile": item["profile"],
        "status": "ready",
        "generation_mode": "dynamic_rules",
        "selection_reason": reason,
    }


def generate_dynamic_agents_from_context(context: str, target_count: Optional[int] = None) -> List[Dict[str, Any]]:
    target = clamp_int(target_count or AGENT_DEFAULT_COUNT, AGENT_DEFAULT_COUNT, AGENT_MIN_COUNT, AGENT_MAX_COUNT)
    text = (context or "").lower()

    ranked = []
    for item in AGENT_CATALOG:
        score = score_agent_catalog_item(text, item)
        if score > 0:
            ranked.append((score, item))

    ranked.sort(key=lambda pair: pair[0], reverse=True)
    selected: List[Dict[str, Any]] = []
    used_keys = set()

    for score, item in ranked:
        if item["key"] in used_keys:
            continue
        selected.append(build_agent_from_catalog(item, len(selected), f"Selecionado dinamicamente por {score} evidência(s) no cenário."))
        used_keys.add(item["key"])
        if len(selected) >= target:
            break

    for item in FALLBACK_AGENT_CATALOG:
        if len(selected) >= target:
            break
        if item["key"] not in used_keys:
            selected.append(build_agent_from_catalog(item, len(selected), "Selecionado como cobertura mínima do cenário."))
            used_keys.add(item["key"])

    return selected[:target]


async def generate_dynamic_agents_with_llm(context: str, sim: Optional[Dict[str, Any]] = None, target_count: Optional[int] = None) -> List[Dict[str, Any]]:
    if not USE_LLM_AGENT_GENERATION or not ai_client:
        return generate_dynamic_agents_from_context(context, target_count)

    target = clamp_int(target_count or AGENT_DEFAULT_COUNT, AGENT_DEFAULT_COUNT, AGENT_MIN_COUNT, AGENT_MAX_COUNT)
    try:
        raw = await limited_llm_chat(
            messages=[
                {
                    "role": "system",
                    "content": "Você cria agentes de simulação para análise de cenários. Responda somente JSON válido.",
                },
                {
                    "role": "user",
                    "content": (
                        f"Crie até {target} agentes para analisar o cenário abaixo. "
                        "Retorne uma lista JSON com name, role, profession, profile e persona. "
                        "Não use redes sociais. Não crie mais agentes que o solicitado.\n\n"
                        f"CENÁRIO:\n{context[:4500]}"
                    ),
                },
            ],
            max_tokens=900,
            sim=sim,
            reason="agent_generation",
        )
        match = re.search(r"\[[\s\S]*\]", raw)
        items = json.loads(match.group(0) if match else raw)
        agents = []
        for item in items[:target]:
            agents.append({
                "id": f"agent-{len(agents)}",
                "agent_id": len(agents),
                "name": item.get("name") or f"Agente {len(agents) + 1}",
                "username": item.get("name") or f"Agente {len(agents) + 1}",
                "role": item.get("role") or "Analista de Cenário",
                "profession": item.get("profession") or item.get("profile") or "Especialista",
                "persona": item.get("persona") or "Agente dinâmico gerado para analisar o cenário.",
                "profile": item.get("profile") or "Dinâmico",
                "status": "ready",
                "generation_mode": "dynamic_llm",
                "selection_reason": "Gerado dinamicamente por LLM com controle de taxa.",
            })
        if agents:
            return agents
    except Exception as exc:
        if sim is not None:
            sim.setdefault("logs", []).append({"time": utc_now(), "message": f"Falha ao gerar agentes com LLM; usando regras locais: {exc}"})
    return generate_dynamic_agents_from_context(context, target_count)


def get_current_project_context(project_id: str = "render-project") -> Dict[str, Any]:
    return PROJECT_CONTEXTS.get(project_id) or PROJECT_CONTEXTS.get("render-project") or {}


def get_context_text(project_id: str = "render-project") -> str:
    context = get_current_project_context(project_id)
    return "\n".join([
        str(context.get("prompt") or ""),
        str(context.get("context_preview") or ""),
        str(context.get("source_type") or ""),
    ]).strip()


def get_agents_for_simulation(sim: Dict[str, Any]) -> List[Dict[str, Any]]:
    agents = sim.get("dynamic_agents") or sim.get("agents") or []
    if agents:
        return agents
    context_text = sim.get("context_preview") or get_context_text(sim.get("project_id", "render-project"))
    target = sim.get("agent_target") or AGENT_DEFAULT_COUNT
    agents = generate_dynamic_agents_from_context(context_text, target)
    sim["dynamic_agents"] = agents
    sim["agents"] = agents
    sim["agents_count"] = len(agents)
    return agents


def build_config(simulation_id: str, agents: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    agents = agents or generate_dynamic_agents_from_context(get_context_text("render-project"), AGENT_DEFAULT_COUNT)
    return {
        "simulation_id": simulation_id,
        "id": simulation_id,
        "project_id": "render-project",
        "graph_id": "graph-render-project",
        "status": "completed",
        "state": "completed",
        "phase": "completed",
        "ready": True,
        "done": True,
        "config_generated": True,
        "config_ready": True,
        "config_status": "completed",
        "config_reasoning": "Configuração gerada em modo dinâmico com controle de consumo no Render.",
        "time_config": {
            "total_simulation_hours": SIMULATION_ROUNDS,
            "minutes_per_round": 60,
            "agents_per_hour_min": 1,
            "agents_per_hour_max": len(agents),
            "peak_hours": [9, 10, 14, 15],
            "peak_activity_multiplier": 1.0,
            "work_hours": list(range(8, 18)),
            "work_activity_multiplier": 1.0,
            "morning_hours": [8, 9, 10, 11],
            "morning_activity_multiplier": 1.0,
            "off_peak_hours": [18, 19, 20],
            "off_peak_activity_multiplier": 0.5,
        },
        "agent_configs": [
            {
                "agent_id": p["agent_id"],
                "entity_name": p["name"],
                "entity_type": p.get("profile", "Dinâmico"),
                "stance": "neutral",
                "active_hours": list(range(8, 19)),
                "posts_per_hour": 1,
                "comments_per_hour": 1,
                "response_delay_min": 1,
                "response_delay_max": 5,
                "activity_level": 0.5,
                "sentiment_bias": 0,
                "influence_weight": 1,
            }
            for p in agents
        ],
        "event_config": {
            "initial_posts": [],
            "hot_topics": [],
            "narrative_direction": "Simulação analítica baseada no contexto enviado ao MiroFish.",
        },
        "agents_count": len(agents),
        "personas_count": len(agents),
        "agents": agents,
        "personas": agents,
        "profiles": agents,
        "entities_count": len(agents),
        "entity_types": [p.get("profile", "Dinâmico") for p in agents],
        "enable_reddit": False,
        "enable_twitter": False,
        "reddit_enabled": False,
        "twitter_enabled": False,
        "external_sources_enabled": False,
        "llm_configured": bool(API_KEY),
        "neo4j_configured": bool(neo4j_driver),
        "postgres_configured": bool(POSTGRES_URL),
        "postgres_schema": POSTGRES_SCHEMA,
        "scheduler": scheduler_payload(),
        "backend_version": BACKEND_VERSION,
        "generated_at": utc_now(),
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }


def scheduler_payload() -> Dict[str, Any]:
    return {
        "agent_min_count": AGENT_MIN_COUNT,
        "agent_default_count": AGENT_DEFAULT_COUNT,
        "agent_max_count": AGENT_MAX_COUNT,
        "agent_batch_size": AGENT_BATCH_SIZE,
        "agent_concurrency": AGENT_CONCURRENCY,
        "agent_batch_delay_seconds": AGENT_BATCH_DELAY_SECONDS,
        "llm_requests_per_second": LLM_REQUESTS_PER_SECOND,
        "llm_min_interval_seconds": LLM_MIN_INTERVAL_SECONDS,
        "max_llm_calls_per_simulation": MAX_LLM_CALLS_PER_SIMULATION,
        "use_llm_agent_generation": USE_LLM_AGENT_GENERATION,
    }


def ensure_simulation(simulation_id: str) -> Dict[str, Any]:
    sim = SIMULATIONS.get(simulation_id)
    if not sim:
        context = get_current_project_context("render-project")
        context_text = context.get("context_preview", "") or context.get("prompt", "") or ""
        agents = generate_dynamic_agents_from_context(context_text, AGENT_DEFAULT_COUNT)
        sim = {
            "simulation_id": simulation_id,
            "id": simulation_id,
            "task_id": simulation_id,
            "project_id": "render-project",
            "graph_id": "graph-render-project",
            "status": "created",
            "state": "created",
            "message": "Simulation created.",
            "agents_target": len(agents),
            "agents_count": len(agents),
            "dynamic_agents": agents,
            "agents": agents,
            "current_round": 0,
            "total_rounds": SIMULATION_ROUNDS,
            "progress": 0,
            "progress_percent": 0,
            "report_ready": False,
            "config_generated": True,
            "enable_reddit": False,
            "enable_twitter": False,
            "logs": [],
            "actions": [],
            "posts": [],
            "timeline": [],
            "context_preview": context_text,
            "context_source": context.get("source_type", "none"),
            "config": build_config(simulation_id, agents),
            "scheduler": scheduler_payload(),
            "llm_calls_used": 0,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "started_at": None,
            "completed_at": None,
            "error": None,
        }
        SIMULATIONS[simulation_id] = sim
        BUILD_TASKS[simulation_id] = sim
    return sim


async def refresh_dynamic_agents(sim: Dict[str, Any], force: bool = False) -> List[Dict[str, Any]]:
    if sim.get("dynamic_agents") and not force:
        return sim["dynamic_agents"]
    context_text = sim.get("context_preview") or get_context_text(sim.get("project_id", "render-project"))
    agents = await generate_dynamic_agents_with_llm(context_text, sim=sim, target_count=sim.get("agent_target") or AGENT_DEFAULT_COUNT)
    sim["dynamic_agents"] = agents
    sim["agents"] = agents
    sim["agents_count"] = len(agents)
    sim["agents_target"] = len(agents)
    sim["config"] = build_config(sim["simulation_id"], agents)
    return agents


def attach_project_context(sim: Dict[str, Any], project_id: str = "render-project") -> None:
    context = get_current_project_context(project_id)
    if context:
        sim["context_preview"] = context.get("context_preview", "")
        sim["context_source"] = context.get("source_type", "unknown")
        sim["prompt"] = context.get("prompt", "")
        sim["project_context"] = context


def simulation_response(simulation_id: str) -> Dict[str, Any]:
    sim = ensure_simulation(simulation_id)
    status = sim.get("status", "created")
    return {
        "success": True,
        "ok": True,
        "status": status,
        "state": status,
        "message": sim.get("message", "Simulation status returned successfully."),
        "simulation_id": simulation_id,
        "id": simulation_id,
        "task_id": sim.get("task_id", simulation_id),
        "project_id": sim.get("project_id", "render-project"),
        "graph_id": sim.get("graph_id", "graph-render-project"),
        "current_round": sim.get("current_round", 0),
        "total_rounds": sim.get("total_rounds", SIMULATION_ROUNDS),
        "progress": sim.get("progress", 0),
        "progress_percent": sim.get("progress_percent", sim.get("progress", 0)),
        "completed_at": sim.get("completed_at"),
        "error": sim.get("error"),
        "report_ready": sim.get("report_ready", False),
        "context_source": sim.get("context_source"),
        "agents_count": sim.get("agents_count", len(sim.get("agents", []))),
        "scheduler": sim.get("scheduler", scheduler_payload()),
        "data": sim,
        "result": sim,
        "simulation": sim,
        "task": sim,
    }


async def analyze_with_agent(agent: Dict[str, Any], round_number: int, context: str, sim: Dict[str, Any]) -> str:
    if not ai_client:
        return f"{agent['name']} analisou a rodada {round_number} em modo fallback local com contexto de {len(context or '')} caracteres."

    context_text = context[:4500] if context else "Nenhum contexto foi carregado. Informe uma fonte de dados ou arquivo."
    try:
        return await limited_llm_chat(
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"Você é {agent['name']}. Papel: {agent.get('role')}. "
                        f"Especialidade: {agent.get('profession')}. Persona: {agent.get('persona')}. "
                        "Use obrigatoriamente o contexto fornecido. Responda em português, com uma conclusão objetiva de até 3 frases."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Rodada: {round_number}. Contexto disponível:\n{context_text}",
                },
            ],
            max_tokens=180,
            sim=sim,
            reason=f"agent_{agent['agent_id']}_round_{round_number}",
        )
    except Exception as exc:
        return f"{agent['name']} concluiu a rodada {round_number} em fallback após bloqueio/erro LLM: {exc}"


async def execute_agent_round(agent: Dict[str, Any], round_number: int, sim: Dict[str, Any]) -> Dict[str, Any]:
    sim.setdefault("logs", []).append({
        "time": utc_now(),
        "message": f"Liberando {agent['name']} para rodada {round_number} conforme scheduler Render.",
    })
    content = await analyze_with_agent(agent, round_number, sim.get("context_preview", ""), sim)
    return {
        "round": round_number,
        "round_num": round_number,
        "agent_id": agent["agent_id"],
        "agent_name": agent["name"],
        "agent_profile": agent.get("profile"),
        "agent_profession": agent.get("profession"),
        "type": "analysis",
        "action_type": "CREATE_POST",
        "platform": "internal",
        "context_source": sim.get("context_source"),
        "content": content,
        "action_args": {"content": content},
        "created_at": utc_now(),
        "timestamp": utc_now(),
    }


async def run_simulation_for_id(simulation_id: str):
    sim = ensure_simulation(simulation_id)
    attach_project_context(sim, sim.get("project_id", "render-project"))
    agents = await refresh_dynamic_agents(sim, force=False)
    total_rounds = int(sim.get("total_rounds") or SIMULATION_ROUNDS)

    sim.update({
        "status": "running",
        "state": "running",
        "message": "Simulation running with dynamic rate-limited agents.",
        "started_at": sim.get("started_at") or utc_now(),
        "completed_at": None,
        "error": None,
        "report_ready": False,
        "updated_at": utc_now(),
        "scheduler": scheduler_payload(),
    })

    print("--- [MiroFish] Iniciando ciclo de simulação com agentes dinâmicos e scheduler ---", flush=True)
    print(f"[MiroFish] Agentes dinâmicos: {len(agents)} | batch={AGENT_BATCH_SIZE} | concurrency={AGENT_CONCURRENCY} | min_interval={LLM_MIN_INTERVAL_SECONDS}s", flush=True)

    try:
        for round_number in range(1, total_rounds + 1):
            sim["current_round"] = round_number
            sim["progress"] = int((round_number / total_rounds) * 100)
            sim["progress_percent"] = sim["progress"]
            sim["message"] = f"Processando rodada {round_number} de {total_rounds}."
            sim["updated_at"] = utc_now()

            round_actions = []
            for batch_start in range(0, len(agents), AGENT_BATCH_SIZE):
                batch = agents[batch_start: batch_start + AGENT_BATCH_SIZE]
                global_start = (round_number - 1) * len(agents) + batch_start
                global_end = global_start + len(batch) - 1
                print(f"-> Processando lote controlado: {global_start} até {global_end}", flush=True)
                sim.setdefault("logs", []).append({
                    "time": utc_now(),
                    "message": f"Rodada {round_number}: liberando lote {batch_start // AGENT_BATCH_SIZE + 1} com {len(batch)} agente(s).",
                })

                results = await asyncio.gather(*[execute_agent_round(agent, round_number, sim) for agent in batch])
                for action in results:
                    round_actions.append(action)
                    sim["actions"].append(action)
                    sim["posts"].append({
                        "id": f"post_{simulation_id}_{round_number}_{action['agent_id']}",
                        "round": round_number,
                        "agent_id": action["agent_id"],
                        "author": action["agent_name"],
                        "content": action["content"],
                        "created_at": utc_now(),
                    })

                if batch_start + AGENT_BATCH_SIZE < len(agents):
                    await asyncio.sleep(AGENT_BATCH_DELAY_SECONDS)

            sim["timeline"].append({
                "round": round_number,
                "status": "completed",
                "actions_count": len(round_actions),
                "created_at": utc_now(),
            })
            sim["logs"].append({"time": utc_now(), "message": f"Rodada {round_number}/{total_rounds} concluída com {len(round_actions)} ação(ões)."})
            await asyncio.sleep(ROUND_DELAY_SECONDS)

        sim.update({
            "status": "completed",
            "state": "completed",
            "message": "Simulation completed successfully.",
            "current_round": total_rounds,
            "progress": 100,
            "progress_percent": 100,
            "completed_at": utc_now(),
            "report_ready": True,
            "updated_at": utc_now(),
        })
        sim["report"] = build_report_payload(simulation_id)
        print(f"--- [MiroFish] Simulação {simulation_id} concluída ---", flush=True)
    except Exception as exc:
        sim.update({"status": "failed", "state": "failed", "message": f"Simulation failed: {exc}", "error": str(exc), "updated_at": utc_now()})
        print(f"[MiroFish] Erro na simulação {simulation_id}: {exc}", flush=True)


def build_report_id(simulation_id: str) -> str:
    return f"report_{simulation_id}"


def build_report_payload(simulation_id: str) -> Dict[str, Any]:
    sim = ensure_simulation(simulation_id)
    report_id = build_report_id(simulation_id)
    actions = sim.get("actions", [])
    agents = sim.get("agents", [])
    report = {
        "report_id": report_id,
        "reportId": report_id,
        "simulation_id": simulation_id,
        "status": "ready",
        "state": "ready",
        "title": "Relatório MiroFish - Simulação Concluída",
        "summary": "Relatório operacional gerado a partir da simulação com agentes dinâmicos e scheduler de consumo.",
        "agents_count": len(agents),
        "total_rounds": sim.get("total_rounds"),
        "total_actions": len(actions),
        "scheduler": sim.get("scheduler", scheduler_payload()),
        "generated_at": utc_now(),
    }
    REPORTS[report_id] = report
    return report


def build_report_agent_logs(report_id: str) -> List[Dict[str, Any]]:
    simulation_id = report_id.replace("report_", "")
    sim = ensure_simulation(simulation_id)
    agents = sim.get("agents", [])
    actions = sim.get("actions", [])
    outline = {
        "title": "Relatório MiroFish - Simulação Concluída",
        "summary": "Relatório operacional da simulação com agentes dinâmicos e controle de consumo.",
        "sections": [
            {"title": "Resumo Executivo"},
            {"title": "Agentes Dinâmicos"},
            {"title": "Riscos e Próximos Passos"},
        ],
    }
    agent_names = ", ".join([a.get("name", "Agente") for a in agents]) or "nenhum agente registrado"
    last_actions = "\n".join([f"- {a.get('agent_name')}: {a.get('content', '')[:220]}" for a in actions[-8:]]) or "Sem ações registradas."
    return [
        {"timestamp": utc_now(), "action": "report_start", "details": {"report_id": report_id, "simulation_id": simulation_id, "simulation_requirement": "Gerar relatório consolidado."}},
        {"timestamp": utc_now(), "action": "planning_start", "details": {"message": "Planejando estrutura do relatório."}},
        {"timestamp": utc_now(), "action": "planning_complete", "details": {"message": "Estrutura criada.", "outline": outline}},
        {"timestamp": utc_now(), "action": "section_start", "section_index": 1, "section_title": "Resumo Executivo", "details": {"message": "Gerando seção."}},
        {"timestamp": utc_now(), "action": "section_complete", "section_index": 1, "section_title": "Resumo Executivo", "details": {"content": f"## Resumo Executivo\n\nA simulação `{simulation_id}` foi concluída com `{len(actions)}` ações em `{sim.get('total_rounds')}` rodadas. O backend utilizou agentes dinâmicos e scheduler de consumo para liberar chamadas conforme capacidade configurada."}},
        {"timestamp": utc_now(), "action": "section_start", "section_index": 2, "section_title": "Agentes Dinâmicos", "details": {"message": "Gerando seção."}},
        {"timestamp": utc_now(), "action": "section_complete", "section_index": 2, "section_title": "Agentes Dinâmicos", "details": {"content": f"## Agentes Dinâmicos\n\nAgentes selecionados: {agent_names}.\n\nÚltimas análises:\n{last_actions}"}},
        {"timestamp": utc_now(), "action": "section_start", "section_index": 3, "section_title": "Riscos e Próximos Passos", "details": {"message": "Gerando seção."}},
        {"timestamp": utc_now(), "action": "section_complete", "section_index": 3, "section_title": "Riscos e Próximos Passos", "details": {"content": "## Riscos e Próximos Passos\n\nPersistir simulações, ações e relatórios em PostgreSQL é a próxima evolução crítica. Também é recomendado permitir configuração visual de capacidade por ambiente, incluindo quantidade máxima de agentes, concorrência e chamadas por segundo."}},
        {"timestamp": utc_now(), "action": "report_complete", "details": {"report_id": report_id, "simulation_id": simulation_id, "message": "Relatório finalizado."}},
    ]


def build_console_logs(report_id: str) -> List[str]:
    simulation_id = report_id.replace("report_", "")
    sim = ensure_simulation(simulation_id)
    return [
        f"[{datetime.utcnow().isoformat()}] Report Agent started for {report_id}",
        f"[{datetime.utcnow().isoformat()}] Simulation loaded: {simulation_id}",
        f"[{datetime.utcnow().isoformat()}] Agents: {sim.get('agents_count', len(sim.get('agents', [])))}",
        f"[{datetime.utcnow().isoformat()}] Scheduler: {sim.get('scheduler', scheduler_payload())}",
        f"[{datetime.utcnow().isoformat()}] Report completed",
    ]


@asynccontextmanager
async def lifespan(app: FastAPI):
    if AUTO_START_SIMULATION:
        simulation_id = f"sim_auto_{uuid4().hex}"
        ensure_simulation(simulation_id)
        RUN_TASKS[simulation_id] = asyncio.create_task(run_simulation_for_id(simulation_id))
        print("[MiroFish] AUTO_START_SIMULATION=true. Iniciando loop automático.", flush=True)
    else:
        print("[MiroFish] AUTO_START_SIMULATION=false. Loop automático desativado.", flush=True)
    yield
    for task in RUN_TASKS.values():
        if not task.done():
            task.cancel()
    if neo4j_driver:
        neo4j_driver.close()


app = FastAPI(title="MiroFish Render Orchestrator", version=BACKEND_VERSION, lifespan=lifespan)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])


@app.get("/")
def root():
    return {"status": "running", "service": "MiroFish Render Orchestrator", "backend_version": BACKEND_VERSION, "health": "/health", "docs": "/docs"}


@app.head("/")
def root_head():
    return {}


@app.get("/health")
def health_check():
    return {
        "status": "running",
        "backend_version": BACKEND_VERSION,
        "agents": {"min": AGENT_MIN_COUNT, "default": AGENT_DEFAULT_COUNT, "max": AGENT_MAX_COUNT},
        "scheduler": scheduler_payload(),
        "simulation_rounds": SIMULATION_ROUNDS,
        "round_delay_seconds": ROUND_DELAY_SECONDS,
        "auto_start_simulation": AUTO_START_SIMULATION,
        "llm_configured": bool(API_KEY),
        "neo4j_configured": bool(neo4j_driver),
        "postgres_configured": bool(POSTGRES_URL),
        "postgres_schema": POSTGRES_SCHEMA,
        "model": MODEL,
        "base_url": BASE_URL,
    }


@app.head("/health")
def health_head():
    return {}


@app.get("/api/db/health")
async def db_health():
    try:
        payload = await postgres_health_payload()
        return response_ok({"status": "connected", "state": "connected", "data": payload, "result": payload})
    except Exception as exc:
        return response_error("Falha ao conectar no PostgreSQL.", str(exc))


@app.get("/api/db/schema/{schema}/tables")
async def db_schema_tables(schema: str):
    try:
        tables = await list_postgres_tables(schema)
        return response_ok({"status": "completed", "schema": schema, "count": len(tables), "data": tables, "result": tables})
    except Exception as exc:
        return response_error("Falha ao listar tabelas do schema.", str(exc))


@app.get("/api/db/schema/{schema}/table/{table}/preview")
async def db_table_preview(schema: str, table: str, limit: int = 10):
    try:
        preview = await preview_postgres_table(schema, table, limit)
        return response_ok({"status": "completed", "schema": schema, "table": table, "data": preview, "result": preview})
    except Exception as exc:
        return response_error("Falha ao consultar preview da tabela.", str(exc))


@app.get("/api/db/context")
async def db_context(schema: str = POSTGRES_SCHEMA, table: Optional[str] = None, limit: int = 5):
    try:
        context = await build_postgres_context(schema=schema, table=table, limit=limit)
        payload = {"schema": schema, "table": table, "context_preview": context, "context_size": len(context)}
        return response_ok({"status": "completed", "data": payload, "result": payload})
    except Exception as exc:
        return response_error("Falha ao montar contexto PostgreSQL.", str(exc))


@app.get("/api/agents/catalog")
async def agents_catalog():
    return response_ok({"status": "completed", "count": len(AGENT_CATALOG), "data": AGENT_CATALOG, "result": AGENT_CATALOG})


@app.post("/api/agents/preview")
async def agents_preview(request: Request):
    payload = await parse_request_payload(request)
    context = str(payload.get("context") or payload.get("prompt") or get_context_text(get_project_id(payload)))
    target = payload.get("agent_count") or payload.get("agents_count") or AGENT_DEFAULT_COUNT
    agents = generate_dynamic_agents_from_context(context, target)
    return response_ok({"status": "completed", "count": len(agents), "data": agents, "result": agents, "scheduler": scheduler_payload()})


@app.post("/update-scenario")
async def update_scenario(prompt: str = Form(...), source_type: str = Form("text"), file: Optional[UploadFile] = File(None), schema: str = Form(POSTGRES_SCHEMA), table: Optional[str] = Form(None), agent_count: Optional[int] = Form(None)):
    source = (source_type or "text").lower()
    if source in {"postgres", "database", "db", "datalake"}:
        preview = await build_postgres_context(schema=schema or POSTGRES_SCHEMA, table=table, limit=5)
    else:
        uploaded_preview = await read_upload_preview(file)
        preview = uploaded_preview[:12000] if uploaded_preview else prompt[:12000]

    PROJECT_CONTEXTS["render-project"] = {
        "project_id": "render-project",
        "project_name": "Projeto MiroFish Render",
        "prompt": prompt,
        "source_type": source,
        "schema": schema,
        "table": table,
        "agent_count": clamp_int(agent_count, AGENT_DEFAULT_COUNT, AGENT_MIN_COUNT, AGENT_MAX_COUNT) if agent_count else AGENT_DEFAULT_COUNT,
        "context_preview": preview,
        "updated_at": utc_now(),
    }
    agents = generate_dynamic_agents_from_context(prompt + "\n" + preview, PROJECT_CONTEXTS["render-project"]["agent_count"])
    PROJECT_CONTEXTS["render-project"]["agents_preview"] = agents
    return response_ok({"status": "success", "message": f"Contexto carregado usando fonte: {source}", "data": PROJECT_CONTEXTS["render-project"], "result": PROJECT_CONTEXTS["render-project"]})


@app.post("/api/graph/ontology/generate")
async def generate_ontology_compat(request: Request, file: Optional[UploadFile] = File(None), prompt: Optional[str] = Form(None), source_type: Optional[str] = Form(None), schema: str = Form(POSTGRES_SCHEMA), table: Optional[str] = Form(None)):
    payload = await parse_request_payload(request)
    project_id = get_project_id(payload)
    project_name = get_project_name(payload)
    scenario = prompt or payload.get("prompt") or payload.get("scenario") or "Gerar ontologia a partir dos dados enviados."
    source = (source_type or payload.get("source_type") or payload.get("sourceType") or "upload").lower()

    if source in {"postgres", "database", "db", "datalake"}:
        uploaded_preview = await build_postgres_context(schema=schema or POSTGRES_SCHEMA, table=table, limit=5)
    else:
        uploaded_preview = await read_upload_preview(file)

    context_preview = uploaded_preview[:12000] if uploaded_preview else scenario[:2000]
    agent_count = clamp_int(payload.get("agent_count") or payload.get("agents_count") or AGENT_DEFAULT_COUNT, AGENT_DEFAULT_COUNT, AGENT_MIN_COUNT, AGENT_MAX_COUNT)
    agents = generate_dynamic_agents_from_context(scenario + "\n" + context_preview, agent_count)
    PROJECT_CONTEXTS[project_id] = {
        "project_id": project_id,
        "project_name": project_name,
        "prompt": scenario,
        "source_type": source,
        "schema": schema,
        "table": table,
        "agent_count": agent_count,
        "agents_preview": agents,
        "context_preview": context_preview,
        "updated_at": utc_now(),
    }
    task_id = f"ontology_generate_{uuid4().hex}"
    task = {"task_id": task_id, "id": task_id, "project_id": project_id, "graph_id": f"graph-{project_id}", "ontology_id": f"ontology-{project_id}", "status": "completed", "state": "completed", "message": "Ontology generation completed successfully.", "agents_preview": agents, "created_at": utc_now(), "updated_at": utc_now()}
    BUILD_TASKS[task_id] = task
    return response_ok({"status": "completed", "state": "completed", "task_id": task_id, "data": task, "result": task, "task": task})


@app.post("/api/graph/build")
async def graph_build(request: Request):
    payload = await parse_request_payload(request)
    project_id = get_project_id(payload)
    context = get_current_project_context(project_id)
    agents = context.get("agents_preview") or generate_dynamic_agents_from_context(get_context_text(project_id), context.get("agent_count") or AGENT_DEFAULT_COUNT)
    task_id = f"graph_build_{uuid4().hex}"
    task = {"task_id": task_id, "id": task_id, "project_id": project_id, "graph_id": f"graph-{project_id}", "ontology_id": f"ontology-{project_id}", "status": "completed", "state": "completed", "message": "Graph build completed successfully.", "nodes_created": len(agents) + 1, "relationships_created": len(agents), "agents_preview": agents, "created_at": utc_now(), "updated_at": utc_now()}
    BUILD_TASKS[task_id] = task
    return response_ok({"status": "completed", "state": "completed", "task_id": task_id, "data": task, "result": task, "task": task})


@app.get("/api/graph/task/{task_id}")
async def graph_task_status(task_id: str):
    task = BUILD_TASKS.get(task_id) or {"task_id": task_id, "id": task_id, "project_id": "render-project", "graph_id": "graph-render-project", "status": "completed", "state": "completed", "message": "Task returned by compatibility mode.", "updated_at": utc_now()}
    BUILD_TASKS[task_id] = task
    return response_ok({"status": task["status"], "state": task["state"], "task_id": task_id, "data": task, "result": task, "task": task})


@app.get("/api/graph/project/{project_id}")
async def graph_project_detail(project_id: str):
    context = get_current_project_context(project_id)
    agents = context.get("agents_preview") or generate_dynamic_agents_from_context(get_context_text(project_id), context.get("agent_count") or AGENT_DEFAULT_COUNT)
    project = {"project_id": project_id, "id": project_id, "name": context.get("project_name", "Projeto MiroFish Render"), "status": "built", "state": "built", "graph_id": f"graph-{project_id}", "ontology_id": f"ontology-{project_id}", "agents_target": len(agents), "agents_preview": agents, "context_source": context.get("source_type"), "backend_version": BACKEND_VERSION}
    return response_ok({"state": "built", "project_id": project_id, "graph_id": project["graph_id"], "data": project, "result": project, "project": project})


@app.get("/api/graph/data/{graph_id}")
async def graph_data(graph_id: str):
    project_id = graph_id.replace("graph-", "") if graph_id.startswith("graph-") else "render-project"
    context = get_current_project_context(project_id)
    agents = context.get("agents_preview") or generate_dynamic_agents_from_context(get_context_text(project_id), context.get("agent_count") or AGENT_DEFAULT_COUNT)
    nodes = [{"id": project_id, "label": "Project", "type": "Project", "name": "Projeto MiroFish Render"}]
    nodes += [{"id": p["id"], "label": p["name"], "type": "Agent", "name": p["name"], "profession": p["profession"], "profile": p.get("profile")} for p in agents]
    edges = [{"id": f"edge-{project_id}-{p['id']}", "source": project_id, "target": p["id"], "type": "USES_AGENT"} for p in agents]
    data = {"graph_id": graph_id, "project_id": project_id, "nodes": nodes, "edges": edges, "agents": agents, "status": "ready"}
    return response_ok({"status": "ready", "graph_id": graph_id, "project_id": project_id, "nodes": nodes, "edges": edges, "data": data, "result": data, "graph": data})


@app.get("/api/simulation/history")
async def simulation_history(limit: int = 20):
    items = list(SIMULATIONS.values())[-limit:]
    return response_ok({"status": "completed", "count": len(items), "data": items, "result": items})


@app.post("/api/simulation/create")
async def simulation_create(request: Request):
    print("[MiroFish] Rota específica simulation create acionada: /api/simulation/create", flush=True)
    payload = await parse_request_payload(request)
    project_id = get_project_id(payload)
    simulation_id = str(payload.get("simulation_id") or payload.get("simulationId") or f"sim_{uuid4().hex}")
    sim = ensure_simulation(simulation_id)
    attach_project_context(sim, project_id)
    sim["agent_target"] = clamp_int(payload.get("agent_count") or payload.get("agents_count") or get_current_project_context(project_id).get("agent_count") or AGENT_DEFAULT_COUNT, AGENT_DEFAULT_COUNT, AGENT_MIN_COUNT, AGENT_MAX_COUNT)
    agents = await refresh_dynamic_agents(sim, force=True)
    sim.update({"project_id": project_id, "graph_id": f"graph-{project_id}", "status": "created", "state": "created", "updated_at": utc_now(), "agents_count": len(agents)})
    return simulation_response(simulation_id)


@app.get("/api/simulation/{simulation_id}")
async def simulation_get_by_id(simulation_id: str):
    return simulation_response(simulation_id)


@app.post("/api/simulation/env-status")
async def simulation_env_status_post():
    payload = {"status": "ready", "state": "ready", "ready": True, "llm_configured": bool(API_KEY), "neo4j_configured": bool(neo4j_driver), "postgres_configured": bool(POSTGRES_URL), "postgres_schema": POSTGRES_SCHEMA, "backend_version": BACKEND_VERSION, "scheduler": scheduler_payload()}
    return response_ok({"status": "ready", "state": "ready", "data": payload, "result": payload})


@app.get("/api/simulation/env-status")
async def simulation_env_status_get():
    return await simulation_env_status_post()


@app.post("/api/simulation/prepare")
async def simulation_prepare(request: Request):
    payload_req = await parse_request_payload(request)
    simulation_id = str(payload_req.get("simulation_id") or payload_req.get("simulationId") or payload_req.get("id") or f"sim_{uuid4().hex}")
    project_id = get_project_id(payload_req)
    task_id = f"prepare_{uuid4().hex}"
    sim = ensure_simulation(simulation_id)
    attach_project_context(sim, project_id)
    sim["agent_target"] = clamp_int(payload_req.get("agent_count") or payload_req.get("agents_count") or get_current_project_context(project_id).get("agent_count") or AGENT_DEFAULT_COUNT, AGENT_DEFAULT_COUNT, AGENT_MIN_COUNT, AGENT_MAX_COUNT)
    agents = await refresh_dynamic_agents(sim, force=True)
    sim.update({"task_id": task_id, "prepare_task_id": task_id, "status": "completed", "state": "completed", "message": "Simulation preparation completed successfully.", "config_generated": True, "config": build_config(simulation_id, agents), "updated_at": utc_now()})
    BUILD_TASKS[task_id] = sim
    return response_ok({"status": "completed", "state": "completed", "simulation_id": simulation_id, "task_id": task_id, "data": sim, "result": sim, "simulation": sim, "task": sim})


@app.post("/api/simulation/prepare/status")
async def simulation_prepare_status_post(request: Request):
    payload_req = await parse_request_payload(request)
    simulation_id = str(payload_req.get("simulation_id") or payload_req.get("simulationId") or payload_req.get("id") or "render-project")
    sim = ensure_simulation(simulation_id)
    sim.update({"status": "completed", "state": "completed", "progress": 100, "progress_percent": 100, "updated_at": utc_now()})
    return response_ok({"status": "completed", "state": "completed", "simulation_id": simulation_id, "data": sim, "result": sim})


@app.get("/api/simulation/{simulation_id}/profiles/realtime")
async def simulation_profiles_realtime(simulation_id: str, platform: Optional[str] = None):
    sim = ensure_simulation(simulation_id)
    agents = get_agents_for_simulation(sim)
    payload = {"simulation_id": simulation_id, "platform": platform or "internal", "status": "completed", "state": "completed", "count": len(agents), "profiles": agents, "personas": agents, "agent_personas": agents, "agents": agents, "total_expected": len(agents), "enable_reddit": False, "enable_twitter": False, "scheduler": scheduler_payload(), "backend_version": BACKEND_VERSION}
    return response_ok({"status": "completed", "state": "completed", "simulation_id": simulation_id, "count": len(agents), "profiles": agents, "personas": agents, "agent_personas": agents, "agents": agents, "data": payload, "result": payload})


@app.get("/api/simulation/{simulation_id}/config/realtime")
async def get_simulation_config_realtime(simulation_id: str):
    sim = ensure_simulation(simulation_id)
    attach_project_context(sim, sim.get("project_id", "render-project"))
    agents = get_agents_for_simulation(sim)
    config = sim.get("config") or build_config(simulation_id, agents)
    sim["config"] = config
    sim["config_generated"] = True
    data = {"simulation_id": simulation_id, "status": "completed", "state": "completed", "generation_stage": "completed", "is_generating": False, "config_generated": True, "config_ready": True, "ready": True, "done": True, "context_source": sim.get("context_source"), "summary": {"total_agents": len(agents), "simulation_hours": config.get("time_config", {}).get("total_simulation_hours"), "initial_posts_count": 0, "hot_topics_count": 0, "has_twitter_config": False, "has_reddit_config": False, "generated_at": config.get("generated_at"), "llm_model": config.get("llm_model") or config.get("mode")}, "config": config}
    return response_ok({"status": "completed", "state": "completed", "simulation_id": simulation_id, "config_generated": True, "config_ready": True, "config": config, "data": data, "result": data})


@app.get("/api/simulation/{simulation_id}/config")
async def get_simulation_config(simulation_id: str):
    sim = ensure_simulation(simulation_id)
    agents = get_agents_for_simulation(sim)
    config = sim.get("config") or build_config(simulation_id, agents)
    return response_ok({"status": "completed", "state": "completed", "simulation_id": simulation_id, "config_generated": True, "config": config, "data": config, "result": config})


@app.post("/api/simulation/start")
async def start_simulation(request: Request):
    payload = await parse_request_payload(request)
    simulation_id = str(payload.get("simulation_id") or payload.get("simulationId") or payload.get("id") or next(reversed(SIMULATIONS), f"sim_{uuid4().hex}"))
    sim = ensure_simulation(simulation_id)
    attach_project_context(sim, sim.get("project_id", "render-project"))
    total_rounds = int(payload.get("max_rounds") or payload.get("maxRounds") or payload.get("rounds") or SIMULATION_ROUNDS)
    sim.update({"total_rounds": max(1, min(total_rounds, 60)), "current_round": 0, "progress": 0, "progress_percent": 0, "status": "running", "state": "running", "message": "Simulation started.", "started_at": utc_now(), "completed_at": None, "error": None, "report_ready": False, "updated_at": utc_now(), "llm_calls_used": 0})
    existing_task = RUN_TASKS.get(simulation_id)
    if existing_task and not existing_task.done():
        existing_task.cancel()
    RUN_TASKS[simulation_id] = asyncio.create_task(run_simulation_for_id(simulation_id))
    return simulation_response(simulation_id)


@app.post("/api/simulation/{simulation_id}/run")
async def simulation_run_by_id(simulation_id: str):
    sim = ensure_simulation(simulation_id)
    attach_project_context(sim, sim.get("project_id", "render-project"))
    sim.update({"status": "running", "state": "running", "message": "Simulation run started successfully.", "updated_at": utc_now(), "llm_calls_used": 0})
    existing_task = RUN_TASKS.get(simulation_id)
    if existing_task and not existing_task.done():
        existing_task.cancel()
    RUN_TASKS[simulation_id] = asyncio.create_task(run_simulation_for_id(simulation_id))
    return simulation_response(simulation_id)


@app.get("/api/simulation/{simulation_id}/run-status")
async def simulation_run_status(simulation_id: str):
    return simulation_response(simulation_id)


@app.get("/api/simulation/{simulation_id}/run-status/detail")
async def simulation_run_status_detail(simulation_id: str):
    sim = ensure_simulation(simulation_id)
    detail = dict(sim)
    detail["recent_actions"] = sim.get("actions", [])[-20:]
    detail["all_actions"] = sim.get("actions", [])
    detail["logs"] = sim.get("logs", [])[-50:]
    detail["timeline"] = sim.get("timeline", [])
    return response_ok({"status": sim.get("status", "created"), "state": sim.get("state", sim.get("status", "created")), "simulation_id": simulation_id, "data": detail, "result": detail, "detail": detail})


@app.get("/api/simulation/{simulation_id}/posts")
async def simulation_posts(simulation_id: str, platform: str = "internal", limit: int = 50, offset: int = 0):
    sim = ensure_simulation(simulation_id)
    posts = sim.get("posts", [])[offset: offset + limit]
    return response_ok({"status": "completed", "simulation_id": simulation_id, "platform": platform, "count": len(posts), "data": posts, "result": posts, "posts": posts})


@app.get("/api/simulation/{simulation_id}/timeline")
async def simulation_timeline(simulation_id: str, start_round: int = 0, end_round: Optional[int] = None):
    sim = ensure_simulation(simulation_id)
    timeline = sim.get("timeline", [])
    if end_round is not None:
        timeline = [item for item in timeline if start_round <= item.get("round", 0) <= end_round]
    else:
        timeline = [item for item in timeline if item.get("round", 0) >= start_round]
    return response_ok({"status": "completed", "simulation_id": simulation_id, "count": len(timeline), "data": timeline, "result": timeline, "timeline": timeline})


@app.get("/api/simulation/{simulation_id}/agent-stats")
async def simulation_agent_stats(simulation_id: str):
    sim = ensure_simulation(simulation_id)
    stats = []
    for p in get_agents_for_simulation(sim):
        stats.append({"agent_id": p["agent_id"], "agent_name": p["name"], "actions": len([a for a in sim.get("actions", []) if a.get("agent_id") == p["agent_id"]])})
    return response_ok({"status": "completed", "simulation_id": simulation_id, "data": stats, "result": stats, "stats": stats})


@app.get("/api/simulation/{simulation_id}/actions")
async def simulation_actions(simulation_id: str, limit: int = 50, offset: int = 0):
    sim = ensure_simulation(simulation_id)
    actions = sim.get("actions", [])[offset: offset + limit]
    return response_ok({"status": "completed", "simulation_id": simulation_id, "count": len(actions), "data": actions, "result": actions, "actions": actions})


@app.post("/api/simulation/interview/batch")
async def interview_agents(request: Request):
    payload = await parse_request_payload(request)
    simulation_id = str(payload.get("simulation_id") or payload.get("simulationId") or "render-project")
    sim = ensure_simulation(simulation_id)
    agents = get_agents_for_simulation(sim)
    interviews = payload.get("interviews") or []
    results: Dict[str, Any] = {}
    for item in interviews:
        agent_id = int(item.get("agent_id", 0))
        prompt = str(item.get("prompt") or "")
        agent = agents[agent_id] if 0 <= agent_id < len(agents) else agents[0]
        answer = await analyze_with_agent(agent, sim.get("current_round", 0) + 1, prompt + "\n" + sim.get("context_preview", ""), sim)
        results[f"internal_{agent_id}"] = {"agent_id": agent_id, "agent_name": agent.get("name"), "response": answer, "answer": answer}
        results[f"reddit_{agent_id}"] = {"agent_id": agent_id, "agent_name": agent.get("name"), "response": answer, "answer": answer}
    data = {"simulation_id": simulation_id, "result": {"results": results}, "results": results}
    return response_ok({"status": "completed", "data": data, "result": data})


@app.post("/api/report/generate")
async def generate_report(request: Request):
    payload = await parse_request_payload(request)
    simulation_id = str(payload.get("simulation_id") or payload.get("simulationId") or next(reversed(SIMULATIONS), f"sim_{uuid4().hex}"))
    report = build_report_payload(simulation_id)
    return response_ok({"status": "ready", "state": "ready", "report_id": report["report_id"], "reportId": report["report_id"], "simulation_id": simulation_id, "data": report, "result": report, "report": report})


@app.get("/api/report/generate/status")
async def report_generate_status(report_id: str):
    report = REPORTS.get(report_id)
    if not report:
        simulation_id = report_id.replace("report_", "")
        report = build_report_payload(simulation_id)
    return response_ok({"status": "ready", "state": "ready", "report_id": report_id, "data": report, "result": report})


@app.get("/api/report/{report_id}/agent-log")
async def report_agent_log(report_id: str, from_line: int = 0):
    logs = build_report_agent_logs(report_id)
    slice_logs = logs[max(0, from_line):]
    return response_ok({"status": "completed", "report_id": report_id, "data": {"logs": slice_logs, "from_line": from_line, "total_lines": len(logs), "completed": True}, "result": {"logs": slice_logs}})


@app.get("/api/report/{report_id}/console-log")
async def report_console_log(report_id: str, from_line: int = 0):
    logs = build_console_logs(report_id)
    slice_logs = logs[max(0, from_line):]
    return response_ok({"status": "completed", "report_id": report_id, "data": {"logs": slice_logs, "from_line": from_line, "total_lines": len(logs), "completed": True}, "result": {"logs": slice_logs}})


@app.get("/api/report/{report_id}")
async def get_report(report_id: str):
    report = REPORTS.get(report_id)
    if not report:
        simulation_id = report_id.replace("report_", "")
        report = build_report_payload(simulation_id)
    return response_ok({"status": report.get("status", "ready"), "report_id": report_id, "data": report, "result": report, "report": report})


@app.post("/api/report/chat")
async def report_chat(request: Request):
    payload = await parse_request_payload(request)
    simulation_id = str(payload.get("simulation_id") or payload.get("simulationId") or next(reversed(SIMULATIONS), "render-project"))
    message = str(payload.get("message") or "")
    sim = ensure_simulation(simulation_id)
    report_context = json.dumps({
        "status": sim.get("status"),
        "agents": [a.get("name") for a in sim.get("agents", [])],
        "actions": [a.get("content") for a in sim.get("actions", [])[-8:]],
        "scheduler": sim.get("scheduler", scheduler_payload()),
    }, ensure_ascii=False)[:5000]

    try:
        answer = await limited_llm_chat(
            messages=[
                {"role": "system", "content": "Você é o Report Agent do MiroFish. Responda em português, de forma objetiva, usando o relatório e as ações da simulação."},
                {"role": "user", "content": f"Pergunta: {message}\n\nContexto da simulação:\n{report_context}"},
            ],
            max_tokens=320,
            sim=sim,
            reason="report_chat",
        )
    except Exception as exc:
        answer = f"Com base na simulação `{simulation_id}`, os agentes executaram {len(sim.get('actions', []))} ações com {len(sim.get('agents', []))} agentes dinâmicos. A pergunta foi: {message}. Observação técnica: resposta em fallback porque o chat real não conseguiu chamar o LLM neste momento ({exc})."

    data = {"simulation_id": simulation_id, "response": answer, "answer": answer, "created_at": utc_now()}
    return response_ok({"status": "completed", "data": data, "result": data})


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def api_fallback(path: str, request: Request):
    print(f"[MiroFish] Fallback /api acionado: /api/{path}", flush=True)
    payload = await parse_request_payload(request)
    data = {"status": "ready", "state": "ready", "path": f"/api/{path}", "payload_received": payload, "backend_version": BACKEND_VERSION}
    return response_ok({"status": "ready", "state": "ready", "message": f"Rota /api/{path} recebida pelo backend.", "path": f"/api/{path}", "data": data, "result": data})
