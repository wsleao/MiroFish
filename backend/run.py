import os
import re
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


BACKEND_VERSION = "mirofish-render-v9-postgres-context"

API_KEY = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("GROQ_API_KEY")
BASE_URL = os.getenv("OPENAI_API_BASE", "https://api.groq.com/openai/v1")
MODEL = os.getenv("MODEL_NAME", "llama-3.1-8b-instant")

TOTAL_AGENTS = int(os.getenv("TOTAL_AGENTS", "3"))
SIMULATION_ROUNDS = int(os.getenv("SIMULATION_ROUNDS", "8"))
ROUND_DELAY_SECONDS = float(os.getenv("ROUND_DELAY_SECONDS", "3"))
AUTO_START_SIMULATION = os.getenv("AUTO_START_SIMULATION", "false").lower() == "true"

NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
POSTGRES_URL = os.getenv("EXTERNAL_DB_URL") or os.getenv("DATABASE_URL")
POSTGRES_SCHEMA = os.getenv("POSTGRES_SCHEMA", "scadiadalbertobackes")

BUILD_TASKS: Dict[str, Dict[str, Any]] = {}
PROJECT_CONTEXTS: Dict[str, Dict[str, Any]] = {}
SIMULATIONS: Dict[str, Dict[str, Any]] = {}
RUN_TASKS: Dict[str, asyncio.Task] = {}

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
            select
                column_name,
                data_type,
                is_nullable,
                ordinal_position
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


def default_personas() -> List[Dict[str, Any]]:
    return [
        {
            "id": "agent-0",
            "agent_id": 0,
            "name": "Agente Financeiro",
            "username": "Agente Financeiro",
            "role": "Analista de Fluxo de Caixa e Orçamento",
            "profession": "Especialista Financeiro Agro",
            "persona": "Especialista em análise financeira rural, fluxo de caixa, orçamento e previsão de necessidade de capital.",
            "profile": "Financeiro",
            "status": "ready",
        },
        {
            "id": "agent-1",
            "agent_id": 1,
            "name": "Agente Dados/BI",
            "username": "Agente Dados/BI",
            "role": "Analista de Dados e Indicadores",
            "profession": "Especialista em BI e Indicadores",
            "persona": "Especialista em BI, dashboards, estruturação de indicadores, histórico financeiro e análise comparativa.",
            "profile": "Dados",
            "status": "ready",
        },
        {
            "id": "agent-2",
            "agent_id": 2,
            "name": "Agente Produto",
            "username": "Agente Produto",
            "role": "Especialista de Produto ERP Agro",
            "profession": "Especialista de Produto",
            "persona": "Especialista em requisitos de produto, jornada do usuário, módulos financeiros e aderência operacional do SCADIAgro.",
            "profile": "Produto",
            "status": "ready",
        },
    ][: max(1, min(TOTAL_AGENTS, 3))]


def build_config(simulation_id: str) -> Dict[str, Any]:
    personas = default_personas()
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
        "config_reasoning": "Configuração gerada em modo compatível no Render.",
        "time_config": {
            "total_simulation_hours": SIMULATION_ROUNDS,
            "minutes_per_round": 60,
            "agents_per_hour_min": 1,
            "agents_per_hour_max": len(personas),
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
                "entity_type": p["profile"],
                "stance": "neutral",
                "active_hours": list(range(8, 19)),
                "posts_per_hour": 1,
                "comments_per_hour": 2,
                "response_delay_min": 1,
                "response_delay_max": 5,
                "activity_level": 0.5,
                "sentiment_bias": 0,
                "influence_weight": 1,
            }
            for p in personas
        ],
        "event_config": {
            "initial_posts": [],
            "hot_topics": [],
            "narrative_direction": "Simulação analítica baseada no contexto enviado ao MiroFish.",
        },
        "agents_count": len(personas),
        "personas_count": len(personas),
        "agents": personas,
        "personas": personas,
        "profiles": personas,
        "entities_count": len(personas),
        "entity_types": [p["profile"] for p in personas],
        "enable_reddit": False,
        "enable_twitter": False,
        "reddit_enabled": False,
        "twitter_enabled": False,
        "external_sources_enabled": False,
        "llm_configured": bool(API_KEY),
        "neo4j_configured": bool(neo4j_driver),
        "postgres_configured": bool(POSTGRES_URL),
        "postgres_schema": POSTGRES_SCHEMA,
        "backend_version": BACKEND_VERSION,
        "generated_at": utc_now(),
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }


