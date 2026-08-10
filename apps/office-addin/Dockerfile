# syntax=docker/dockerfile:1
# Zweistufig: Node nur zur Build-Zeit (Next.js als Build-Tooling, Konzept 8),
# das Laufzeit-Image enthält keinen Node-Prozess - reiner statischer Export,
# ausgeliefert über nginx. `manifest.xml`/`assets/` sind KEIN Next.js-Build-
# Ausgang - werden separat ins fertige Image kopiert.
FROM node:22-alpine AS build
WORKDIR /app
COPY apps/office-addin/package.json apps/office-addin/package-lock.json* ./
RUN npm install
COPY apps/office-addin/ ./
# Gateway-Adresse zur Build-Zeit fest einbrennen (kein Server, der es später
# nachladen könnte) - überschreibbar über einen Build-Arg.
ARG NEXT_PUBLIC_GATEWAY_BASE_URL=http://localhost:8009
ENV NEXT_PUBLIC_GATEWAY_BASE_URL=$NEXT_PUBLIC_GATEWAY_BASE_URL
ARG NEXT_PUBLIC_TEMPLATE_LIBRARY_FOLDER_NAME=Vorlagen
ENV NEXT_PUBLIC_TEMPLATE_LIBRARY_FOLDER_NAME=$NEXT_PUBLIC_TEMPLATE_LIBRARY_FOLDER_NAME
RUN npm run build

FROM nginx:1.27-alpine
COPY apps/office-addin/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/out /usr/share/nginx/html
COPY apps/office-addin/manifest.xml /usr/share/nginx/html/manifest.xml
COPY apps/office-addin/assets/ /usr/share/nginx/html/assets/
EXPOSE 80
