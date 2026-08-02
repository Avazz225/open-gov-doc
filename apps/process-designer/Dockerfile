# syntax=docker/dockerfile:1
# Zweistufig: Node nur zur Build-Zeit (Next.js als Build-Tooling, Konzept 8),
# das Laufzeit-Image enthält keinen Node-Prozess - reiner statischer Export,
# ausgeliefert über nginx.
FROM node:22-alpine AS build
WORKDIR /app
COPY apps/process-designer/package.json apps/process-designer/package-lock.json* ./
RUN npm install
COPY apps/process-designer/ ./
# Gateway-Adresse zur Build-Zeit fest einbrennen (kein Server, der es später
# nachladen könnte) - überschreibbar über einen Build-Arg.
ARG NEXT_PUBLIC_GATEWAY_BASE_URL=http://localhost:8009
ENV NEXT_PUBLIC_GATEWAY_BASE_URL=$NEXT_PUBLIC_GATEWAY_BASE_URL
RUN npm run build

FROM nginx:1.27-alpine
COPY apps/process-designer/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/out /usr/share/nginx/html
EXPOSE 80
