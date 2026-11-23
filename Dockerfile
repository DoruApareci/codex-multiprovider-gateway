FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN groupadd -r gateway && useradd -r -g gateway gateway
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY gateway.py providers.yaml ./
COPY catalog ./catalog
USER gateway
EXPOSE 8787
CMD ["uvicorn", "gateway:app", "--host", "0.0.0.0", "--port", "8787", "--no-access-log", "--no-proxy-headers"]
