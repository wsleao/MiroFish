import os
import asyncio
import asyncpg
from fastapi import FastAPI, UploadFile, Form, File
from fastapi.middleware.cors import CORSMiddleware
from openai import AsyncOpenAI
from neo4j import GraphDatabase
from contextlib import asynccontextmanager
from typing import Optional

# ==========================================
# 1. CONFIGURAÇÕES INTERNAS E CONEXÕES
# ==========================================
API_KEY = os.getenv("LLM_API_KEY") 
BASE_URL = os.getenv("OPENAI_API_BASE", "https://groq.com")
MODEL = os.getenv("MODEL_NAME", "llama-3.1-8b-instant")
TOTAL_AGENTS = 3000

# Strings de conexão dos bancos de dados (Configure no painel Environment do Render)
POSTGRES_URL = os.getenv("EXTERNAL_DB_URL")
neo4j_driver = GraphDatabase.driver(
    os.getenv("NEO4J_URI"), 
    auth=(os.getenv("NEO4J_USERNAME"), os.getenv("NEO4J_PASSWORD"))
)
ai_client = AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL)

async def simulate_agent_turn(agent_id, session):
    """Processa um único agente controlando o uso de tokens (TPM)"""
    try:
        result = session.run(
            "MATCH (a:Agent {id: $id}) RETURN a.memory AS mem, a.personality AS pers", 
            id=agent_id
        )
        record = result.single()
        memory = record["mem"] if record else "Início da simulação."
        personality = record["pers"] if record else "Agente MiroFish."

        response = await ai_client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": f"Você é um investidor. Perfil: {personality}"},
                {"role": "user", "content": f"Histórico: {memory}. Qual sua próxima ação breve?"}
            ],
            max_tokens=60
        )
        new_memory = response.choices[0].message.content

        session.run(
            "MATCH (a:Agent {id: $id}) SET a.memory = $mem", 
            id=agent_id, mem=new_memory
        )
    except Exception as e:
        print(f"Erro no agente {agent_id}: {e}", flush=True)

async def run_simulation_loop():
    """Loop eterno fragmentado para respeitar as cotas gratuitas da Groq"""
    await asyncio.sleep(5)
    MICRO_BATCH_SIZE = 2      
    PAUSE_BETWEEN_BATCHES = 15.0 

    while True:
        print("--- [MiroFish] Iniciando ciclo de simulação ---", flush=True)
        try:
            with neo4j_driver.session() as session:
                for i in range(0, TOTAL_AGENTS, MICRO_BATCH_SIZE):
                    current_batch = range(i, min(i + MICRO_BATCH_SIZE, TOTAL_AGENTS))
                    print(f"-> Processando lote: {i} até {i + len(current_batch) - 1}", flush=True)
                    
                    tasks = [simulate_agent_turn(agent_id, session) for agent_id in current_batch]
                    await asyncio.gather(*tasks)
                    
                    await asyncio.sleep(PAUSE_BETWEEN_BATCHES)
                    
            print("--- [MiroFish] Ciclo finalizado! Próxima rodada em 60 segundos... ---", flush=True)
            await asyncio.sleep(60)
        except Exception as loop_error:
            print(f"Erro no loop: {loop_error}. Reiniciando em 20 segundos...", flush=True)
            await asyncio.sleep(20)

@asynccontextmanager
async def lifespan(app: FastAPI):
    loop_task = asyncio.create_task(run_simulation_loop())
    yield
    loop_task.cancel()

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# 3. EXTRAÇÃO ASSÍNCRONA DO POSTGRESQL
# ==========================================
async def buscar_dados_do_postgres() -> str:
    """Conecta no banco Postgres público e puxa a última linha registrada"""
    if not POSTGRES_URL:
        return "Erro: Link do Postgres externo não configurado no Render."
    try:
        conn = await asyncpg.connect(POSTGRES_URL)
        # ATENÇÃO: Ajuste 'sua_tabela' e 'conteudo_texto' conforme a estrutura do seu banco
        query = "SELECT conteudo_texto FROM sua_tabela ORDER BY id DESC LIMIT 1;"
        row = await conn.fetchrow(query)
        await conn.close()
        
        if row and row["conteudo_texto"]:
            return str(row["conteudo_texto"])
        return "Tabela vazia ou formato incorreto no Postgres externo."
    except Exception as e:
        return f"Falha ao ler o Postgres externo: {e}"

# ==========================================
# 4. ROTA DE ATUALIZAÇÃO INTELIGENTE (MULTI-FONTE)
# ==========================================
@app.post("/update-scenario")
async def update_scenario(
    prompt: str = Form(...),
    source_type: str = Form(...), # Define a escolha: 'file' ou 'postgres'
    file: Optional[UploadFile] = File(None)
):
    """
    Rota central adaptável. Lê o parâmetro source_type vindo do Frontend 
    e decide de onde extrair a carga de dados para o enxame de 3.000 agentes.
    """
    contexto_adicional = ""

    # CASO 1: O usuário escolheu usar um banco de dados PostgreSQL
    if source_type == "postgres":
        print("[MiroFish] Fonte detectada: Banco de Dados PostgreSQL", flush=True)
        contexto_adicional = await buscar_dados_do_postgres()

    # CASO 2: O usuário escolheu fazer upload manual de um arquivo local (.txt, .csv)
    elif source_type == "file" and file:
        print(f"[MiroFish] Fonte detectada: Upload de Arquivo ({file.filename})", flush=True)
        conteudo = await file.read()
        contexto_adicional = conteudo.decode("utf-8", errors="ignore")
    
    else:
        contexto_adicional = "Nenhuma fonte de dados secundária foi acoplada."

    # Junta a diretriz com a massa de dados coletada
    contexto_final = f"{prompt}\n\n[Carga de Contexto Vinculada]:\n{contexto_adicional}"

    # Salva em lote no Neo4j AuraDB para mudar a mente de todos os agentes
    with neo4j_driver.session() as session:
        session.run(
            "MATCH (a:Agent) SET a.personality = $prompt, a.memory = $contexto", 
            prompt=prompt, contexto=contexto_final
        )

    return {
        "status": "success",
        "message": f"Mentes sincronizadas usando a fonte: {source_type}",
        "preview_size": len(contexto_adicional)
    }

@app.get("/health")
def health_check():
    return {"status": "running", "agents_target": TOTAL_AGENTS}
