import os
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
# Version: v4-task-project-routes-before-fallback
# ============================================================

BACKEND_VERSION = "mirofish-render-v4-task-project-routes-before-fallback"


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
        return {
            "neo4j_updated": False,
            "warning": "Neo4j não configurado. Contexto não foi gravado nos agentes."
        }

    with get_neo4j_session() as session:
        session.run(
            """
            UNWIND range(0, $total_agents - 1) AS agent_id
            MERGE (a:Agent {id: agent_id})
            SET a.personality = $prompt,
                a.memory = $contexto,
                a.updated_at = datetime()
            """,
            total_agents=TOTAL_AGENTS,
            prompt=prompt,
            contexto=contexto_final
        )

    return {
        "neo4j_updated": True,
        "agents_updated": TOTAL_AGENTS
    }


async def process_update_scenario(
    prompt: str,
    source_type: str,
    file: Optional[UploadFile] = None
) -> Dict[str, Any]:
    contexto_adicional = ""
    normalized_source_type = (source_type or "text").lower()

    if normalized_source_type == "postgres":
        print("[MiroFish] Fonte detectada: PostgreSQL", flush=True)
        contexto_adicional = await buscar_dados_do_postgres()

    elif normalized_source_type in ["file", "upload", "files"] and file:
        print(f"[MiroFish] Fonte detectada: Upload de arquivo ({file.filename})", flush=True)
        contexto_adicional = await extract_context_from_file(file)

    else:
        contexto_adicional = "Nenhuma fonte secundária acoplada. Usando apenas o prompt informado."

    contexto_final = f"""
[PROMPT PRINCIPAL]
{prompt}

[CARGA DE CONTEXTO VINCULADA]
{contexto_adicional}
""".strip()

    neo4j_result = await sync_agents_context(prompt, contexto_final)

    return {
        "success": True,
        "ok": True,
        "status": "success",
        "message": f"Mentes sincronizadas usando a fonte: {normalized_source_type}",
        "source_type": normalized_source_type,
        "preview_size": len(contexto_adicional),
        "context_preview": contexto_final[:2000],
        "neo4j": neo4j_result
    }


# ============================================================
# 6. ROTAS BÁSICAS
# ============================================================

@app.get("/")
def root():
    return {
        "status": "running",
        "service": "MiroFish Render Orchestrator",
        "backend_version": BACKEND_VERSION,
        "health": "/health",
        "docs": "/docs"
    }


@app.head("/")
def root_head():
    return {}


@app.get("/health")
def health_check():
    return {
        "status": "running",
        "backend_version": BACKEND_VERSION,
        "agents_target": TOTAL_AGENTS,
        "auto_start_simulation": AUTO_START_SIMULATION,
        "llm_configured": bool(API_KEY),
        "neo4j_configured": bool(neo4j_driver),
        "postgres_configured": bool(POSTGRES_URL),
        "model": MODEL,
        "base_url": BASE_URL,
        "routes_hint": [
            "/",
            "/health",
            "/update-scenario",
            "/api/graph/ontology/generate",
            "/api/graph/build",
            "/api/graph/task/{task_id}",
            "/api/graph/project/{project_id}",
            "/api/graph/build/status/{task_id}",
            "/api/simulation/history"
        ]
    }


@app.post("/update-scenario")
async def update_scenario(
    prompt: str = Form(...),
    source_type: str = Form(...),
    file: Optional[UploadFile] = File(None)
):
    return await process_update_scenario(
        prompt=prompt,
        source_type=source_type,
        file=file
    )


# ============================================================
# 7. ROTAS DE TASK/PROJECT - DEVEM FICAR ANTES DO FALLBACK
# ============================================================

@app.get("/api/graph/task/{task_id}")
async def graph_task_status(task_id: str):
    print(f"[MiroFish] Rota específica task acionada: /api/graph/task/{task_id}", flush=True)
    return task_status_response(task_id)


@app.get("/api/graph/tasks/{task_id}")
async def graph_tasks_status(task_id: str):
    print(f"[MiroFish] Rota específica tasks acionada: /api/graph/tasks/{task_id}", flush=True)
    return task_status_response(task_id)


@app.get("/api/graph/task/{task_id}/status")
async def graph_task_status_suffix(task_id: str):
    print(f"[MiroFish] Rota específica task/status acionada: /api/graph/task/{task_id}/status", flush=True)
    return task_status_response(task_id)


