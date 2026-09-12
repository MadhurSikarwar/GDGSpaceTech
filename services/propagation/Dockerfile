FROM python:3.12-slim

WORKDIR /app

COPY services/propagation/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY shared /app/shared
COPY services/propagation/app /app/services/propagation/app
COPY services/propagation/data /app/services/propagation/data

ENV PYTHONPATH=/app
EXPOSE 8000

CMD ["uvicorn", "services.propagation.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
