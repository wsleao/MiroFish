import os
import asyncio
import asyncpg
from contextlib import asynccontextmanager
from typing import Optional, Any, Dict, List

from fastapi import FastAPI, UploadFile, Form, File, Request
from fastapi.middleware.cors import CORSMiddleware
from openai import AsyncOpenAI
from neo4j import GraphDatabase


# ==========================================
# 1. CONFIGURAÇÕES INTERNAS E CONEXÕES
# ==========================================

API_KEY = os.getenv("LLM_API_KEY")

# Para Groq, o padrão correto é este:
BASE_URL = os.getenv("OPENAI_API_BASE", "https://api.groq.com/openai/v1")

MODEL = os.getenv("MODEL_NAME", "llama-3.1-8b-instant")
TOTAL_AGENTS = int(os.getenv("TOTAL_AGENTS", "3000"))

# Por segurança, deixe false no Render enquanto estiver testando as rotas.
AUTO_START_SIMULATION = os.getenv("AUTO_START_SIMULATION", "false").lower() == "true"

POSTGRES_URL = os.getenv("EXTERNAL_DB_URL")

NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

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

def get_neo4j_session():
    if not neo4j_driver:
        raise RuntimeError(
            "Neo4j não configurado. Verifique NEO4J_URI, NEO4J_USERNAME e NEO4J_PASSWORD no Render."
        )
    return neo4j_driver.session()


async def simulate_agent_turn(agent_id: int):
    """
    Processa um único agente.
    Corrigido:
    - Não compartilha a mesma sessão Neo4j entre tarefas concorrentes.
    - Corrige response.choices[0].message.content.
    - Valida se LLM_API_KEY existe.
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
                SET a.memory = $mem
                """,
                id=agent_id,
                mem=new_memory
            )

    except Exception as e:
        print(f"Erro no agente {agent_id}: {e}", flush=True)


async def run_simulation_loop():
    """
    Loop fragmentado.
    Recomendo deixar AUTO_START_SIMULATION=false no Render enquanto testa o deploy.
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
# 3. CORS
# ==========================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==========================================
# 4. EXTRAÇÃO DO POSTGRESQL
# ==========================================

async def buscar_dados_do_postgres() -> str:
    """
    Conecta no Postgres e busca uma carga de contexto.

    Atenção:
    A query abaixo ainda está com tabela genérica.
    Ajuste para uma tabela real do seu Datalake.
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


async def sync_agents_context(prompt: str, contexto_final: str):
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
                a.memory = $contexto
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
# 5. ROTAS ORIGINAIS
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
        "base_url": BASE_URL
    }


# ==========================================
# 6. ROTAS COMPATÍVEIS COM O FRONTEND DO MIROFISH
# ==========================================

@app.post("/api/graph/ontology/generate")
async def generate_ontology_compat(request: Request):
    """
    Rota criada para corrigir o erro 404.

    O frontend está chamando:
    /api/graph/ontology/generate

    Mas o backend original tinha apenas:
    /update-scenario
    """

    form = await request.form()

    prompt = (
        form.get("prompt")
        or form.get("scenario")
        or form.get("description")
        or form.get("project_description")
        or form.get("content")
        or "Gerar ontologia a partir dos dados enviados."
    )

    source_type = (
        form.get("source_type")
        or form.get("sourceType")
        or form.get("type")
        or "upload"
    )

    uploaded_file = None

    for _, value in form.multi_items():
        if hasattr(value, "filename") and value.filename:
            uploaded_file = value
            break

    result = await process_update_scenario(
        prompt=str(prompt),
        source_type=str(source_type),
        file=uploaded_file
    )

    return {
        "success": True,
        "status": "success",
        "message": "Ontology generation routed successfully.",
        "project_id": "render-project",
        "ontology_id": "render-ontology",
        "data": result
    }


@app.get("/api/simulation/history")
async def simulation_history(limit: int = 20):
    """
    Evita erro no frontend ao carregar histórico.
    """
    return {
        "count": 0,
        "data": []
    }


@app.get("/api/projects/history")
async def projects_history(limit: int = 20):
    """
    Evita erro no frontend ao carregar histórico de projetos.
    """
    return {
        "count": 0,
        "data": []
    }


@app.get("/api/graph/projects/history")
async def graph_projects_history(limit: int = 20):
    """
    Alias adicional para histórico de projetos.
    """
    return {
        "count": 0,
        "data": []
    }


@app.get("/api/projects")
async def projects_list(limit: int = 20):
    """
    Alias adicional para lista de projetos.
    """
    return {
        "count": 0,
        "data": []
    }


@app.post("/api/simulation/start")
async def start_simulation():
    """
    Inicia uma rodada em background manualmente.
    """
    asyncio.create_task(run_simulation_loop())

    return {
        "success": True,
        "status": "started",
        "message": "Loop de simulação iniciado em background."
    }
