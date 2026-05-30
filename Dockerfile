FROM python:3.12-slim

# Build deps for psycopg2 and XGBoost
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps (Prophet excluded — Stan compilation is slow in CI;
# XGBoost is the primary forecasting model in this stack)
COPY pyproject.toml .
RUN pip install --no-cache-dir \
    "fastapi>=0.115" "uvicorn[standard]>=0.32" \
    "anthropic>=0.40" \
    "psycopg2-binary>=2.9" \
    "xgboost>=2.0" "scikit-learn>=1.4" "pandas>=2.2" "numpy>=1.26" \
    "python-dotenv>=1.0" "pydantic>=2.6" "pydantic-settings>=2.2" \
    "httpx>=0.27" "tenacity>=8.2" \
    "mcp>=1.0" "pgvector>=0.3" \
    "python-multipart>=0.0.9" "jinja2>=3.1"

COPY . .

EXPOSE 8000

CMD ["uvicorn", "api.main:app", \
     "--host", "0.0.0.0", "--port", "8000", "--reload"]