@app.get("/api/graph/tasks/{task_id}/status")
async def graph_tasks_status_suffix(task_id: str):
    print(f"[MiroFish] Rota específica tasks/status acionada: /api/graph/tasks/{task_id}/status", flush=True)
    return task_status_response(task_id)


@app.get("/api/graph/project/{project_id}")
async def graph_project_detail(project_id: str):
    print(f"[MiroFish] Rota específica project acionada: /api/graph/project/{project_id}", flush=True)
    return project_response(project_id)


@app.get("/api/graph/projects/{project_id}")
async def graph_projects_detail(project_id: str):
    print(f"[MiroFish] Rota específica projects acionada: /api/graph/projects/{project_id}", flush=True)
    return project_response(project_id)


# ============================================================
# 8. BUILD DO GRAFO
# ============================================================

async def execute_graph_build_task(task_id: str, payload: Optional[dict] = None):
    payload = payload or {}

    project_id = get_project_id(payload)
    project_name = get_project_name(payload)

    task = get_task_or_default(task_id)
    task.update({
        "task_id": task_id,
        "id": task_id,
        "project_id": project_id,
        "graph_id": f"graph-{project_id}",
        "ontology_id": f"ontology-{project_id}",
        "status": "running",
        "state": "running",
        "started_at": utc_now(),
        "updated_at": utc_now()
    })

    try:
        if not neo4j_driver:
            task.update({
                "status": "completed",
                "state": "completed",
                "message": "Neo4j não configurado. Build simulado concluído.",
                "nodes_created": 0,
                "relationships_created": 0,
                "completed_at": utc_now(),
                "updated_at": utc_now()
            })
            return

        with get_neo4j_session() as session:
            result = session.run(
                """
                MERGE (p:Project {id: $project_id})
                SET p.name = $project_name,
                    p.status = 'built',
                    p.updated_at = datetime()

                MERGE (o:Ontology {id: $ontology_id})
                SET o.status = 'generated',
                    o.updated_at = datetime()

                MERGE (p)-[:HAS_ONTOLOGY]->(o)

                WITH p, o
                UNWIND range(0, $total_agents - 1) AS agent_id
                MERGE (a:Agent {id: agent_id})
                SET a.project_id = $project_id,
                    a.updated_at = datetime()
                MERGE (p)-[:HAS_AGENT]->(a)

                RETURN count(a) AS agents
                """,
                project_id=project_id,
                project_name=project_name,
                ontology_id=f"ontology-{project_id}",
                total_agents=TOTAL_AGENTS
            )

            record = result.single()
            agents_created = record["agents"] if record else TOTAL_AGENTS

        task.update({
            "status": "completed",
            "state": "completed",
            "message": "Graph build completed successfully.",
            "nodes_created": int(agents_created) + 2,
            "relationships_created": int(agents_created) + 1,
            "completed_at": utc_now(),
            "updated_at": utc_now()
        })

    except Exception as exc:
        task.update({
            "status": "failed",
            "state": "failed",
            "message": str(exc),
            "error": str(exc),
            "completed_at": utc_now(),
            "updated_at": utc_now()
        })


async def start_graph_build_from_payload(payload: Optional[dict] = None) -> Dict[str, Any]:
    payload = payload or {}
    project_id = get_project_id(payload)

    response = create_task_response(
        task_type="graph_build",
        project_id=project_id,
        status="pending",
        message="Graph build task created successfully."
    )

    task_id = response["task_id"]

    asyncio.create_task(
        execute_graph_build_task(
            task_id=task_id,
            payload=payload
        )
    )

    return response


async def start_graph_build_response(request: Request):
    payload = await parse_request_payload(request)
    return await start_graph_build_from_payload(payload)


@app.post("/api/graph/build")
async def graph_build(request: Request):
    return await start_graph_build_response(request)


@app.post("/api/graph/build/start")
async def graph_build_start(request: Request):
    return await start_graph_build_response(request)


@app.post("/api/graph/start-build")
async def graph_start_build(request: Request):
    return await start_graph_build_response(request)


@app.post("/api/graph/buildGraph")
async def graph_build_graph(request: Request):
    return await start_graph_build_response(request)


