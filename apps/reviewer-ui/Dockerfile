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
# Login illustration (P83-S3, ADR 0237): same mirrored-layout principle, the
# relative import in src/components/LoginIllustration.tsx resolves unchanged.
COPY libs/dms-showcase/ libs/dms-showcase/
# Gateway-Adresse zur Build-Zeit fest einbrennen (kein Server, der es später
# nachladen könnte) - überschreibbar über einen Build-Arg.
ARG NEXT_PUBLIC_GATEWAY_BASE_URL=http://localhost:8009
ENV NEXT_PUBLIC_GATEWAY_BASE_URL=$NEXT_PUBLIC_GATEWAY_BASE_URL
RUN npm run build --prefix apps/reviewer-ui
# Components file (P100-S1, ADR 0271): npm production dependencies plus the
# runtime image's Alpine packages (copied in from it), kept in the image for
# scanners; the npm part alone is also served (it ships to browsers anyway).
COPY scripts/frontend-components.mjs scripts/
COPY --from=nginx:1.27-alpine /lib/apk/db/installed /tmp/runtime-apk-installed
COPY --from=nginx:1.27-alpine /etc/os-release /tmp/runtime-os-release
RUN node scripts/frontend-components.mjs reviewer-ui apps/reviewer-ui/package-lock.json /repo/components /tmp/runtime-apk-installed /tmp/runtime-os-release

FROM nginx:1.27-alpine
COPY apps/reviewer-ui/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /repo/apps/reviewer-ui/out /usr/share/nginx/html
COPY --from=build /repo/components/components.cdx.json /app/META-INF/components.cdx.json
COPY --from=build /repo/components/components-npm.cdx.json /usr/share/nginx/html/META-INF/components.cdx.json
LABEL org.opencontainers.image.title="reviewer-ui" \
      dms.components="/app/META-INF/components.cdx.json"
EXPOSE 80
# Internal components port (P100-S3): never published, see nginx.conf.
EXPOSE 8081
