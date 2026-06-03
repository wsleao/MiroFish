import os
import asyncio
from fastapi import FastAPI
from openai import AsyncOpenAI
from neo4j import GraphDatabase
from contextlib import asynccontextmanager

# Configurações obrigatórias coletadas das variáveis de ambiente do Render
API_KEY = os.getenv("LLM_API_KEY") 
BASE_URL = os.getenv("OPENAI_API_BASE", "https://groq.com")
MODEL = os.getenv("MODEL_NAME", "llama-3.1-8b-instant")
BATCH_SIZE = int(os.getenv("AGENT_BATCH_SIZE", "30"))
TOTAL_AGENTS = 3000

# Inicialização dos clientes de IA e do Banco de Dados de Grafos
ai_client = AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL)
neo4j_driver = GraphDatabase.driver(
    os.getenv("NEO4J_URI"), 
    auth=(os.getenv("NEO4J_USERNAME"), os.getenv("NEO4J_PASSWORD"))
)

async def simulate_agent_turn(agent_id, session):
    """Processa um único agente de forma leve e rápida na Groq"""
    try:
        # Busca a memória do agente no Neo4j
        result = session.run(
            "MATCH (a:Agent {id: $id}) RETURN a.memory AS mem, a.personality AS pers", 
            id=agent_id
        )
        record = result.single()
        memory = record["mem"] if record else "Início da simulação."
        personality = record["pers"] if record else "Agente MiroFish."

        # Dispara a chamada para a inteligência artificial
        response = await ai_client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": f"Você é um agente simulado. Perfil: {personality}"},
                {"role": "user", "content": f"Histórico recente: {memory}. Qual sua próxima ação?"}
            ],
            max_tokens=100
        )
        new_memory = response.choices.message.content

        # Grava de volta a resposta no Neo4j AuraDB
        session.run(
            "MATCH (a:Agent {id: $id}) SET a.memory = $mem", 
            id=agent_id, mem=new_memory
        )
    except Exception as e:
        print(f"Erro no agente {agent_id}: {e}")

async def run_simulation_loop():
    """Gerencia o loop infinito dos 3.000 agentes divididos em lotes na nuvem"""
    await asyncio.sleep(5) # Aguarda o servidor HTTP acordar
    while True:
        print("--- [MiroFish] Iniciando ciclo completo de simulação ---", flush=True)
        try:
            with neo4j_driver.session() as session:
                for i in range(0, TOTAL_AGENTS, BATCH_SIZE):
                    current_batch = range(i, min(i + BATCH_SIZE, TOTAL_AGENTS))
                    print(f"-> Processando lote de agentes: {i} até {i + len(current_batch) - 1}", flush=True)
                    
                    tasks = [simulate_agent_turn(agent_id, session) for agent_id in current_batch]
                    await asyncio.gather(*tasks)
                    
                    await asyncio.sleep(0.4) # Intervalo de segurança anti-bloqueio
                    
            print("--- [MiroFish] Ciclo finalizado! Próxima rodada em 15 segundos... ---", flush=True)
            await asyncio.sleep(15)
        except Exception as loop_error:
            print(f"Erro no loop principal: {loop_error}. Reiniciando em 20 segundos...", flush=True)
            await asyncio.sleep(20)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Inicializa o loop contínuo em segundo plano assim que o servidor ligar
    loop_task = asyncio.create_task(run_simulation_loop())
    yield
    loop_task.cancel()

app = FastAPI(lifespan=lifespan)

@app.get("/health")
def health_check():
    """Rota para o UptimeRobot acessar a cada 5 minutos"""
    return {"status": "running", "agents_target": TOTAL_AGENTS}
