#!/bin/bash
set -e

echo "[STARTUP] ═══════════════════════════════════════════"
echo "[STARTUP]   Liga Fumos — iniciando serviços"
echo "[STARTUP] ═══════════════════════════════════════════"

# Inicia o GC service em background
echo "[STARTUP] Iniciando Dota 2 GC service..."
/app/gc/liga-discord-gc &
GC_PID=$!
echo "[STARTUP] GC service iniciado (PID: $GC_PID)"

# Pequena pausa para o GC logar o banner antes do Python
sleep 1

# Inicia o bot Discord em foreground (Railway monitora este processo)
echo "[STARTUP] Iniciando bot Discord..."
python main.py

# Se o Python sair, mata o GC também
kill $GC_PID 2>/dev/null || true
echo "[STARTUP] Todos os serviços encerrados"
