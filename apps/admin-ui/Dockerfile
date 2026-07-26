# syntax=docker/dockerfile:1
# Zweistufig: Node nur zur Build-Zeit (Next.js als Build-Tooling, Konzept 8),
# das Laufzeit-Image enthält keinen Node-Prozess - reiner statischer Export,
# ausgeliefert über nginx. Identisches Muster wie apps/user-ui, siehe ADR 0006.
FROM node:22-alpine AS build
WORKDIR /app
COPY apps/admin-ui/package.json apps/admin-ui/package-lock.json* ./
RUN npm install
COPY apps/admin-ui/ ./
ARG NEXT_PUBLIC_GATEWAY_BASE_URL=http://localhost:8009
ENV NEXT_PUBLIC_GATEWAY_BASE_URL=$NEXT_PUBLIC_GATEWAY_BASE_URL
RUN npm run build

FROM nginx:1.27-alpine
COPY apps/admin-ui/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/out /usr/share/nginx/html
EXPOSE 80
