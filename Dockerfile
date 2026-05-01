# ── Stage 1: Build Go binary ────────────────────────────────
FROM golang:1.22-alpine AS go-builder

WORKDIR /build

COPY gc/go.mod gc/go.sum ./
RUN go mod download

COPY gc/ ./
RUN CGO_ENABLED=0 GOOS=linux go build -o liga-discord-gc ./cmd/

# ── Stage 2: Python + Go runtime ────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application source
COPY . .

# Go binary
COPY --from=go-builder /build/liga-discord-gc ./gc/liga-discord-gc

# Startup script
RUN chmod +x start.sh gc/liga-discord-gc

CMD ["./start.sh"]
