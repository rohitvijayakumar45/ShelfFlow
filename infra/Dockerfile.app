FROM node:22.15.0-alpine AS build
WORKDIR /apps
COPY apps/package*.json ./
COPY apps/customer/package.json customer/package.json
COPY apps/staff/package.json staff/package.json
RUN npm ci
COPY apps ./
ARG APP=customer
RUN cd ${APP} && npm run build
FROM nginx:1.27.5-alpine
ARG APP=customer
COPY --from=build /apps/${APP}/dist /usr/share/nginx/html
COPY infra/nginx.conf /etc/nginx/conf.d/default.conf