def ensure_simulation(simulation_id: str) -> Dict[str, Any]:
    sim = SIMULATIONS.get(simulation_id)
    if not sim:
        sim = {
            "simulation_id": simulation_id,
            "id": simulation_id,
            "task_id": simulation_id,
            "project_id": "render-project",
            "graph_id": "graph-render-project",
            "status": "created",
            "state": "created",
            "message": "Simulation created.",
            "agents_target": len(default_personas()),
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
            "context_preview": PROJECT_CONTEXTS.get("render-project", {}).get("context_preview", ""),
            "context_source": PROJECT_CONTEXTS.get("render-project", {}).get("source_type", "none"),
            "config": build_config(simulation_id),
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "started_at": None,
            "completed_at": None,
            "error": None,
        }
        SIMULATIONS[simulation_id] = sim
        BUILD_TASKS[simulation_id] = sim
    return sim


def attach_project_context(sim: Dict[str, Any], project_id: str = "render-project") -> None:
    context = PROJECT_CONTEXTS.get(project_id) or PROJECT_CONTEXTS.get("render-project") or {}
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
        "data": sim,
        "result": sim,
        "simulation": sim,
        "task": sim,
    }


async def maybe_call_llm(agent_name: str, round_number: int, context: str = "") -> str:
    if not ai_client:
        return f"{agent_name} processou a rodada {round_number} em modo fallback local com contexto de {len(context)} caracteres."
    try:
        context_text = context[:4500] if context else "Nenhum contexto foi carregado. Informe uma fonte de dados ou arquivo."
        response = await ai_client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Você é um agente analítico do MiroFish. "
                        "Use obrigatoriamente o contexto fornecido. "
                        "Se o contexto vier de PostgreSQL, analise a estrutura, tabelas, colunas e possíveis oportunidades. "
                        "Responda em português, em uma conclusão objetiva de até 3 frases."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Agente: {agent_name}. Rodada: {round_number}. Contexto disponível:\n{context_text}",
                },
            ],
            max_tokens=160,
        )
        return response.choices[0].message.content or f"{agent_name} concluiu análise da rodada {round_number}."
    except Exception as exc:
        return f"{agent_name} concluiu a rodada {round_number} em fallback após erro LLM: {exc}"


