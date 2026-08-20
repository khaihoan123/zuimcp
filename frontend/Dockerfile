# syntax=docker/dockerfile:1

# The build stage keeps devDependencies and TypeScript source, so it doubles as
# the image the Better Auth migration CLI runs in (see compose.yaml).
FROM node:22-bookworm-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:22-bookworm-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production
# adapter-node leaves `dependencies` external, so they're installed here.
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
COPY --from=build /app/build ./build

EXPOSE 3000
CMD ["node", "build"]
