FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .
RUN useradd --uid 10001 --user-group --create-home appuser
USER 10001:10001
EXPOSE 8000
CMD ["cycling-server"]
