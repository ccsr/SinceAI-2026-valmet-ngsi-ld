FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY scripts/api.py ./scripts/
COPY data-model/ ./data-model/
COPY entities/ ./entities/

EXPOSE 8000

CMD ["python3", "scripts/api.py", "--entities", "./entities", "--port", "8000", "--host", "0.0.0.0"]
