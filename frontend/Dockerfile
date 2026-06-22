# Build stage
# Using the glibc-based "slim" image (not alpine) for the build stage.
# Vite 5 / Rollup 4 has a known npm bug resolving the correct
# platform-specific optional binary (@rollup/rollup-linux-x64-musl)
# on Alpine, which silently breaks the build or produces an empty
# dist/. node:20-slim avoids this entirely.
FROM node:20-slim AS build
WORKDIR /app

COPY package.json ./
RUN npm install

COPY . .
RUN npm run build

# Fail the build loudly if Vite didn't actually produce output,
# instead of shipping an empty nginx image that "starts" but serves nothing.
RUN test -f dist/index.html || (echo "BUILD FAILED: dist/index.html missing" && exit 1)

# Serve stage
FROM nginx:1.27-alpine

# curl is needed for the docker-compose healthcheck -- the base
# nginx:alpine image does not include wget or curl by default.
RUN apk add --no-cache curl

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
