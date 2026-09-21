# syntax=docker/dockerfile:1
# Zweistufig: Node nur zur Build-Zeit (Next.js als Build-Tooling, Konzept 8),
# das Laufzeit-Image enthält keinen Node-Prozess - reiner statischer Export,
# ausgeliefert über nginx. Identisches Muster wie apps/user-ui, siehe ADR 0006.
# Das Repo-Layout (apps/admin-ui/, libs/dms-ui/) wird im Build-Stage 1:1
# gespiegelt (Phase 49 Session 3, ADR 0168) - der relative `@import` in
# globals.css zur geteilten libs/dms-ui/tokens.css funktioniert damit
# identisch zu einem lokalen `npm run build`, ohne Sonderfall-Pfade.
FROM node:22-alpine AS build
WORKDIR /repo
COPY apps/admin-ui/package.json apps/admin-ui/package-lock.json* apps/admin-ui/
RUN npm install --prefix apps/admin-ui
COPY apps/admin-ui/ apps/admin-ui/
COPY libs/dms-ui/ libs/dms-ui/
ARG NEXT_PUBLIC_GATEWAY_BASE_URL=http://localhost:8009
ENV NEXT_PUBLIC_GATEWAY_BASE_URL=$NEXT_PUBLIC_GATEWAY_BASE_URL
# Federation Hub (Phase 40 Session 3) - reached directly, not through the
# gateway above, see lib/config.ts.
ARG NEXT_PUBLIC_FEDERATION_HUB_BASE_URL=http://localhost:8018
ENV NEXT_PUBLIC_FEDERATION_HUB_BASE_URL=$NEXT_PUBLIC_FEDERATION_HUB_BASE_URL
# Fleet Management (P67-S1) - reached directly, same reasoning as
# Federation Hub above, see lib/config.ts.
ARG NEXT_PUBLIC_FLEET_MANAGEMENT_SERVICE_BASE_URL=http://localhost:8031
ENV NEXT_PUBLIC_FLEET_MANAGEMENT_SERVICE_BASE_URL=$NEXT_PUBLIC_FLEET_MANAGEMENT_SERVICE_BASE_URL
RUN npm run build --prefix apps/admin-ui

FROM nginx:1.27-alpine
COPY apps/admin-ui/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /repo/apps/admin-ui/out /usr/share/nginx/html
EXPOSE 80