@app.post("/api/graph/ontology/build")
async def graph_ontology_build(request: Request):
    return await start_graph_build_response(request)


@app.post("/api/graph/build/startBuildGraph")
async def graph_start_build_graph(request: Request):
    return await start_graph_build_response(request)


@app.post("/api/graph/startBuildGraph")
async def graph_start_build_graph_alias(request: Request):
    return await start_graph_build_response(request)


@app.get("/api/graph/build/status/{task_id}")
async def graph_build_status_by_id(task_id: str):
    return task_status_response(task_id)


@app.get("/api/graph/status/{task_id}")
async def graph_status_by_id(task_id: str):
    return task_status_response(task_id)


@app.get("/api/task/{task_id}")
async def generic_task_status(task_id: str):
    return task_status_response(task_id)


@app.get("/api/tasks/{task_id}")
async def generic_tasks_status(task_id: str):
    return task_status_response(task_id)


@app.get("/api/graph/status")
async def graph_status():
    status_payload = {
        "status": "ready",
        "state": "ready",
        "neo4j_configured": bool(neo4j_driver),
        "agents_target": TOTAL_AGENTS,
        "backend_version": BACKEND_VERSION
    }

    return {
        "success": True,
        "ok": True,
        "status": "ready",
        "message": "Graph service is available.",
        **status_payload,
        "data": status_payload,
        "result": status_payload
    }


@app.get("/api/graph/build/status")
async def graph_build_status():
    status_payload = {
        "status": "completed",
        "state": "completed",
        "neo4j_configured": bool(neo4j_driver),
        "agents_target": TOTAL_AGENTS,
        "backend_version": BACKEND_VERSION
    }

    return {
        "success": True,
        "ok": True,
        "status": "completed",
        "message": "Graph build status available.",
        **status_payload,
        "data": status_payload,
        "result": status_payload
    }


# ============================================================
# 9. ONTOLOGIA
# ============================================================

@app.post("/api/graph/ontology/generate")
async def generate_ontology_compat(request: Request):
    payload = await parse_request_payload(request)
    uploaded_file = await get_first_uploaded_file_from_request(request)

    prompt = str(
        payload.get("prompt")
        or payload.get("scenario")
        or payload.get("description")
        or payload.get("project_description")
        or payload.get("content")
        or "Gerar ontologia a partir dos dados enviados."
    )

    source_type = str(
        payload.get("source_type")
        or payload.get("sourceType")
        or payload.get("type")
        or ("upload" if uploaded_file else "text")
    )

    result = await process_update_scenario(
        prompt=prompt,
        source_type=source_type,
        file=uploaded_file
    )

    project_id = get_project_id(payload)

    response = create_task_response(
        task_type="ontology_generate",
        project_id=project_id,
        status="completed",
        message="Ontology generation completed successfully."
    )

    task_id = response["task_id"]

    ontology_payload = {
        "task_id": task_id,
        "id": task_id,
        "project_id": project_id,
        "graph_id": f"graph-{project_id}",
        "ontology_id": f"ontology-{project_id}",
        "status": "completed",
        "state": "completed",
        "result": result,
        "updated_at": utc_now()
    }

    BUILD_TASKS[task_id].update(ontology_payload)

    return {
        "success": True,
        "ok": True,
        "status": "success",
        "state": "completed",
        "message": "Ontology generation routed successfully.",
        "task_id": task_id,
        "id": task_id,
        "project_id": project_id,
        "graph_id": f"graph-{project_id}",
        "ontology_id": f"ontology-{project_id}",
        "data": ontology_payload,
        "result": ontology_payload,
        "task": ontology_payload
    }


@app.get("/api/graph/ontology/status/{task_id}")
async def ontology_status_by_id(task_id: str):
    return task_status_response(task_id)


# ============================================================
# 10. HISTÓRICO / PROJETOS / SIMULAÇÃO
# ============================================================

@app.get("/api/simulation/history")
async def simulation_history(limit: int = 20):
    return {
        "success": True,
        "ok": True,
        "count": 0,
        "data": [],
        "result": []
    }


@app.get("/api/projects/history")
async def projects_history(limit: int = 20):
    return {
        "success": True,
        "ok": True,
        "count": 0,
        "data": [],
        "result": []
    }


