FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-gc.txt ./

# Instala dependências principais do bot
RUN pip install --no-cache-dir -r requirements.txt

# steam==1.4.4 tem constraint quebrada: requer dota2>=0.4,<1, mas dota2 pulou de 0.3.3→1.0.0
# Solução: instalar deps de steam/dota2 manualmente e ambos com --no-deps
RUN pip install --no-cache-dir eventemitter vdf gevent protobuf six cachetools
RUN pip install --no-cache-dir --no-deps steam dota2

COPY . .

CMD ["python", "main.py"]
