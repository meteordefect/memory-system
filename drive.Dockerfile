FROM ghcr.io/nyrest/hindsight-ingestion:latest

# The image listens on 8080 as uid 10001. Render assigns $PORT and mounts
# the disk as root, so start as root, hand /data to that user, and listen
# where Render is sending traffic.
USER root
RUN apk add --no-cache su-exec
COPY drive-start.sh /drive-start.sh
RUN chmod 755 /drive-start.sh
HEALTHCHECK NONE
ENTRYPOINT ["/drive-start.sh"]
