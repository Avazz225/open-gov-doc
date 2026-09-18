# syntax=docker/dockerfile:1
# Zweistufig: Node nur zur Build-Zeit (Next.js als Build-Tooling, Konzept 8),
# das Laufzeit-Image enthält keinen Node-Prozess - reiner statischer Export,
# ausgeliefert über nginx. Das Repo-Layout (apps/reviewer-ui/, libs/dms-ui/)
# wird im Build-Stage 1:1 gespiegelt (Phase 49 Session 1, ADR 0168) - der
# relative `@import` in globals.css zur geteilten libs/dms-ui/tokens.css
# funktioniert damit identisch zu einem lokalen `npm run build`, ohne
# Sonderfall-Pfade.
FROM node:22-alpine AS build
WORKDIR /repo
COPY apps/reviewer-ui/package.json apps/reviewer-ui/package-lock.json* apps/reviewer-ui/
RUN npm install --prefix apps/reviewer-ui
COPY apps/reviewer-ui/ apps/reviewer-ui/
COPY libs/dms-ui/ libs/dms-ui/
# Gateway-Adresse zur Build-Zeit fest einbrennen (kein Server, der es später
# nachladen könnte) - überschreibbar über einen Build-Arg.
ARG NEXT_PUBLIC_GATEWAY_BASE_URL=http://localhost:8009
ENV NEXT_PUBLIC_GATEWAY_BASE_URL=$NEXT_PUBLIC_GATEWAY_BASE_URL
RUN npm run build --prefix apps/reviewer-ui

FROM nginx:1.27-alpine
COPY apps/reviewer-ui/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /repo/apps/reviewer-ui/out /usr/share/nginx/html
EXPOSE 80