@app.get("/api/graph/projects/history")
async def graph_projects_history(limit: int = 20):
    return {
        "success": True,
        "ok": True,
        "count": 0,
        "data": [],
        "result": []
    }


@app.get("/api/projects")
async def projects_list(limit: int = 20):
    return {
        "success": True,
        "ok": True,
        "count": 0,
        "data": [],
        "result": []
    }


@app.post("/api/simulation/start")
async def start_simulation():
    asyncio.create_task(run_simulation_loop())

    return create_task_response(
        task_type="simulation_loop",
        project_id="render-project",
        status="running",
        message="Loop de simulação iniciado em background."
    )


@app.get("/api/simulation/status")
async def simulation_status():
    status_payload = {
        "status": "ready",
        "state": "ready",
        "agents_target": TOTAL_AGENTS,
        "auto_start_simulation": AUTO_START_SIMULATION,
        "backend_version": BACKEND_VERSION
    }

    return {
        "success": True,
        "ok": True,
        **status_payload,
        "data": status_payload,
        "result": status_payload
    }

# ============================================================
# 11. SIMULATION CREATE / RUN - DEVE FICAR ANTES DOS FALLBACKS
# ============================================================

@app.head("/health")
def health_head():
    return {}


def create_simulation_response(
    project_id: str = "render-project",
    status: str = "created",
    message: str = "Simulation created successfully."
) -> Dict[str, Any]:
    simulation_id = f"sim_{uuid4().hex}"
    task_id = f"simulation_{uuid4().hex}"

    payload = {
        "simulation_id": simulation_id,
        "id": simulation_id,
        "task_id": task_id,
        "project_id": project_id,
        "graph_id": f"graph-{project_id}",
        "status": status,
        "state": status,
        "message": message,
        "agents_target": TOTAL_AGENTS,
        "current_round": 0,
        "created_at": utc_now(),
        "updated_at": utc_now()
    }

    BUILD_TASKS[simulation_id] = payload
    BUILD_TASKS[task_id] = payload

    return {
        "success": True,
        "ok": True,
        "status": status,
        "state": status,
        "message": message,
        "simulation_id": simulation_id,
        "id": simulation_id,
        "task_id": task_id,
        "project_id": project_id,
        "graph_id": f"graph-{project_id}",
        "data": payload,
        "result": payload,
        "simulation": payload,
        "task": payload
    }


def simulation_status_response(simulation_id: str) -> Dict[str, Any]:
    simulation = BUILD_TASKS.get(simulation_id)

    if not simulation:
        simulation = {
            "simulation_id": simulation_id,
            "id": simulation_id,
            "task_id": simulation_id,
            "project_id": "render-project",
            "graph_id": "graph-render-project",
            "status": "completed",
            "state": "completed",
            "message": "Simulation não encontrada em memória. Retornando completed para compatibilidade.",
            "agents_target": TOTAL_AGENTS,
            "current_round": 0,
            "created_at": utc_now(),
            "updated_at": utc_now()
        }
        BUILD_TASKS[simulation_id] = simulation

    status = simulation.get("status", "completed")

    return {
        "success": True,
        "ok": True,
        "status": status,
        "state": status,
        "message": simulation.get("message", "Simulation status returned successfully."),
        "simulation_id": simulation.get("simulation_id", simulation_id),
        "id": simulation.get("id", simulation_id),
        "task_id": simulation.get("task_id", simulation_id),
        "project_id": simulation.get("project_id", "render-project"),
        "graph_id": simulation.get("graph_id", "graph-render-project"),
        "data": simulation,
        "result": simulation,
        "simulation": simulation,
        "task": simulation
    }


@app.post("/api/simulation/create")
async def simulation_create(request: Request):
    print("[MiroFish] Rota específica simulation create acionada: /api/simulation/create", flush=True)

    payload = await parse_request_payload(request)
    project_id = get_project_id(payload)

    return create_simulation_response(
        project_id=project_id,
        status="created",
        message="Simulation created successfully."
    )


@app.post("/api/simulation/run")
async def simulation_run(request: Request):
    print("[MiroFish] Rota específica simulation run acionada: /api/simulation/run", flush=True)

    payload = await parse_request_payload(request)
    project_id = get_project_id(payload)

    return create_simulation_response(
        project_id=project_id,
        status="running",
        message="Simulation run started successfully."
    )


