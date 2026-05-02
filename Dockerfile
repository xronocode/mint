FROM python:3.12-slim AS python-base

RUN apt-get update && apt-get install -y --no-install-recommends \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY package.json ./
RUN npm install --omit=dev

COPY pyproject.toml ./
COPY src/ src/
COPY rules/ rules/
COPY skills/ skills/
COPY templates/ templates/

RUN pip install --no-cache-dir .

COPY .env.example .env.example

ENV MINT_ROOT=/app
ENV MINT_RULES_DIR=/app/rules
ENV MINT_SKILLS_DIR=/app/skills
ENV MINT_TEMPLATES_DIR=/app/templates
ENV MINT_TOKENS_DIR=/app/tokens
ENV MINT_SANDBOX_TIMEOUT=30

EXPOSE 8080

ENTRYPOINT ["mint"]
CMD ["serve", "--transport", "sse", "--host", "0.0.0.0", "--port", "8080"]
