FROM ubuntu:26.04

WORKDIR /app

# Impede que o apt-get trave aguardando interações do usuário (ex: timezone)
ENV DEBIAN_FRONTEND=noninteractive

# Instala dependências básicas, Python 3, Pip, repositório Postgres e os clientes de DB
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    gnupg \
    lsb-release \
    curl \
    ca-certificates \
    gcc \
    libpq-dev \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor -o /etc/apt/trusted.gpg.d/postgresql.gpg \
    && echo "deb http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client-18 \
    default-mysql-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Utiliza a flag --break-system-packages para permitir a instalação no sistema global do container
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

COPY . .

EXPOSE 5000

# Altera a chamada para 'python3' pois é o padrão no Ubuntu
CMD ["python3", "app.py"]