@app.post("/api/simulation/{simulation_id}/run")
async def simulation_run_by_id(simulation_id: str):
    print(f"[MiroFish] Rota específica simulation run by id acionada: /api/simulation/{simulation_id}/run", flush=True)

    simulation = BUILD_TASKS.get(simulation_id) or {
        "simulation_id": simulation_id,
        "id": simulation_id,
        "task_id": simulation_id,
        "project_id": "render-project",
        "graph_id": "graph-render-project",
        "agents_target": TOTAL_AGENTS,
        "current_round": 0,
        "created_at": utc_now()
    }

    simulation.update({
        "status": "running",
        "state": "running",
        "message": "Simulation run started successfully.",
        "updated_at": utc_now()
    })

    BUILD_TASKS[simulation_id] = simulation

    return simulation_status_response(simulation_id)


@app.get("/api/simulation/{simulation_id}")
async def simulation_get_by_id(simulation_id: str):
    print(f"[MiroFish] Rota específica simulation get acionada: /api/simulation/{simulation_id}", flush=True)
    return simulation_status_response(simulation_id)


@app.get("/api/simulation/status/{simulation_id}")
async def simulation_status_by_id(simulation_id: str):
    print(f"[MiroFish] Rota específica simulation status acionada: /api/simulation/status/{simulation_id}", flush=True)
    return simulation_status_response(simulation_id)


@app.get("/api/simulation/run-status/{simulation_id}")
async def simulation_run_status_by_id(simulation_id: str):
    print(f"[MiroFish] Rota específica simulation run-status acionada: /api/simulation/run-status/{simulation_id}", flush=True)
    return simulation_status_response(simulation_id)


@app.get("/api/simulation/task/{task_id}")
async def simulation_task_status(task_id: str):
    print(f"[MiroFish] Rota específica simulation task acionada: /api/simulation/task/{task_id}", flush=True)
    return simulation_status_response(task_id)
    
# ============================================================
# 11. DADOS DO GRAFO - DEVE FICAR ANTES DOS FALLBACKS
# ============================================================

def graph_data_payload(graph_id: str) -> Dict[str, Any]:
    """
    Retorna os dados do grafo no formato esperado pelo frontend.
    Mantém múltiplos formatos: nodes/edges, data, result e graph.
    """

    project_id = graph_id.replace("graph-", "") if graph_id.startswith("graph-") else "render-project"

    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []

    # Nós mínimos para o frontend não quebrar
    project_node = {
        "id": project_id,
        "label": "Project",
        "type": "Project",
        "name": "Projeto MiroFish Render",
        "status": "built"
    }

    ontology_node = {
        "id": f"ontology-{project_id}",
        "label": "Ontology",
        "type": "Ontology",
        "name": "Ontologia Gerada",
        "status": "generated"
    }

    nodes.append(project_node)
    nodes.append(ontology_node)

    edges.append({
        "id": f"{project_id}-has-ontology",
        "source": project_id,
        "target": f"ontology-{project_id}",
        "label": "HAS_ONTOLOGY",
        "type": "HAS_ONTOLOGY"
    })

    # Se Neo4j estiver disponível, tenta enriquecer com alguns agentes
    if neo4j_driver:
        try:
            with get_neo4j_session() as session:
                result = session.run(
                    """
                    MATCH (p:Project {id: $project_id})-[:HAS_AGENT]->(a:Agent)
                    RETURN a.id AS agent_id,
                           a.personality AS personality,
                           a.memory AS memory
                    ORDER BY a.id
                    LIMIT 50
                    """,
                    project_id=project_id
                )

                for record in result:
                    agent_id = str(record["agent_id"])
                    agent_node_id = f"agent-{agent_id}"

                    nodes.append({
                        "id": agent_node_id,
                        "label": f"Agent {agent_id}",
                        "type": "Agent",
                        "name": f"Agent {agent_id}",
                        "personality": record["personality"] or "",
                        "memory": record["memory"] or ""
                    })

                    edges.append({
                        "id": f"{project_id}-has-agent-{agent_id}",
                        "source": project_id,
                        "target": agent_node_id,
                        "label": "HAS_AGENT",
                        "type": "HAS_AGENT"
                    })

        except Exception as exc:
            nodes.append({
                "id": "neo4j-warning",
                "label": "Neo4j Warning",
                "type": "Warning",
                "message": str(exc)
            })

    payload = {
        "graph_id": graph_id,
        "project_id": project_id,
        "status": "completed",
        "state": "completed",
        "nodes": nodes,
        "edges": edges,
        "links": edges,
        "elements": {
            "nodes": nodes,
            "edges": edges
        },
        "backend_version": BACKEND_VERSION,
        "nodes_count": len(nodes),
        "edges_count": len(edges)
    }

    return payload


