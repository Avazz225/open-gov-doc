# syntax=docker/dockerfile:1
# Zweistufig: Node nur zur Build-Zeit (Next.js als Build-Tooling, Konzept 8),
# das Laufzeit-Image enthält keinen Node-Prozess - reiner statischer Export,
# ausgeliefert über nginx. `manifest.xml`/`assets/` sind KEIN Next.js-Build-
# Ausgang - werden separat ins fertige Image kopiert. Das Repo-Layout
# (apps/office-addin/, libs/dms-ui/) wird im Build-Stage 1:1 gespiegelt
# (Phase 49 Session 1, ADR 0168) - der relative `@import` in globals.css zur
# geteilten libs/dms-ui/tokens.css funktioniert damit identisch zu einem
# lokalen `npm run build`, ohne Sonderfall-Pfade.
FROM node:22-alpine AS build
WORKDIR /repo
COPY apps/office-addin/package.json apps/office-addin/package-lock.json* apps/office-addin/
RUN npm install --prefix apps/office-addin
COPY apps/office-addin/ apps/office-addin/
COPY libs/dms-ui/ libs/dms-ui/
# Gateway-Adresse zur Build-Zeit fest einbrennen (kein Server, der es später
# nachladen könnte) - überschreibbar über einen Build-Arg.
ARG NEXT_PUBLIC_GATEWAY_BASE_URL=http://localhost:8009
ENV NEXT_PUBLIC_GATEWAY_BASE_URL=$NEXT_PUBLIC_GATEWAY_BASE_URL
ARG NEXT_PUBLIC_TEMPLATE_LIBRARY_FOLDER_NAME=Vorlagen
ENV NEXT_PUBLIC_TEMPLATE_LIBRARY_FOLDER_NAME=$NEXT_PUBLIC_TEMPLATE_LIBRARY_FOLDER_NAME
RUN npm run build --prefix apps/office-addin
# Components file (P100-S1, ADR 0271): npm production dependencies plus the
# runtime image's Alpine packages (copied in from it), kept in the image for
# scanners; the npm part alone is also served (it ships to browsers anyway).
COPY scripts/frontend-components.mjs scripts/
COPY --from=nginx:1.27-alpine /lib/apk/db/installed /tmp/runtime-apk-installed
RUN node scripts/frontend-components.mjs office-addin apps/office-addin/package-lock.json /repo/components /tmp/runtime-apk-installed

FROM nginx:1.27-alpine
COPY apps/office-addin/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /repo/apps/office-addin/out /usr/share/nginx/html
COPY --from=build /repo/components/components.cdx.json /app/META-INF/components.cdx.json
COPY --from=build /repo/components/components-npm.cdx.json /usr/share/nginx/html/META-INF/components.cdx.json
LABEL org.opencontainers.image.title="office-addin" \
      dms.components="/app/META-INF/components.cdx.json"
COPY apps/office-addin/manifest.xml /usr/share/nginx/html/manifest.xml
COPY apps/office-addin/assets/ /usr/share/nginx/html/assets/
EXPOSE 80
