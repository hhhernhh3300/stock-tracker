# --------------------------------------------------------------------------- #
# Single-service image for the Analyst Agent.
#
# Stage 1 builds the React/Vite frontend.
# Stage 2 runs the FastAPI backend, which also serves the built frontend, so the
# whole app lives behind ONE URL (no CORS, no second service to deploy).
#
# Build locally:   docker build -t analyst-agent .
# Run locally:     docker run --rm -p 8000:8000 --env-file backend/.env analyst-agent
# Then open:       http://localhost:8000
# --------------------------------------------------------------------------- #

# ---- Stage 1: build the frontend -------------------------------------------
FROM node:20-slim AS frontend

WORKDIR /frontend

# Same-origin: the frontend calls /api/... on the same host, so no API URL needed.
ENV VITE_API_URL=""

# Install deps first for better layer caching.
COPY frontend/package*.json ./
RUN npm install

# Build the static site into /frontend/dist
COPY frontend/ ./
RUN npm run build


# ---- Stage 2: the Python API + static frontend -----------------------------
FROM python:3.12-slim AS runtime

# Faster, quieter, no .pyc clutter
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install Python deps first for better layer caching.
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# Copy the backend source.
COPY backend/ ./backend/

# Copy the built frontend from stage 1 to where main.py expects it.
COPY --from=frontend /frontend/dist ./frontend/dist

# main.py looks here for index.html (overridable).
ENV FRONTEND_DIST=/app/frontend/dist

# Hosts like Render/Railway/Fly set $PORT; default to 8000 locally.
ENV PORT=8000
EXPOSE 8000

WORKDIR /app/backend

# Use shell form so ${PORT} is expanded at runtime.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