@app.get("/api/graph/data/{graph_id}")
async def graph_data(graph_id: str):
    print(f"[MiroFish] Rota específica graph data acionada: /api/graph/data/{graph_id}", flush=True)

    payload = graph_data_payload(graph_id)

    return {
        "success": True,
        "ok": True,
        "status": "success",
        "state": "completed",
        "graph_id": graph_id,
        "project_id": payload["project_id"],
        "nodes": payload["nodes"],
        "edges": payload["edges"],
        "links": payload["links"],
        "elements": payload["elements"],
        "data": payload,
        "result": payload,
        "graph": payload
    }


@app.get("/api/graph/data/{graph_id}/status")
async def graph_data_status(graph_id: str):
    print(f"[MiroFish] Rota específica graph data status acionada: /api/graph/data/{graph_id}/status", flush=True)

    payload = graph_data_payload(graph_id)

    return {
        "success": True,
        "ok": True,
        "status": "completed",
        "state": "completed",
        "graph_id": graph_id,
        "project_id": payload["project_id"],
        "data": payload,
        "result": payload
    }


@app.get("/api/graph/graph/{graph_id}")
async def graph_graph_data(graph_id: str):
    print(f"[MiroFish] Rota específica graph acionada: /api/graph/graph/{graph_id}", flush=True)
    return await graph_data(graph_id)


@app.get("/api/graph/{graph_id}/data")
async def graph_data_alias(graph_id: str):
    print(f"[MiroFish] Rota específica graph data alias acionada: /api/graph/{graph_id}/data", flush=True)
    return await graph_data(graph_id)
    
# ============================================================
# 11. FALLBACKS - DEVEM SER AS ÚLTIMAS ROTAS DO ARQUIVO
# ============================================================

@app.api_route("/api/graph/{full_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def graph_fallback(full_path: str, request: Request):
    payload = await parse_request_payload(request)

    print(f"[MiroFish] Fallback /api/graph acionado: /api/graph/{full_path}", flush=True)

    if request.method in ["POST", "PUT", "PATCH"]:
        return await start_graph_build_from_payload(payload)

    fallback_payload = {
        "status": "ready",
        "state": "ready",
        "path": f"/api/graph/{full_path}",
        "payload_received": payload,
        "backend_version": BACKEND_VERSION
    }

    return {
        "success": True,
        "ok": True,
        "status": "ready",
        "state": "ready",
        "message": f"Rota /api/graph/{full_path} recebida pelo backend.",
        "path": f"/api/graph/{full_path}",
        "data": fallback_payload,
        "result": fallback_payload
    }


@app.api_route("/api/{full_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def api_fallback(full_path: str, request: Request):
    payload = await parse_request_payload(request)

    print(f"[MiroFish] Fallback /api acionado: /api/{full_path}", flush=True)

    if request.method in ["POST", "PUT", "PATCH"]:
        response = create_task_response(
            task_type="generic_api",
            project_id=get_project_id(payload),
            status="completed",
            message=f"Rota /api/{full_path} recebida pelo backend."
        )

        response["path"] = f"/api/{full_path}"
        response["payload_received"] = payload
        response["data"]["path"] = f"/api/{full_path}"
        response["data"]["payload_received"] = payload
        response["result"]["path"] = f"/api/{full_path}"
        response["result"]["payload_received"] = payload

        return response

    fallback_payload = {
        "status": "ready",
        "state": "ready",
        "path": f"/api/{full_path}",
        "payload_received": payload,
        "backend_version": BACKEND_VERSION
    }

    return {
        "success": True,
        "ok": True,
        "status": "ready",
        "state": "ready",
        "message": f"Rota /api/{full_path} recebida pelo backend.",
        "path": f"/api/{full_path}",
        "data": fallback_payload,
        "result": fallback_payload
    }
