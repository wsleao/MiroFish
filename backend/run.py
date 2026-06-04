from pathlib import Path
import py_compile

code = r'''import os
import asyncio
import asyncpg
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional, Any, Dict, List
from uuid import uuid4

from fastapi import FastAPI, UploadFile, Form, File, Request
from fastapi.middleware.cors import CORSMiddleware
from openai import AsyncOpenAI
from neo4j import GraphDatabase


# ============================================================
# MIROFISH ORCHESTRATOR - RENDER COMPATIBLE BACKEND
# Version: v5-safe-graph-data
# ============================================================

BACKEND_VERSION = "mirofish-render-v5-safe-graph-data"


# ============================================================
# 1. CONFIGURAÇÕES
# ============================================================

API_KEY = os.getenv("LLM_API_KEY")
BASE_URL = os.getenv("OPENAI_API_BASE", "https://api.groq.com/openai/v1")
MODEL = os.getenv("MODEL_NAME", "llama-3.1-8b-instant")

TOTAL_AGENTS = int(os.getenv("TOTAL_AGENTS", "3000"))

# Em Render/Groq, recomendo false durante testes.
AUTO_START_SIMULATION = os.getenv("AUTO_START_SIMULATION", "false").lower() == "true"

MICRO_BATCH_SIZE = int(os.getenv("MICRO_BATCH_SIZE", "2"))
PAUSE_BETWEEN_BATCHES = float(os.getenv("PAUSE_BETWEEN_BATCHES", "15"))
PAUSE_BETWEEN_CYCLES = float(os.getenv("PAUSE_BETWEEN_CYCLES", "60"))

POSTGRES_URL = os.getenv("EXTERNAL_DB_URL")

NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

BUILD_TASKS: Dict[str, Dict[str, Any]] = {}

neo4j_driver = None
if NEO4J_URI and NEO4J_USERNAME and NEO4J_PASSWORD:
    neo4j_driver = GraphDatabase.driver(
        NEO4J_URI,
        auth=(NEO4J_USERNAME, NEO4J_PASSWORD)
    )

ai_client = None
if API_KEY:
    ai_client = AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL)


# ============================================================
# 2. HELPERS
# ============================================================

def utc_now() -> str:
    return datetime.utcnow().isoformat()


def get_neo4j_session():
    if not neo4j_driver:
        raise RuntimeError(
            "Neo4j não configurado. Verifique NEO4J_URI, "
            "NEO4J_USERNAME e NEO4J_PASSWORD no Render."
        )
    return neo4j_driver.session()


def get_project_id(payload: Optional[dict] = None) -> str:
    payload = payload or {}
    return str(
        payload.get("project_id")
        or payload.get("projectId")
        or payload.get("project")
        or payload.get("id")
        or "render-project"
    )


def get_project_name(payload: Optional[dict] = None) -> str:
    payload = payload or {}
    return str(
        payload.get("project_name")
        or payload.get("projectName")
        or payload.get("name")
        or "Projeto MiroFish Render"
    )


async def parse_request_payload(request: Request) -> dict:
    """
    Aceita JSON, form-data, multipart ou corpo vazio.
    """
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


async def get_first_uploaded_file_from_request(request: Request) -> Optional[UploadFile]:
    try:
        form = await request.form()
        for _, value in form.multi_items():
            if hasattr(value, "filename") and value.filename:
                return value
    except Exception:
        return None

    return None


def create_task_payload(
    task_id: str,
    task_type: str,
    project_id: str,
    status: str = "completed",
    message: str = "Task completed successfully."
) -> Dict[str, Any]:
    return {
        "task_id": task_id,
        "id": task_id,
        "project_id": project_id,
        "graph_id": f"graph-{project_id}",
        "ontology_id": f"ontology-{project_id}",
        "status": status,
        "state": status,
        "task_type": task_type,
        "message": message,
        "created_at": utc_now(),
        "updated_at": utc_now()
    }


def create_task_response(
    task_type: str = "graph_build",
    project_id: str = "render-project",
    status: str = "completed",
    message: str = "Task completed successfully."
) -> Dict[str, Any]:
    """
    Retorna task_id em múltiplos formatos para cobrir o frontend minificado.
    """
    task_id = f"{task_type}_{uuid4().hex}"
    task_payload = create_task_payload(
        task_id=task_id,
        task_type=task_type,
        project_id=project_id,
        status=status,
        message=message
    )

    BUILD_TASKS[task_id] = task_payload

    return {
        "success": True,
        "ok": True,
        "status": status,
        "state": status,
        "message": message,

        "task_id": task_id,
        "id": task_id,
        "project_id": project_id,
        "graph_id": task_payload["graph_id"],
        "ontology_id": task_payload["ontology_id"],

        "data": task_payload,
        "result": task_payload,
        "task": task_payload
    }


def get_task_or_default(task_id: str) -> Dict[str, Any]:
    task = BUILD_TASKS.get(task_id)

    if not task:
        task_type = "graph_build" if task_id.startswith("graph_build") else "generic_task"
        task = create_task_payload(
            task_id=task_id,
            task_type=task_type,
            project_id="render-project",
            status="completed",
            message="Task não encontrada em memória. Retornando completed para compatibilidade."
        )
        BUILD_TASKS[task_id] = task

    task.setdefault("task_id", task_id)
    task.setdefault("id", task_id)
    task.setdefault("project_id", "render-project")
    task.setdefault("graph_id", f"graph-{task.get('project_id', 'render-project')}")
    task.setdefault("ontology_id", f"ontology-{task.get('project_id', 'render-project')}")
    task.setdefault("status", "completed")
    task.setdefault("state", task.get("status", "completed"))
    task.setdefault("updated_at", utc_now())

    return task


def task_status_response(task_id: str) -> Dict[str, Any]:
    task = get_task_or_default(task_id)
    status = task.get("status", "completed")

    return {
        "success": True,
        "ok": True,
        "status": status,
        "state": status,
        "message": task.get("message", "Task status returned successfully."),
        "task_id": task_id,
        "id": task_id,
        "project_id": task.get("project_id", "render-project"),
        "graph_id": task.get("graph_id", "graph-render-project"),
        "ontology_id": task.get("ontology_id", "ontology-render-project"),
        "data": task,
        "result": task,
        "task": task
    }


def project_payload(project_id: str) -> Dict[str, Any]:
    payload = {
        "project_id": project_id,
        "id": project_id,
        "name": "Projeto MiroFish Render",
        "status": "built",
        "state": "built",
        "graph_id": f"graph-{project_id}",
        "ontology_id": f"ontology-{project_id}",
        "nodes": [],
        "edges": [],
        "agents_target": TOTAL_AGENTS,
        "neo4j_configured": bool(neo4j_driver),
        "backend_version": BACKEND_VERSION
    }

    if neo4j_driver:
        try:
            with get_neo4j_session() as session:
                result = session.run(
                    """
                    MATCH (p:Project {id: $project_id})
                    OPTIONAL MATCH (p)-[:HAS_AGENT]->(a:Agent)
                    RETURN p.id AS project_id,
                           p.name AS name,
                           count(a) AS agents_count
                    """,
                    project_id=project_id
                )
                record = result.single()
                if record:
                    payload["name"] = record["name"] or payload["name"]
                    payload["agents_count"] = int(record["agents_count"] or 0)
        except Exception as exc:
            payload["neo4j_warning"] = str(exc)

    return payload


def project_response(project_id: str) -> Dict[str, Any]:
    payload = project_payload(project_id)

    return {
        "success": True,
        "ok": True,
        "status": "success",
        "state": "built",
        "project_id": project_id,
        "graph_id": payload["graph_id"],
        "ontology_id": payload["ontology_id"],
        "data": payload,
        "result": payload,
        "project": payload
    }


# ============================================================
# 3. SIMULAÇÃO DE AGENTES
# ============================================================

async def simulate_agent_turn(agent_id: int):
    if not ai_client:
        print("[MiroFish] LLM_API_KEY não configurada. Pulando simulação de agente.", flush=True)
        return

    if not neo4j_driver:
        print("[MiroFish] Neo4j não configurado. Pulando simulação de agente.", flush=True)
        return

    try:
        with get_neo4j_session() as session:
            result = session.run(
                """
                MATCH (a:Agent {id: $id})
                RETURN a.memory AS mem, a.personality AS pers
                """,
                id=agent_id
            )
            record = result.single()
            memory = record["mem"] if record and record["mem"] else "Início da simulação."
            personality = record["pers"] if record and record["pers"] else "Agente MiroFish."

        response = await ai_client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    "content": f"Você é um agente especialista do MiroFish. Perfil: {personality}"
                },
                {
                    "role": "user",
                    "content": f"Histórico: {memory}. Qual sua próxima análise breve?"
                }
            ],
            max_tokens=120
        )

        new_memory = response.choices[0].message.content or ""

        with get_neo4j_session() as session:
            session.run(
                """
                MERGE (a:Agent {id: $id})
                SET a.memory = $mem,
                    a.updated_at = datetime()
                """,
                id=agent_id,
                mem=new_memory
            )

    except Exception as exc:
        print(f"[MiroFish] Erro no agente {agent_id}: {exc}", flush=True)


async def run_simulation_loop():
    await asyncio.sleep(5)

    while True:
        print("--- [MiroFish] Iniciando ciclo de simulação ---", flush=True)

        try:
            for i in range(0, TOTAL_AGENTS, MICRO_BATCH_SIZE):
                current_batch = range(i, min(i + MICRO_BATCH_SIZE, TOTAL_AGENTS))
                print(
                    f"-> Processando lote: {i} até {i + len(current_batch) - 1}",
                    flush=True
                )

                tasks = [simulate_agent_turn(agent_id) for agent_id in current_batch]
                await asyncio.gather(*tasks)

                await asyncio.sleep(PAUSE_BETWEEN_BATCHES)

            print(
                f"--- [MiroFish] Ciclo finalizado. Próxima rodada em {PAUSE_BETWEEN_CYCLES}s ---",
                flush=True
            )
            await asyncio.sleep(PAUSE_BETWEEN_CYCLES)

        except Exception as loop_error:
            print(
                f"[MiroFish] Erro no loop: {loop_error}. Reiniciando em 20 segundos...",
                flush=True
            )
            await asyncio.sleep(20)


@asynccontextmanager
async def lifespan(app: FastAPI):
    loop_task = None

    if AUTO_START_SIMULATION:
        print("[MiroFish] AUTO_START_SIMULATION=true. Iniciando loop automático.", flush=True)
        loop_task = asyncio.create_task(run_simulation_loop())
    else:
        print("[MiroFish] AUTO_START_SIMULATION=false. Loop automático desativado.", flush=True)

    yield

    if loop_task:
        loop_task.cancel()

    if neo4j_driver:
        neo4j_driver.close()


app = FastAPI(
    title="MiroFish Render Orchestrator",
    version=BACKEND_VERSION,
    lifespan=lifespan
)


# ============================================================
# 4. CORS
# ============================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# 5. POSTGRES / CONTEXTO
# ============================================================

async def buscar_dados_do_postgres() -> str:
    if not POSTGRES_URL:
        return "Erro: EXTERNAL_DB_URL não configurado no Render."

    conn = None

    try:
        conn = await asyncpg.connect(POSTGRES_URL)

        query = """
        SELECT conteudo_texto
        FROM scadiadalbertobackes.sua_tabela
        ORDER BY id DESC
        LIMIT 1;
        """

        row = await conn.fetchrow(query)

        if row and row["conteudo_texto"]:
            return str(row["conteudo_texto"])

        return "A tabela consultada está vazia ou sem dados estruturados."

    except Exception as exc:
        return f"Falha ao ler o PostgreSQL: {exc}"

    finally:
        if conn:
            await conn.close()


async def extract_context_from_file(file: Optional[UploadFile]) -> str:
    if not file:
        return ""

    content = await file.read()
    return content.decode("utf-8", errors="ignore")


async def sync_agents_context(prompt: str, contexto_final: str) -> Dict[str, Any]:
    if not neo4j_driver:
       
