# Console app image. No docker CLI needed — the `docker` Python SDK
# talks to the host daemon directly over the mounted socket.
# Run with: -v /var/run/docker.sock:/var/run/docker.sock
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
