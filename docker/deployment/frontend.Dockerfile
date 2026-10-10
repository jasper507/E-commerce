FROM node:24-bookworm-slim AS build

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

FROM nginx:stable-alpine

COPY --from=build /app/frontend/dist/ /usr/share/nginx/html/
COPY docker/deployment/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
