# Verificação

## Testes locais

Instale o grupo de desenvolvimento e execute as verificações que também rodam no CI:

```bash
uv sync --frozen --group dev
uv run ruff check src tests
uv run ruff format --check src tests
uv run pytest
node --test tests/*.test.cjs
uv build
docker build -t signal:dev .
```

A suíte Python padrão não precisa de banco ou credenciais de IA. Ela cobre intake, extração simulada,
fila, busca, triagem, tópicos, contexto, renderização segura, autenticação e o catálogo MCP. Os testes
Node executam os módulos JavaScript da interface e da extensão com APIs do navegador simuladas.

## Integração com SurrealDB

Com um SurrealDB 3 acessível pelas variáveis `SIGNAL_DB_*`:

```bash
SIGNAL_INTEGRATION=1 uv run pytest
```

Os testes criam bancos temporários `signal_test_*` no namespace configurado e os removem ao final.
Eles não devem alterar o database `signal` nem chamar modelos pagos. A integração cobre concorrência,
transações, persistência, upload HTTP, OAuth completo com PKCE, refresh/revogação de token e uma
chamada MCP autenticada.

Para exercitar a extensão contra uma instância descartável em `8020`:

```bash
SIGNAL_LIVE_EXTENSION=1 node --test tests/extension.test.cjs tests/frontend.test.cjs
```

Esse teste cria uma captura real no database configurado; não o execute contra a biblioteca pessoal
se você não quiser adicionar a fixture.

## CI e releases

`.github/workflows/ci.yml` roda em pull requests e em mudanças de `main`. Ele verifica estilo, testes
Python e Node, constrói wheel/sdist e confirma que o Dockerfile compila.

`.github/workflows/release.yml` roda somente quando uma GitHub Release é publicada. Ele autentica no
GHCR com o `GITHUB_TOKEN`, constrói `linux/amd64` e `linux/arm64`, inclui SBOM e provenance e publica:

- `ghcr.io/lfnovo/signal:<versão completa>`
- `ghcr.io/lfnovo/signal:<major>.<minor>`
- `ghcr.io/lfnovo/signal:latest` para releases estáveis

Pre-releases não atualizam `latest`. A publicação exige que GitHub Actions tenha permissão de escrita
em packages, já declarada no job. Depois da primeira publicação, confirme que a visibilidade do pacote
GHCR está pública nas configurações do pacote.

## Smoke test da imagem

Use um arquivo `.env` de teste com senha e provider, então:

```bash
docker compose build signal
docker compose up -d
curl --fail http://127.0.0.1:8020/api/health
docker compose ps
docker compose down
```

O health check só confirma aplicação e banco. Para validar uma release, faça login, capture uma URL,
espere o estado **Ready**, converse com a fonte e conecte um cliente MCP remoto até listar as tools.

## Conteúdo dos artefatos

Inspecione o pacote ao alterar configuração do Hatch:

```bash
uv build
unzip -l dist/*.whl
tar tzf dist/*.tar.gz
```

Prompts, templates, arquivos estáticos e `schemas.surrealql` precisam estar no wheel. `.env`, `.signal`,
caches, configurações locais e dados pessoais nunca podem aparecer nos artefatos ou na imagem.
