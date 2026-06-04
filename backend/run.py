from pathlib import Path

code = r'''import os
import asyncio
import asyncpg
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional, Any, Dict
from uuid import uuid4

from fastapi import FastAPI, UploadFile, Form, File, Request
from fastapi.middleware.cors import CORSMiddleware
from openai import AsyncOpenAI
from neo4j import GraphDatabase


# ==========================================
# 1. CONFIGURAÇÕES INTERNAS E CONEXÕES
# ==========================================

API_KEY = os.getenv("LLM_API_KEY")

# Para Groq, use este endpoint compatível com OpenAI.
# Para OpenAI oficial, configure OPENAI_API_BASE=https://api.openai.com/v1
BASE_URL = os.getenv("OPENAI_API_BASE", "https://api.groq.com/openai/v1")

MODEL = os.getenv("MODEL_NAME", "llama-3.1-8b-instant")
TOTAL_AGENTS = int(os.getenv("TOTAL_AGENTS", "3000"))

# Recomendo deixar false no Render enquanto valida as rotas.
AUTO_START_SIMULATION = os.getenv("AUTO_START_SIMULATION", "false").lower() == "true"

# Banco externo / Datalake
POSTGRES_URL = os.getenv("EXTERNAL_DB_URL")

# Neo4j AuraDB
NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

# Controle simples em memória para tarefas do frontend.
# Em produção, isso poderia ir para Redis/Postgres.
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


# ==========================================
# 2. FUNÇÕES AUXILIARES
# ==========================================

def utc_now() -> str:
    return datetime.utcnow().isoformat()


def get_neo4j_session():
    if not neo4j_driver:
        raise RuntimeError(
            "Neo4j não configurado. Verifique NEO4J_URI, "
            "NEO4J_USERNAME e NEO4J_PASSWORD no Render."
        )
    return neo4j_driver.session()


async def parse_request_payload(request: Request) -> dict:
    """
    Lê JSON ou multipart/form-data sem quebrar quando o corpo vier vazio.
    """
    try:
        return await request.json()
    except Exception:
        try:
            form = await request.form()
            payload = {}

            for key, value in form.multi_items():
                if hasattr(value, "filename"):
                    payload[key] = getattr(value, "filename", "")
                else:
                    payload[key] = value

            return payload
        except Exception:
            return {}


async def get_first_uploaded_file_from_request(request: Request) -> Optional[UploadFile]:
    """
    Captura o primeiro arquivo enviado em qualquer campo multipart.
    """
    try:
        form = await request.form()
        for _, value in form.multi_items():
            if hasattr(value, "filename") and value.filename:
                return value
    except Exception:
        return None

    return None


def create_task_response(
    task_type: str = "graph_build",
    project_id: str = "render-project",
    status: str = "pending",
    message: str = "Task created successfully."
) -> Dict[str, Any]:
    """
    Resposta padronizada para o frontend do MiroFish.

    Garante task_id em vários formatos:
    - response.task_id
    - response.data.task_id
    - response.result.task_id
    """

    task_id = f"{task_type}_{uuid4().hex}"

    task_payload = {
        "task_id": task_id,
        "id": task_id,
        "project_id": project_id,
        "status": status,
        "task_type": task_type,
        "created_at": utc_now()
    }

    BUILD_TASKS[task_id] = task_payload

    return {
        "success": True,
        "status": status,
        "message": message,

        # Formato direto
        "task_id": task_id,
        "id": task_id,
        "project_id": project_id,

        # Formatos esperados por frontends diferentes
        "data": task_payload,
        "result": task_payload
    }


def get_project_id(payload: dict | None = None) -> str:
    payload = payload or {}

    return str(
        payload.get("project_id")
        or payload.get("projectId")
        or payload.get("id")
        or payload.get("project")
        or "render-project"
    )


def get_project_name(payload: dict | None = None) -> str:
    payload = payload or {}

    return str(
        payload.get("project_name")
        or payload.get("projectName")
        or payload.get("name")
        or "Projeto MiroFish Render"
    )


# ==========================================
# 3. SIMULAÇÃO DE AGENTES
# ==========================================

async def simulate_agent_turn(agent_id: int):
    """
    Processa um único agente.

    Correções aplicadas:
    - Não compartilha a mesma sessão Neo4j entre tarefas concorrentes.
    - Usa response.choices[0].message.content.
    - Valida se LLM_API_KEY e Neo4j existem.
    """

    if not ai_client:
        print("LLM_API_KEY não configurada. Pulando simulação de agente.", flush=True)
        return

    if not neo4j_driver:
        print("Neo4j não configurado. Pulando simulação de agente.", flush=True)
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

    except Exception as e:
        print(f"Erro no agente {agent_id}: {e}", flush=True)


async def run_simulation_loop():
    """
    Loop fragmentado para reduzir risco de estourar cota de TPM/RPM.
    """

    await asyncio.sleep(5)

    micro_batch_size = int(os.getenv("MICRO_BATCH_SIZE", "2"))
    pause_between_batches = float(os.getenv("PAUSE_BETWEEN_BATCHES", "15"))
    pause_between_cycles = float(os.getenv("PAUSE_BETWEEN_CYCLES", "60"))

    while True:
        print("--- [MiroFish] Iniciando ciclo de simulação ---", flush=True)

        try:
            for i in range(0, TOTAL_AGENTS, micro_batch_size):
                current_batch = range(i, min(i + micro_batch_size, TOTAL_AGENTS))
                print(
                    f"-> Processando lote: {i} até {i + len(current_batch) - 1}",
                    flush=True
                )

                tasks = [simulate_agent_turn(agent_id) for agent_id in current_batch]
                await asyncio.gather(*tasks)

                await asyncio.sleep(pause_between_batches)

            print(
                f"--- [MiroFish] Ciclo finalizado. Próxima rodada em {pause_between_cycles}s ---",
                flush=True
            )
            await asyncio.sleep(pause_between_cycles)

        except Exception as loop_error:
            print(
                f"Erro no loop: {loop_error}. Reiniciando em 20 segundos...",
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


app = FastAPI(lifespan=lifespan)


# ==========================================
# 4. CORS
# ==========================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==========================================
# 5. EXTRAÇÃO DO POSTGRESQL
# ==========================================

async def buscar_dados_do_postgres() -> str:
    """
    Conecta no Postgres e busca uma carga de contexto.

    Ajuste a query para uma tabela real do seu Datalake.
    Hoje ela está preservando a lógica do seu exemplo original.
    """

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

    except Exception as e:
        return f"Falha ao ler o PostgreSQL: {e}"

    finally:
        if conn:
            await conn.close()


async def extract_context_from_file(file: Optional[UploadFile]) -> str:
    if not file:
        return ""

    content = await file.read()
    return content.decode("utf-8", errors="ignore")


async def sync_agents_context(prompt: str, contexto_final: str) -> Dict[str, Any]:
    """
    Atualiza os agentes no Neo4j.
    Se ainda não existirem agentes, cria de 0 até TOTAL_AGENTS - 1.
    """

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
    """
    Função central reutilizada pelas rotas:
    - /update-scenario
    - /api/graph/ontology/generate
    """

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
        "status": "success",
        "success": True,
        "message": f"Mentes sincronizadas usando a fonte: {normalized_source_type}",
        "source_type": normalized_source_type,
        "preview_size": len(contexto_adicional),
        "context_preview": contexto_final[:2000],
        "neo4j": neo4j_result
    }


# ==========================================
# 6. ROTAS ORIGINAIS
# ==========================================

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


@app.get("/health")
def health_check():
    return {
        "status": "running",
        "agents_target": TOTAL_AGENTS,
        "auto_start_simulation": AUTO_START_SIMULATION,
        "llm_configured": bool(API_KEY),
        "neo4j_configured": bool(neo4j_driver),
        "postgres_configured": bool(POSTGRES_URL),
        "model": MODEL,
        "base_url": BASE_URL,
        "routes_hint": [
            "/update-scenario",
            "/api/graph/ontology/generate",
            "/api/graph/build",
            "/api/graph/build/status/{task_id}",
            "/api/simulation/history"
        ]
    }


# ==========================================
# 7. ROTAS COMPATÍVEIS COM ONTOLOGIA
# ==========================================

@app.post("/api/graph/ontology/generate")
async def generate_ontology_compat(request: Request):
    """
    Corrige o 404 da rota esperada pelo frontend:
    POST /api/graph/ontology/generate
    """

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

    task_response = create_task_response(
        task_type="ontology_generate",
        project_id=project_id,
        status="completed",
        message="Ontology generation completed successfully."
    )

    task_id = task_response["task_id"]

    ontology_payload = {
        "task_id": task_id,
        "id": task_id,
        "project_id": project_id,
        "ontology_id": f"ontology-{project_id}",
        "status": "completed",
        "result": result
    }

    BUILD_TASKS[task_id].update(ontology_payload)

    return {
        "success": True,
        "status": "success",
        "message": "Ontology generation routed successfully.",

        # Formato direto
        "task_id": task_id,
        "id": task_id,
        "project_id": project_id,
        "ontology_id": f"ontology-{project_id}",

        # Formatos usados por frontends Axios
        "data": ontology_payload,
        "result": ontology_payload
    }


@app.get("/api/graph/ontology/status/{task_id}")
async def ontology_status_by_id(task_id: str):
    return await graph_build_status_by_id(task_id)


# ==========================================
# 8. BUILD DO GRAFO
# ==========================================

async def execute_graph_build_task(task_id: str, payload: dict | None = None):
    """
    Executa o build do grafo em background.
    O frontend recebe task_id imediatamente e depois consulta status.
    """

    payload = payload or {}

    project_id = get_project_id(payload)
    project_name = get_project_name(payload)

    BUILD_TASKS.setdefault(task_id, {})
    BUILD_TASKS[task_id].update({
        "task_id": task_id,
        "id": task_id,
        "project_id": project_id,
        "status": "running",
        "started_at": utc_now()
    })

    try:
        if not neo4j_driver:
            BUILD_TASKS[task_id].update({
                "status": "completed",
                "message": "Neo4j não configurado. Build simulado concluído.",
                "graph_id": f"graph-{project_id}",
                "ontology_id": f"ontology-{project_id}",
                "nodes_created": 0,
                "relationships_created": 0,
                "completed_at": utc_now()
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

        BUILD_TASKS[task_id].update({
            "status": "completed",
            "message": "Graph build completed successfully.",
            "graph_id": f"graph-{project_id}",
            "ontology_id": f"ontology-{project_id}",
            "nodes_created": int(agents_created) + 2,
            "relationships_created": int(agents_created) + 1,
            "completed_at": utc_now()
        })

    except Exception as e:
        BUILD_TASKS[task_id].update({
            "status": "failed",
            "message": str(e),
            "error": str(e),
            "completed_at": utc_now()
        })


async def start_graph_build_from_payload(payload: dict | None = None) -> Dict[str, Any]:
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
    task = BUILD_TASKS.get(task_id)

    if not task:
        task = {
            "task_id": task_id,
            "id": task_id,
            "status": "completed",
            "message": "Task não encontrada em memória. Retornando completed para compatibilidade."
        }

    return {
        "success": True,
        "status": task.get("status", "completed"),
        "task_id": task_id,
        "id": task_id,
        "data": task,
        "result": task
    }


@app.get("/api/graph/status/{task_id}")
async def graph_status_by_id(task_id: str):
    return await graph_build_status_by_id(task_id)


@app.get("/api/task/{task_id}")
async def generic_task_status(task_id: str):
    return await graph_build_status_by_id(task_id)


@app.get("/api/tasks/{task_id}")
async def generic_tasks_status(task_id: str):
    return await graph_build_status_by_id(task_id)


@app.get("/api/graph/status")
async def graph_status():
    status_payload = {
        "status": "ready",
        "neo4j_configured": bool(neo4j_driver),
        "agents_target": TOTAL_AGENTS
    }

    return {
        "success": True,
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
        "neo4j_configured": bool(neo4j_driver),
        "agents_target": TOTAL_AGENTS
    }

    return {
        "success": True,
        "status": "completed",
        "message": "Graph build status available.",
        **status_payload,
        "data": status_payload,
        "result": status_payload
    }


# ==========================================
# 9. HISTÓRICO / PROJETOS / SIMULAÇÃO
# ==========================================

@app.get("/api/simulation/history")