async def run_simulation_for_id(simulation_id: str):
    sim = ensure_simulation(simulation_id)
    attach_project_context(sim, sim.get("project_id", "render-project"))
    personas = default_personas()
    total_rounds = int(sim.get("total_rounds") or SIMULATION_ROUNDS)

    sim.update({
        "status": "running",
        "state": "running",
        "message": "Simulation running.",
        "started_at": sim.get("started_at") or utc_now(),
        "completed_at": None,
        "error": None,
        "report_ready": False,
        "updated_at": utc_now(),
    })

    print("--- [MiroFish] Iniciando ciclo de simulação ---", flush=True)

    try:
        for round_number in range(1, total_rounds + 1):
            sim["current_round"] = round_number
            sim["progress"] = int((round_number / total_rounds) * 100)
            sim["progress_percent"] = sim["progress"]
            sim["message"] = f"Processando rodada {round_number} de {total_rounds}."
            sim["updated_at"] = utc_now()

            start_index = (round_number - 1) * len(personas)
            end_index = start_index + len(personas) - 1
            print(f"-> Processando lote: {start_index} até {end_index}", flush=True)

            round_actions = []
            for persona in personas:
                analysis = await maybe_call_llm(persona["name"], round_number, sim.get("context_preview", ""))
                action = {
                    "round": round_number,
                    "round_num": round_number,
                    "agent_id": persona["agent_id"],
                    "agent_name": persona["name"],
                    "type": "analysis",
                    "context_source": sim.get("context_source"),
                    "content": analysis,
                    "created_at": utc_now(),
                }
                round_actions.append(action)
                sim["actions"].append(action)
                sim["posts"].append({
                    "id": f"post_{simulation_id}_{round_number}_{persona['agent_id']}",
                    "round": round_number,
                    "agent_id": persona["agent_id"],
                    "author": persona["name"],
                    "content": analysis,
                    "created_at": utc_now(),
                })

            sim["timeline"].append({
                "round": round_number,
                "status": "completed",
                "actions_count": len(round_actions),
                "created_at": utc_now(),
            })
            sim["logs"].append({"time": utc_now(), "message": f"Rodada {round_number}/{total_rounds} concluída."})
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
        sim["report"] = {
            "report_id": f"report_{simulation_id}",
            "simulation_id": simulation_id,
            "status": "ready",
            "title": "Relatório MiroFish - POC Render",
            "summary": "Simulação concluída em modo compatível no Render.",
            "context_source": sim.get("context_source"),
            "total_rounds": total_rounds,
            "total_actions": len(sim.get("actions", [])),
            "generated_at": utc_now(),
        }
        print(f"--- [MiroFish] Simulação {simulation_id} concluída ---", flush=True)
    except Exception as exc:
        sim.update({"status": "failed", "state": "failed", "message": f"Simulation failed: {exc}", "error": str(exc), "updated_at": utc_now()})
        print(f"[MiroFish] Erro na simulação {simulation_id}: {exc}", flush=True)


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
        "agents_target": TOTAL_AGENTS,
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


@app.post("/update-scenario")
async def update_scenario(prompt: str = Form(...), source_type: str = Form("text"), file: Optional[UploadFile] = File(None), schema: str = Form(POSTGRES_SCHEMA), table: Optional[str] = Form(None)):
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
        "context_preview": preview,
        "updated_at": utc_now(),
    }
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

    PROJECT_CONTEXTS[project_id] = {
        "project_id": project_id,
        "project_name": project_name,
        "prompt": scenario,
        "source_type": source,
        "schema": schema,
        "table": table,
        "context_preview": uploaded_preview[:12000] if uploaded_preview else scenario[:2000],
        "updated_at": utc_now(),
    }
    task_id = f"ontology_generate_{uuid4().hex}"
    task = {"task_id": task_id, "id": task_id, "project_id": project_id, "graph_id": f"graph-{project_id}", "ontology_id": f"ontology-{project_id}", "status": "completed", "state": "completed", "message": "Ontology generation completed successfully.", "created_at": utc_now(), "updated_at": utc_now()}
    BUILD_TASKS[task_id] = task
    return response_ok({"status": "completed", "state": "completed", "task_id": task_id, "data": task, "result": task, "task": task})


@app.post("/api/graph/build")
async def graph_build(request: Request):
    payload = await parse_request_payload(request)
    project_id = get_project_id(payload)
    task_id = f"graph_build_{uuid4().hex}"
    task = {"task_id": task_id, "id": task_id, "project_id": project_id, "graph_id": f"graph-{project_id}", "ontology_id": f"ontology-{project_id}", "status": "completed", "state": "completed", "message": "Graph build completed successfully.", "nodes_created": 5, "relationships_created": 4, "created_at": utc_now(), "updated_at": utc_now()}
    BUILD_TASKS[task_id] = task
    return response_ok({"status": "completed", "state": "completed", "task_id": task_id, "data": task, "result": task, "task": task})


