FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-gc.txt ./

# Instala dependências principais do bot
RUN pip install --no-cache-dir -r requirements.txt

# Instala steam e dota2 com --no-deps para contornar a constraint quebrada:
# steam==1.4.4 declara dota2>=0.4,<1, mas dota2 pulou de 0.3.3 direto para 1.0.0
RUN pip install --no-cache-dir --no-deps dota2 steam

# Instala as dependências comuns manualmente
RUN pip install --no-cache-dir vdf gevent protobuf six

COPY . .

CMD ["python", "main.py"]
