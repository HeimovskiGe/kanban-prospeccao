FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY worker.py .
# Worker de disparos — fica escutando a tabela campaigns no Supabase
CMD ["python", "worker.py"]
