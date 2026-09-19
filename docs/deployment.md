# Instalação e deploy

O Signal é uma aplicação pessoal de usuário único. O caminho recomendado para uma máquina remota é
Docker Compose com a imagem publicada no GitHub Container Registry. A imagem suporta Linux AMD64 e
ARM64; o banco permanece acessível apenas na rede interna do Compose.

## Docker Compose

```bash
git clone https://github.com/lfnovo/signal.git
cd signal
cp .env.example .env
```

Edite `.env` antes de iniciar. No mínimo:

```dotenv
GEMINI_API_KEY=your-provider-key
SIGNAL_PASSWORD=use-a-long-random-password
SIGNAL_API_URL=https://signal.example.com
SIGNAL_ALLOWED_HOSTS=signal.example.com
SIGNAL_DB_USER=root
SIGNAL_DB_PASSWORD=replace-this-database-password
```

Suba os serviços:

```bash
docker compose pull
docker compose up -d
docker compose ps
docker compose logs -f signal
```

O Compose cria `signal-data` para uploads e a chave de sessão, e `signal-database` para o SurrealDB.
Não exponha a porta do banco. A porta HTTP do Signal é `8020`; defina `SIGNAL_PORT` antes do comando
se precisar publicar outra porta local.

Para fixar uma versão em vez de acompanhar `latest`:

```bash
SIGNAL_IMAGE=ghcr.io/lfnovo/signal:0.1.0 docker compose up -d
```

## HTTPS

Coloque Caddy, nginx, Traefik ou outro proxy reverso na frente de `127.0.0.1:8020`. O proxy deve
preservar `Host`, `X-Forwarded-Proto` e conexões HTTP de longa duração usadas pelo Streamable HTTP.
`SIGNAL_API_URL` precisa ser exatamente a origem HTTPS vista pelo navegador e pelos clientes MCP.
Inclua o hostname em `SIGNAL_ALLOWED_HOSTS`.

O cookie de sessão recebe `Secure` quando `SIGNAL_API_URL` usa HTTPS. A senha humana autentica a
interface e aprova OAuth; clientes MCP recebem tokens individuais e revogáveis em **Connections**.

## Atualização

Para uma imagem estável:

```bash
docker compose pull signal
docker compose up -d signal
docker image prune
```

Leia as notas da release antes de mudar de versão. O schema do banco é aplicado de forma idempotente
na inicialização. Não execute mais de uma réplica do Signal contra os mesmos volumes: a fila usa um
lock de arquivo compartilhado e a aplicação foi projetada para um único processo consumidor.

## Backup

Pare os containers antes de copiar os volumes para obter um snapshot consistente:

```bash
docker compose stop
docker run --rm -v signal_signal-data:/source -v "$PWD/backups":/backup \
  alpine tar czf /backup/signal-data.tar.gz -C /source .
docker run --rm -v signal_signal-database:/source -v "$PWD/backups":/backup \
  alpine tar czf /backup/signal-database.tar.gz -C /source .
docker compose start
```

O nome real do volume inclui o nome do projeto Compose; confirme com `docker volume ls`. Proteja os
backups como dados pessoais: eles contêm fontes, conversas, tokens OAuth e a chave de sessão.

## Instalação pelo código

Use este caminho para desenvolvimento ou quando Docker não estiver disponível:

```bash
git clone https://github.com/lfnovo/signal.git
cd signal
uv sync --frozen --group dev
cp .env.example .env
uv run signal serve
```

O host precisa de Python 3.13, uv, SurrealDB 3 e FFmpeg. Para manter o processo após logout, configure
um serviço do sistema com o diretório do checkout como `WorkingDirectory` e execute
`uv run --frozen signal serve`. CLI, servidor e worker precisam compartilhar `.env` e
`SIGNAL_DATA_DIR`.

## MCP

Clientes remotos usam `https://signal.example.com/mcp` e seguem a aprovação OAuth no navegador.
Agentes locais também podem iniciar o transporte stdio com:

```bash
uv run --project /path/to/signal --frozen signal mcp
```
