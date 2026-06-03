FROM python:3.11-slim

WORKDIR /app

# Instala as bibliotecas necessárias para rodar os agentes
RUN pip install --no-cache-dir fastapi uvicorn openai neo4j

# Copia apenas a pasta do backend para dentro do servidor
COPY ./backend /app/backend

EXPOSE 10000

# Executa diretamente o nosso script de orquestração
CMD ["uvicorn", "backend.run:app", "--host", "0.0.0.0", "--port", "10000"]

CMD ["uvicorn", "backend.run:app", "--host", "0.0.0.0", "--port", "10000"]

