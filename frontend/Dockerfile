FROM node:22-alpine AS build
WORKDIR /app
# pnpm-workspace.yaml belongs in THIS copy, not in the `COPY . .` below: it carries the allowBuilds
# list, and pnpm reads it during install. Copied later, the settings arrive after the step that
# needed them, and the install dies with ERR_PNPM_IGNORED_BUILDS on any machine that has not
# approved esbuild's build script before — i.e. every clean one.
COPY package.json pnpm-lock.yaml* pnpm-workspace.yaml ./
RUN corepack enable && pnpm install --frozen-lockfile
COPY . .

# The canvas builder is behind a build-time flag (src/config.ts). Without this ARG the image could
# only ever ship it OFF: Vite inlines import.meta.env at build time, so a runtime variable on the
# container changes nothing, and there was no way to turn the feature on in a Docker install at all.
ARG VITE_BUILDER_ENABLED=0
ENV VITE_BUILDER_ENABLED=$VITE_BUILDER_ENABLED
RUN pnpm build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
