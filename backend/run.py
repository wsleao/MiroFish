import os
import asyncio
from fastapi import FastAPI
from openai import AsyncOpenAI
from neo4j import GraphDatabase
from contextlib import asynccontextmanager

# ==========================================
# 1. CONFIGURAÇÕES E ALINHAMENTO DE VARIÁVEIS
# ==========================================
# Alinhado com as travas nativas do MiroFish do Passo 4 e 11
API_KEY = os.getenv("LLM_API_KEY") 
BASE_URL = os.getenv("OPENAI_API_BASE", "https://groq.com")
MODEL = os.getenv("MODEL_NAME", "llama-3.1-8b-instant")
BATCH_SIZE = int(os.getenv("AGENT_BATCH_SIZE", "30"))
TOTAL_AGENTS = 3000

# Inicializa os clientes externos de inteligência e banco de grafos
ai_client = AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL)
neo4j_driver = GraphDatabase.driver(
    os.getenv("NEO4J_URI"), 
    auth=(os.getenv("NEO4J_USERNAME"), os.getenv("NEO4J_PASSWORD"))
)

# ==========================================
# 2. MOTOR DE SIMULAÇÃO DOS AGENTES EM ENXAME
# ==========================================
async def simulate_agent_turn(agent_id, session):
    """Processa a tomada de decisão de um único agente de forma leve via LPU"""
    # Busca o estado de memória atualizado diretamente do Neo4j AuraDB
    result = session.run(
        "MATCH (a:Agent {id: $id}) RETURN a.memory AS mem, a.personality AS pers", 
        id=agent_id
    )
    record = result.single()
    memory = record["mem"] if record else "Início da simulação. Aguardando primeira iteração."
    personality = record["pers"] if record else "Agente do enxame MiroFish."

    try:
        # Envia as informações do agente para o processador assíncrono da Groq
        response = await ai_client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": f"Você é um agente simulado no ecossistema MiroFish. Perfil: {personality}"},
                {"role": "user", "content": f"Histórico recente: {memory}. Qual sua próxima ação no enxame?"}
            ],
            max_tokens=120
        )
        new_memory = response.choices.message.content

        # Salva o resultado de volta no Neo4j desonerando a memória RAM do contêiner
        session.run(
            "MERGE (a:Agent {id: $id}) SET a.memory = $mem", 
            id=agent_id, mem=new_memory
        )
    except Exception as e:
        print(f"Erro no processamento do agente {agent_id}: {e}")

async def run_simulation_loop():
    """Gerencia o looping eterno dos 3.000 agentes fragmentados em lotes rígidos"""
    # Aguarda o servidor HTTP estabilizar para não causar conflito de concorrência
    await asyncio.sleep(5)
    
    while True:
        print("--- [MiroFish] Iniciando ciclo completo de simulação ---")
        try:
            with neo4j_driver.session() as session:
                # Divide toda a população em lotes seguros para evitar estouros de Timeout
                for i in range(0, TOTAL_AGENTS, BATCH_SIZE):
                    current_batch = range(i, min(i + BATCH_SIZE, TOTAL_AGENTS))
                    print(f"-> Processando lote de agentes: {i} até {i + len(current_batch) - 1}")
                    
                    # Dispara as requisições paralelas do lote atual
                    tasks = [simulate_agent_turn(agent_id, session) for agent_id in current_batch]
                    await asyncio.gather(*tasks)
                    
                    # Pequeno intervalo para respeitar o limite de requisições por minuto (RPM)
                    await asyncio.sleep(0.3)
                    
            print("--- [MiroFish] Ciclo finalizado! Próxima rodada em 15 segundos... ---")
            await asyncio.sleep(15)
            
        except Exception as loop_error:
            print(f"Erro no loop principal: {loop_error}. Reiniciando em 30 segundos...")
            await asyncio.sleep(30)

# ==========================================
# 3. CONTROLE DE LIFESPAN DO SERVIDOR WEB
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Inicia o loop contínuo em background assim que o contêiner Docker ligar
    loop_task = asyncio.create_task(run_simulation_loop())
    yield
    # Interrompe o loop de forma limpa caso o servidor seja escalado ou desligado
    loop_task.cancel()

# ==========================================
# 4. EXPOSIÇÃO DA ROTA DE SAÚDE (KEEP-ALIVE)
# ==========================================
app = FastAPI(lifespan=lifespan)

@app.get("/health")
def health_check():
    """Endpoint público acessado pelo UptimeRobot para evitar o modo sleep do Render"""
    return {
        "status": "running", 
        "agents_target": TOTAL_AGENTS,
        "engine": "MiroFish Custom Stack (Groq + Neo4j Aura)"
    }
