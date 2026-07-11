# Container image for the AI Voice Presenter.
# Build:  docker build -t ai-voice-presenter .
# Run:    docker run -p 8000:8000 --env-file .env ai-voice-presenter
FROM python:3.12-slim

WORKDIR /app

# Install deps first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# Listen on all interfaces; PaaS hosts inject PORT.
ENV HOST=0.0.0.0 \
    PORT=8000
EXPOSE 8000

CMD ["python", "-u", "backend/main.py"]
