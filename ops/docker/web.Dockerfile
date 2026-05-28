FROM node:22-bookworm-slim

WORKDIR /app

COPY apps/web/package.json /app/package.json
COPY apps/web/package-lock.json /app/package-lock.json
RUN npm config set registry https://registry.npmmirror.com && npm ci

COPY apps/web /app

ARG NEXT_PUBLIC_BASE_PATH=""
ENV NODE_ENV=production \
    NEXT_PUBLIC_BASE_PATH=${NEXT_PUBLIC_BASE_PATH} \
    PORT=3000

RUN npm run build

CMD ["npm", "run", "start", "--", "--hostname", "0.0.0.0", "--port", "3000"]
