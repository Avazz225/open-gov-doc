# syntax=docker/dockerfile:1
# Zweistufig: Node nur zur Build-Zeit (Next.js als Build-Tooling, Konzept 8),
# das Laufzeit-Image enthält keinen Node-Prozess - reiner statischer Export,
# ausgeliefert über nginx. Das Repo-Layout (apps/process-designer/,
# libs/dms-ui/) wird im Build-Stage 1:1 gespiegelt (Phase 49 Session 1,
# ADR 0168) - der relative `@import` in globals.css zur geteilten
# libs/dms-ui/tokens.css funktioniert damit identisch zu einem lokalen
# `npm run build`, ohne Sonderfall-Pfade.
FROM node:22-alpine AS build
WORKDIR /repo
COPY apps/process-designer/package.json apps/process-designer/package-lock.json* apps/process-designer/
RUN npm install --prefix apps/process-designer
COPY apps/process-designer/ apps/process-designer/
COPY libs/dms-ui/ libs/dms-ui/
# Gateway-Adresse zur Build-Zeit fest einbrennen (kein Server, der es später
# nachladen könnte) - überschreibbar über einen Build-Arg.
ARG NEXT_PUBLIC_GATEWAY_BASE_URL=http://localhost:8009
ENV NEXT_PUBLIC_GATEWAY_BASE_URL=$NEXT_PUBLIC_GATEWAY_BASE_URL
RUN npm run build --prefix apps/process-designer

FROM nginx:1.27-alpine
COPY apps/process-designer/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /repo/apps/process-designer/out /usr/share/nginx/html
EXPOSE 80