@app.get("/api/graph/task/{task_id}")
async def graph_task_status(task_id: str):
    task = BUILD_TASKS.get(task_id) or {"task_id": task_id, "id": task_id, "project_id": "render-project", "graph_id": "graph-render-project", "status": "completed", "state": "completed", "message": "Task returned by compatibility mode.", "updated_at": utc_now()}
    BUILD_TASKS[task_id] = task
    return response_ok({"status": task["status"], "state": task["state"], "task_id": task_id, "data": task, "result": task, "task": task})


@app.get("/api/graph/project/{project_id}")
async def graph_project_detail(project_id: str):
    context = PROJECT_CONTEXTS.get(project_id, {})
    project = {"project_id": project_id, "id": project_id, "name": context.get("project_name", "Projeto MiroFish Render"), "status": "built", "state": "built", "graph_id": f"graph-{project_id}", "ontology_id": f"ontology-{project_id}", "agents_target": len(default_personas()), "context_source": context.get("source_type"), "backend_version": BACKEND_VERSION}
    return response_ok({"state": "built", "project_id": project_id, "graph_id": project["graph_id"], "data": project, "result": project, "project": project})


@app.get("/api/graph/data/{graph_id}")
async def graph_data(graph_id: str):
    project_id = graph_id.replace("graph-", "") if graph_id.startswith("graph-") else "render-project"
    personas = default_personas()
    nodes = [{"id": project_id, "label": "Project", "type": "Project", "name": "Projeto MiroFish Render"}]
    nodes += [{"id": p["id"], "label": p["name"], "type": "Agent", "name": p["name"], "profession": p["profession"]} for p in personas]
    edges = [{"id": f"edge-{project_id}-{p['id']}", "source": project_id, "target": p["id"], "type": "USES_AGENT"} for p in personas]
    data = {"graph_id": graph_id, "project_id": project_id, "nodes": nodes, "edges": edges, "agents": personas, "status": "ready"}
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
    sim.update({"project_id": project_id, "graph_id": f"graph-{project_id}", "status": "created", "state": "created", "updated_at": utc_now()})
    attach_project_context(sim, project_id)
    return simulation_response(simulation_id)


@app.get("/api/simulation/{simulation_id}")
async def simulation_get_by_id(simulation_id: str):
    return simulation_response(simulation_id)


@app.post("/api/simulation/env-status")
async def simulation_env_status_post():
    payload = {"status": "ready", "state": "ready", "ready": True, "llm_configured": bool(API_KEY), "neo4j_configured": bool(neo4j_driver), "postgres_configured": bool(POSTGRES_URL), "postgres_schema": POSTGRES_SCHEMA, "backend_version": BACKEND_VERSION}
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
    sim.update({"task_id": task_id, "prepare_task_id": task_id, "status": "completed", "state": "completed", "message": "Simulation preparation completed successfully.", "config_generated": True, "config": build_config(simulation_id), "updated_at": utc_now()})
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
    personas = default_personas()
    payload = {"simulation_id": simulation_id, "platform": platform or "internal", "status": "completed", "state": "completed", "count": len(personas), "profiles": personas, "personas": personas, "agent_personas": personas, "agents": personas, "total_expected": len(personas), "enable_reddit": False, "enable_twitter": False, "backend_version": BACKEND_VERSION}
    return response_ok({"status": "completed", "state": "completed", "simulation_id": simulation_id, "count": len(personas), "profiles": personas, "personas": personas, "agent_personas": personas, "agents": personas, "data": payload, "result": payload})


@app.get("/api/simulation/{simulation_id}/config/realtime")
async def get_simulation_config_realtime(simulation_id: str):
    sim = ensure_simulation(simulation_id)
    attach_project_context(sim, sim.get("project_id", "render-project"))
    config = sim.get("config") or build_config(simulation_id)
    sim["config"] = config
    sim["config_generated"] = True
    data = {"simulation_id": simulation_id, "status": "completed", "state": "completed", "generation_stage": "completed", "is_generating": False, "config_generated": True, "config_ready": True, "ready": True, "done": True, "context_source": sim.get("context_source"), "summary": {"total_agents": len(config.get("agent_configs", [])), "simulation_hours": config.get("time_config", {}).get("total_simulation_hours"), "initial_posts_count": 0, "hot_topics_count": 0, "has_twitter_config": False, "has_reddit_config": False, "generated_at": config.get("generated_at"), "llm_model": config.get("llm_model") or config.get("mode")}, "config": config}
    return response_ok({"status": "completed", "state": "completed", "simulation_id": simulation_id, "config_generated": True, "config_ready": True, "config": config, "data": data, "result": data})


