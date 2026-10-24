FROM node:24-alpine AS build
WORKDIR /build
COPY console/package*.json ./
RUN npm ci
COPY console .
RUN npm run build
FROM nginx:1.28-alpine
COPY --from=build /build/dist /usr/share/nginx/html
COPY deploy/v2/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8080
