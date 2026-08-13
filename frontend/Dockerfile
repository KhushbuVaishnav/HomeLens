# Static frontend only (index.html, app.jsx, style.css) -- no build step,
# no package.json, so Cloud Run's buildpacks have nothing to auto-detect
# a runtime from. This Dockerfile uses the same tool the README already
# documents for local dev (`python3 -m http.server`), so the container
# behaves identically to running it on your own machine, just bound to
# whatever port Cloud Run assigns instead of a hardcoded 5500.
#
# COPY here is `. .`, not `frontend/ .` -- deliberately different from
# this same file on the main branch. The build CONTEXT differs between
# the two projects' Cloud Build triggers (visible in each trigger's
# generated cloudbuild.yaml, the `build` step's args right before `-f`):
# main project's frontend trigger uses context `.` (repo root), so its
# Dockerfile needs `COPY frontend/ .` to reach in and grab just this
# folder. This vertex-test project's trigger was configured with context
# already scoped to `frontend` itself -- so from inside THIS Dockerfile,
# `.` already IS the frontend folder; `COPY frontend/ .` would look for a
# frontend/frontend/ that doesn't exist, exactly the error this replaced.

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
