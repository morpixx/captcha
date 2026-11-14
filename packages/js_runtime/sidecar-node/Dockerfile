FROM node:20-alpine

WORKDIR /app

# Copy manifest first so layer caches when deps don't change.
COPY package.json ./
RUN npm install --omit=dev --no-audit --no-fund && npm cache clean --force

COPY server.js ./

EXPOSE 7474

ENV NODE_ENV=production \
    SIDECAR_PORT=7474 \
    SIDECAR_HOST=0.0.0.0

# --no-deprecation quiets node:vm codeGeneration warnings in prod.
CMD ["node", "--no-deprecation", "server.js"]
