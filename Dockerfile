FROM python:3.11-slim

WORKDIR /app

# Força a instalação limpa e sem cache de todas as dependências obrigatórias
RUN pip install --no-cache-dir fastapi uvicorn openai neo4j asyncpg python-multipart

# Copia a pasta do backend com os scripts para o container
COPY ./backend /app/backend

EXPOSE 10000

# Executa mapeando a raiz do PYTHONPATH
CMD ["sh", "-c", "PYTHONPATH=. uvicorn backend.run:app --host 0.0.0.0 --port 10000"]

RUN pip install --no-cache-dir fastapi uvicorn openai neo4j asyncpg python-multipart
