import os
import asyncio
from fastapi import FastAPI
from openai import AsyncOpenAI
from neo4j import GraphDatabase
from contextlib import asynccontextmanager

# ==========================================
# 1. CONFIGURAÇÕES E ALINHAMENTO DE VARIÁVEIS
# ==========================================
API_KEY = os.getenv("LLM_API_KEY") 
BASE_URL = os.getenv("OPENAI_API_BASE", "https://groq.com")
MODEL = os.getenv("MODEL_NAME", "llama-3.1-8b-instant")
TOTAL_AGENTS = 3000

# Inicialização dos clientes de IA e do Banco de Dados de Grafos
ai_client = AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL)
neo4j_driver = GraphDatabase.driver(
    os.getenv("NEO4J_URI"), 
    auth=(os.getenv("NEO4J_USERNAME"), os.getenv("NEO4J_PASSWORD"))
)

# ==========================================
# 2. MOTOR DE SIMULAÇÃO DOS AGENTES EM ENXAME
# ==========================================
async def simulate_agent_turn(agent_id, session):
    """Processa um único agente de forma leve e rápida na Groq"""
    try:
        # Busca a memória do agente no Neo4j AuraDB
        result = session.run(
            "MATCH (a:Agent {id: $id}) RETURN a.memory AS mem, a.personality AS pers", 
            id=agent_id
        )
        record = result.single()
        memory = record["mem"] if record else "Início da simulação. Aguardando primeira iteração."
        personality = record["pers"] if record else "Agente MiroFish."

        # Dispara a chamada para a inteligência artificial
        response = await ai_client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": f"Você é um agente simulado no ecossistema MiroFish. Perfil: {personality}"},
                {"role": "user", "content": f"Histórico recente: {memory}. Qual sua próxima ação no enxame?"}
            ],
            max_tokens=100
        )
        
        # CORREÇÃO CRÍTICA: Adicionado o índice [0] para ler a lista de escolhas da API OpenAI v1+
        new_memory = response.choices[0].message.content

        # Grava de volta a resposta e atualiza o grafo no Neo4j AuraDB
        session.run(
            "MATCH (a:Agent {id: $id}) SET a.memory = $mem", 
            id=agent_id, mem=new_memory
        )
    except Exception as e:
        print(f"Erro no agente {agent_id}: {e}", flush=True)

async def run_simulation_loop():
    """Gerencia o loop infinito dos 3.000 agentes controlando o ritmo para evitar o erro 429"""
    await asyncio.sleep(5) # Aguarda o servidor HTTP acordar
    
    # Configurações estritas para respeitar o limite gratuito de 30 requisições por minuto (RPM) da Groq
    SAFE_BATCH_SIZE = 5            # Processa apenas 5 agentes por vez concorrentemente
    PAUSE_BETWEEN_BATCHES = 11.0   # Aguarda 11 segundos entre cada micro-lote (aproximadamente 27 requisições por minuto)

    while True:
        print("--- [MiroFish] Iniciando ciclo completo de simulação ---", flush=True)
        try:
            with neo4j_driver.session() as session:
                for i in range(0, TOTAL_AGENTS, SAFE_BATCH_SIZE):
                    current_batch = range(i, min(i + SAFE_BATCH_SIZE, TOTAL_AGENTS))
                    print(f"-> Processando lote seguro de agentes: {i} até {i + len(current_batch) - 1}", flush=True)
                    
                    # Dispara as chamadas concorrentes do micro-lote
                    tasks = [simulate_agent_turn(agent_id, session) for agent_id in current_batch]
                    await asyncio.gather(*tasks)
                    
                    # Pausa estratégica para diluir as requisições dentro do limite de 30 RPM da Groq
                    await asyncio.sleep(PAUSE_BETWEEN_BATCHES)
                    
            print("--- [MiroFish] Ciclo finalizado! Próxima rodada em 30 segundos... ---", flush=True)
            await asyncio.sleep(30)
        except Exception as loop_error:
            print(f"Erro no loop principal: {loop_error}. Reiniciando em 20 segundos...", flush=True)
            await asyncio.sleep(20)

# ==========================================
# 3. CONTROLE DE LIFESPAN DO SERVIDOR WEB
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Inicializa o loop contínuo em segundo plano assim que o contêiner Docker ligar
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
    """Rota para o UptimeRobot acessar a cada 5 minutos"""
    return {
        "status": "running", 
        "agents_target": TOTAL_AGENTS,
        "rate_limiting": "active (5 agents / 11s)"
    }
