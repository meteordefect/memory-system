FROM ghcr.io/vectorize-io/hindsight:latest

# The official image runs as UID 1000. Render's disk is a host mount owned
# by root, so start as root, hand the data directory to that user, then drop.
USER root
COPY render-start.sh /render-start.sh
RUN chmod 755 /render-start.sh

ENTRYPOINT ["/render-start.sh"]
CMD ["run"]
