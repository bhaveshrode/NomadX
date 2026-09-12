FROM nginx:1.25-alpine

# Install openssl to generate self-signed certs for local validation
RUN apk add --no-cache openssl

# Generate self-signed certificate on build
RUN mkdir -p /etc/ssl/private /etc/ssl/certs && \
    openssl req -x509 -nodes -days 365 \
    -subj "/C=US/ST=Staging/L=Staging/O=NomadX/CN=localhost" \
    -newkey rsa:2048 \
    -keyout /etc/ssl/private/nginx-selfsigned.key \
    -out /etc/ssl/certs/nginx-selfsigned.crt

# Expose HTTP and HTTPS ports
EXPOSE 80 443
