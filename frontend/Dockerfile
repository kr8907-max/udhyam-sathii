FROM node:20-alpine as builder

WORKDIR /app

COPY package*.json /app/
RUN npm ci || npm install

COPY . /app/

RUN npm run build

FROM nginx:alpine as runner

RUN rm -rf /usr/share/nginx/html/*

COPY --from=builder /app/dist /usr/share/nginx/html

COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80 8080

CMD ["/bin/sh", "-c", "sed -i \"s/listen 80;/listen ${PORT:-80};/g\" /etc/nginx/conf.d/default.conf && nginx -g 'daemon off;'"]
