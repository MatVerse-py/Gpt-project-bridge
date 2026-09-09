FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN useradd --create-home --uid 10001 bridge
COPY pyproject.toml ./
COPY projectvault ./projectvault
RUN pip install --no-cache-dir .
RUN mkdir -p /app/data && chown -R bridge:bridge /app
USER bridge
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/health', timeout=3)"
CMD ["projectvault", "serve", "--host", "0.0.0.0", "--port", "8787"]
