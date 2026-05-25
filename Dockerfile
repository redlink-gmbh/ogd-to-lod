# Image for running the ogd-to-lod CLI.
#
# The app shells out to `docker run` (yarrrml-parser, RMLMapper), so the
# image ships the Docker CLI and is expected to be run with the host's
# Docker socket bind-mounted (see docker-compose.yml). It does NOT run a
# nested Docker daemon (no docker-in-docker).

FROM python:3.11-slim

# Bring in the Docker CLI (no daemon) from the official image so the app
# can spawn sibling containers via the host daemon mounted at
# /var/run/docker.sock. This is more reliable than the Debian `docker.io`
# package on the slim base.
COPY --from=docker:27-cli /usr/local/bin/docker /usr/local/bin/docker

# curl for healthchecks / smoke scripts, bash for the helper scripts
# under tests/e2e, ca-certificates so curl can reach HTTPS endpoints.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        bash \
 && rm -rf /var/lib/apt/lists/*

# Install the package. Editable install so a bind-mounted source tree
# picks up local edits without rebuilding the image.
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir -e .

# Default to launching the CLI; override with `docker compose run … bash`
# for an interactive shell.
ENTRYPOINT ["ogd-to-lod"]
CMD ["--help"]
