FROM python:3.12-slim

WORKDIR /srv/matverse

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt /srv/matverse/requirements.txt
RUN pip install --no-cache-dir -r /srv/matverse/requirements.txt

COPY services /srv/matverse/services

EXPOSE 8080

CMD ["uvicorn", "services.external_secret_broker.app:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
