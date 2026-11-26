# Imagen de la web (Next.js). El modo de la API (mock, hybrid o real) queda fijo al construir,
# así que llega como argumentos del build (next.config.ts, .env.example).
FROM node:22-slim

WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

ARG NEXT_PUBLIC_API_MODE=real
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ARG NEXT_PUBLIC_API_REAL=
# true: permite un build que no es `real` (la demo en modo híbrido). Nunca en un deploy.
ARG API_MOCKS_IN_BUILD=false
ENV NEXT_PUBLIC_API_MODE=$NEXT_PUBLIC_API_MODE \
    NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_API_REAL=$NEXT_PUBLIC_API_REAL \
    API_MOCKS_IN_BUILD=$API_MOCKS_IN_BUILD

RUN npm run build

ENV NODE_ENV=production
EXPOSE 3000
CMD ["npx", "next", "start", "--hostname", "0.0.0.0", "--port", "3000"]
