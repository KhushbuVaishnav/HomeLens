# Static frontend only (index.html, app.jsx, style.css) -- no build step,
# no package.json, so Cloud Run's buildpacks have nothing to auto-detect
# a runtime from. This Dockerfile uses the same tool the README already
# documents for local dev (`python3 -m http.server`), so the container
# behaves identically to running it on your own machine, just bound to
# whatever port Cloud Run assigns instead of a hardcoded 5500.

FROM python:3.12-slim

WORKDIR /app
COPY frontend/ .

# Cloud Run sets PORT itself (usually 8080) and requires the container to
# listen on whatever value that is -- 8080 here is only the default for
# running this image somewhere that doesn't set PORT.
ENV PORT=8080
EXPOSE 8080

# Shell form (no brackets), not exec form -- needed so ${PORT} actually
# gets substituted with Cloud Run's real value at container start.
CMD python3 -m http.server ${PORT} --bind 0.0.0.0
