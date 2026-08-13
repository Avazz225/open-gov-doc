# syntax=docker/dockerfile:1
# Zweistufig: Node nur zur Build-Zeit (Next.js als Build-Tooling, Konzept 8),
# das Laufzeit-Image enthält keinen Node-Prozess - reiner statischer Export,
# ausgeliefert über nginx.
FROM node:22-alpine AS build
WORKDIR /app
COPY apps/user-ui/package.json apps/user-ui/package-lock.json* ./
RUN npm install
COPY apps/user-ui/ ./
# Gateway-Adresse zur Build-Zeit fest einbrennen (kein Server, der es später
# nachladen könnte) - überschreibbar über einen Build-Arg.
ARG NEXT_PUBLIC_GATEWAY_BASE_URL=http://localhost:8009
ENV NEXT_PUBLIC_GATEWAY_BASE_URL=$NEXT_PUBLIC_GATEWAY_BASE_URL
# Office-Direktbearbeitung (Post-Roadmap-Feature): webdav-connector-Adresse,
# gleiches Buildzeit-Einbrenn-Prinzip wie oben - der Browser spricht diesen
# Dienst direkt an, nicht über den Gateway.
ARG NEXT_PUBLIC_WEBDAV_CONNECTOR_BASE_URL=http://localhost:8027
ENV NEXT_PUBLIC_WEBDAV_CONNECTOR_BASE_URL=$NEXT_PUBLIC_WEBDAV_CONNECTOR_BASE_URL
RUN npm run build

FROM nginx:1.27-alpine
COPY apps/user-ui/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/out /usr/share/nginx/html
EXPOSE 80