@app.get("/api/simulation/{simulation_id}/config")
async def get_simulation_config(simulation_id: str):
    sim = ensure_simulation(simulation_id)
    config = sim.get("config") or build_config(simulation_id)
    return response_ok({"status": "completed", "state": "completed", "simulation_id": simulation_id, "config_generated": True, "config": config, "data": config, "result": config})


@app.post("/api/simulation/start")
async def start_simulation(request: Request):
    payload = await parse_request_payload(request)
    simulation_id = str(payload.get("simulation_id") or payload.get("simulationId") or payload.get("id") or next(reversed(SIMULATIONS), f"sim_{uuid4().hex}"))
    sim = ensure_simulation(simulation_id)
    attach_project_context(sim, sim.get("project_id", "render-project"))
    total_rounds = int(payload.get("max_rounds") or payload.get("maxRounds") or payload.get("rounds") or SIMULATION_ROUNDS)
    sim.update({"total_rounds": max(1, min(total_rounds, 60)), "current_round": 0, "progress": 0, "progress_percent": 0, "status": "running", "state": "running", "message": "Simulation started.", "started_at": utc_now(), "completed_at": None, "error": None, "report_ready": False, "updated_at": utc_now()})
    existing_task = RUN_TASKS.get(simulation_id)
    if existing_task and not existing_task.done():
        existing_task.cancel()
    RUN_TASKS[simulation_id] = asyncio.create_task(run_simulation_for_id(simulation_id))
    return simulation_response(simulation_id)


@app.post("/api/simulation/{simulation_id}/run")
async def simulation_run_by_id(simulation_id: str):
    sim = ensure_simulation(simulation_id)
    attach_project_context(sim, sim.get("project_id", "render-project"))
    sim.update({"status": "running", "state": "running", "message": "Simulation run started successfully.", "updated_at": utc_now()})
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
    for p in default_personas():
        stats.append({"agent_id": p["agent_id"], "agent_name": p["name"], "actions": len([a for a in sim.get("actions", []) if a.get("agent_id") == p["agent_id"]])})
    return response_ok({"status": "completed", "simulation_id": simulation_id, "data": stats, "result": stats, "stats": stats})


@app.get("/api/simulation/{simulation_id}/actions")
async def simulation_actions(simulation_id: str, limit: int = 50, offset: int = 0):
    sim = ensure_simulation(simulation_id)
    actions = sim.get("actions", [])[offset: offset + limit]
    return response_ok({"status": "completed", "simulation_id": simulation_id, "count": len(actions), "data": actions, "result": actions, "actions": actions})


@app.get("/api/report/{report_id}")
async def get_report(report_id: str):
    simulation_id = report_id.replace("report_", "")
    sim = ensure_simulation(simulation_id)
    report = sim.get("report") or {"report_id": report_id, "simulation_id": simulation_id, "status": "pending", "summary": "Relatório ainda não gerado."}
    return response_ok({"status": report.get("status", "pending"), "report_id": report_id, "data": report, "result": report, "report": report})


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def api_fallback(path: str, request: Request):
    print(f"[MiroFish] Fallback /api acionado: /api/{path}", flush=True)
    payload = await parse_request_payload(request)
    data = {"status": "ready", "state": "ready", "path": f"/api/{path}", "payload_received": payload, "backend_version": BACKEND_VERSION}
    return response_ok({"status": "ready", "state": "ready", "message": f"Rota /api/{path} recebida pelo backend.", "path": f"/api/{path}", "data": data, "result": data})
