# Palladium, as one image.
#
# Everything it writes lives in /data, which is a volume: settings, the library index,
# invitations, logs. The films are mounted read-only wherever the person keeps them.
# Nothing is installed on the machine but this.
FROM python:3.13-slim

# ffmpeg does the playing that a device cannot do for itself. Nothing else is needed:
# the server is standard library only.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# Every module, by the shape of its name rather than one by one. The list was
# written out in full and fell behind the server twice: a module added after it
# was written is imported by name at startup, and the container then restarts for
# ever saying only which one it could not find.
COPY pd-server.py pd_*.py changes.json ./
COPY static/ ./static/

# A user of its own rather than root: the films are mounted read-only, but a media
# server reachable from a phone should not be the machine's administrator.
RUN useradd --system --uid 1000 --create-home palladium \
    && mkdir -p /data && chown -R palladium:palladium /data /app
USER palladium

# This container can follow another Palladium and keep copies of what is watched
# there: Settings, This computer, Another server. Point "Keep copies in" at a folder
# under /data, or mount a disk of its own and point it there.
VOLUME ["/data"]
EXPOSE 8765

# A container that is up but not answering is worth knowing about: /health is served
# before anything is set up, so this is true from the first second.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3   CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=4).status == 200 else 1)"

# --root keeps its papers on the volume; --no-open because there is no browser here.
CMD ["python", "-u", "pd-server.py", "--no-open", "--root", "/data"]
