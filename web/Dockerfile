FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
# Proxy (HTTP_PROXY/HTTPS_PROXY) — Docker'ning maxsus build argumentlari: npm ham, apk ham ularni o'zi o'qiydi.
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
RUN apk add --no-cache openssl
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY --chmod=755 tls-selfsigned.sh /docker-entrypoint.d/15-agentmon-tls.sh
COPY --from=build /app/dist /usr/share/nginx/html
ENV WEB_HTTPS_PORT=443
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://127.0.0.1/healthz >/dev/null || exit 1
