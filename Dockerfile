FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn openai neo4j asyncpg python-multipart

COPY ./backend /app/backend

EXPOSE 10000

CMD ["sh", "-c", "PYTHONPATH=. uvicorn backend.nsc_core:app --host 0.0.0.0 --port 10000"]
