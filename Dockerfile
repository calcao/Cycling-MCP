FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .
RUN useradd -u 10001 -m appuser
USER appuser
EXPOSE 8000
CMD ["cycling-server"]
