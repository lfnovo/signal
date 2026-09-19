# API e MCP

Base padrão: `http://127.0.0.1:8020`. Sem `SIGNAL_PASSWORD`, o servidor só pode escutar no loopback. Com senha, a interface e a API web usam uma sessão HTTP; o health check, os arquivos estáticos e os endpoints públicos do OAuth permanecem acessíveis conforme necessário.

| Método | Rota | Uso |
| --- | --- | --- |
| GET | `/api/health` | Verifica aplicação e banco |
| GET | `/api/sources?offset=0&limit=100` | Lista por data decrescente, sem conteúdo/vetores |
| POST | `/api/sources` | Captura URL: `{"url":"https://example.com"}` |
| POST | `/api/sources/upload` | Multipart com campo `file` |
| GET | `/api/sources/{id}` | Fonte com conteúdo e resumo, sem vetores/caminho interno |
| POST | `/api/sources/{id}/retry` | Retoma uma fonte em erro |
| GET | `/api/sources/{id}/chat` | Histórico completo, com evidências |
| POST | `/api/sources/{id}/chat` | Pergunta: `{"question":"Qual é a ideia principal?"}` |
| DELETE | `/api/sources/{id}/chat` | Limpa o histórico desta fonte |
| GET | `/api/preferences` | Idioma e modelos configurados, sem chaves |
| PUT | `/api/preferences` | Salva idioma e/ou provider/modelo; atualizações parciais preservam outros campos |
| GET | `/api/models?provider=google&kind=embedding` | Busca sugestões de modelos do provider; `kind` aceita `language` ou `embedding` |

Capturas retornam `202` e `{"source":{...},"created":true}`. Uma duplicata retorna a fonte existente com `created:false`; não é reprocessada. O ID exposto é a parte hexadecimal do registro `source:<id>`.

```bash
curl http://127.0.0.1:8020/api/sources \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com"}'

curl http://127.0.0.1:8020/api/sources/upload \
  -F 'file=@/caminho/arquivo.pdf'
```

O chat responde quando o modelo conclui, sem streaming. O turno é persistido antes da resposta HTTP. Requisições de chat/limpeza por fonte são serializadas no processo do servidor. Perguntas podem ter até 4.000 caracteres. A fonte precisa estar em `ready`.

Erros usam `detail` em JSON: `400` para entrada inválida, `404` para fonte ausente, `409` para estado incompatível, `413` para upload acima do limite declarado, `422` para validação do corpo e `502` para falha no provider de chat. O arquivo também é contado durante a cópia; nesse caso o limite resulta em `400`. O HTML fica em `/` e `/sources/{id}`; o schema OpenAPI em JSON é exposto em `/openapi.json`.

## MCP para agentes

O transporte remoto usa Streamable HTTP em `/mcp`. Clientes descobrem os metadados OAuth no mesmo host, registram uma conexão e abrem o navegador para aprovação. O usuário entra com `SIGNAL_PASSWORD`; o agente recebe tokens próprios de curta duração e um refresh token, sem receber a senha. **Connections** (`/connections`) lista e revoga cada cliente. A origem pública em `SIGNAL_API_URL` deve coincidir com a URL HTTPS usada pelo cliente.

Para agentes executados na mesma máquina, `uv run signal mcp` inicia o mesmo servidor via stdio. Nesse transporte, o processo local já representa a autorização do usuário e não abre o fluxo OAuth.

Tools disponíveis: status, captura de URL/arquivo, listagem e busca, leitura e conversa com fonte, movimentação Inbox/Library, Focus, renomeação, leitura de tópicos e atualização de contexto. A primeira versão não expõe exclusão permanente. Resources JSON:

- `signal://sources/{identifier}`
- `signal://sources/{identifier}/preview`
- `signal://topics/{identifier}`
- `signal://views/{view}`, com `view` igual a `inbox`, `library` ou `focus`

## Preferências de IA

`GET /api/preferences` retorna os valores efetivos. `PUT /api/preferences` aceita `language`, `llm_provider`, `llm_model`, `embedding_provider` e `embedding_model`. Valores vazios e campos extras (inclusive chaves de API) são rejeitados. Ao trocar o provider, envie também o modelo correspondente. A mudança não precisa de reinício e não modifica fontes existentes.

```json
{
  "language": "Português",
  "llm_provider": "google",
  "llm_model": "gemini-2.5-flash",
  "embedding_provider": "google",
  "embedding_model": "gemini-embedding-001"
}
```

A descoberta retorna `{"models":["model-id", "another-id"]}`. Ela é opcional: falhas de catálogo não impedem informar um model ID manualmente. Mensagens de erro da interface/API são em inglês, independentemente do idioma dos resumos.

## Topics e regeneração

- `GET /api/topics`: vocabulário global, status `official` e IDs das fontes associadas.
- `POST /api/topics` com `{ "name": "Conhecimento" }`: cria um tópico oficial (ou reutiliza um nome equivalente).
- `PUT /api/topics/{id}` com `{ "name": "Novo nome" }`: renomeia globalmente.
- `POST /api/topics/{id}/approve`: torna oficial globalmente.
- `POST /api/topics/{id}/merge` com `{ "target": "id-do-destino" }`: move associações, unifica duplicatas e mantém o destino; se qualquer tópico era oficial, o destino fica oficial.
- `DELETE /api/topics/{id}`: remove o tópico da navegação e suas associações, preservando todas as fontes.
- `POST /api/sources/{id}/topics` com `{ "name": "Novo tópico" }`: cria/reutiliza um tópico oficial e o associa manualmente.
- `PUT /api/sources/{id}/topics/{topic_id}`: associa ou marca como escolha manual.
- `DELETE /api/sources/{id}/topics/{topic_id}`: remove a associação e impede que sugestões futuras a recriem automaticamente.
- `POST /api/sources/{id}/regenerate` com `{ "steering": "Foque nas aplicações práticas" }`: enfileira nova geração conjunta de resumo e tópicos, sem extrair novamente. O campo `steering` é opcional, com limite de 2.000 caracteres. Retorna 202, ou 409 se há outro job ou a fonte não tem conteúdo/vetores prontos.

`/topics` lista oficiais e sugeridos. `/topics/{id}` lista fontes por data, com paginação de 50 itens. Renomear e criar usam normalização Unicode e espaços; nomes equivalentes ignorando caixa não duplicam tópicos.

## Triagem e exclusão de fontes

- `GET /library`: conteúdos aceitos, com paginação. `/` mostra apenas Inbox.
- `GET /api/sources?collection=inbox|library`: filtro opcional. Sem filtro, mantém a listagem de todas as fontes. As linhas incluem `collection` e `topics`.
- `PUT /api/sources/{id}/collection` com `{ "collection": "library" }` ou `{ "collection": "inbox" }`: move sem alterar conteúdo e estado de processamento.
- `DELETE /api/sources/{id}` com `{ "confirm": true }`: exclusão definitiva, incluindo conversa, associações e cópia do arquivo. Sem confirmação retorna 422; fonte ausente retorna 404.

Fontes legadas sem `collection` são tratadas como Inbox. As páginas de tópicos não filtram a coleção.

## Focus

- `GET /focus`: página de fontes marcadas, de ambas as coleções, com paginação.
- `PUT /api/sources/{id}/focus` com `{ "focused": true }` ou `{ "focused": false }`: define a marca sem mudar a coleção.
- `GET /api/sources?focused=true`: filtra marcados; pode combinar com `collection=inbox|library`.

`GET /api/sources/{id}/preview` retorna título, tipo (`summary` ou `content`), HTML Markdown seguro e indicador de truncamento. Prefere o resumo; caso ausente, usa até 16.000 caracteres do conteúdo extraído. Não envia vetores, arquivo ou draft de processamento.

## Busca

- `GET /search`: página com `q`, `mode=hybrid|keywords|semantic`, `collection=inbox|library`, `focused=true`, `kind=url|file`, `topic={id}` e `page`.
- `GET /api/search`: mesmos parâmetros; retorna `sources`, `topics`, `total`, `has_next` e `warnings`. Queries têm limite de 500 caracteres; páginas começam em 1.
- `GET /api/search/suggest?q=...`: sugestões apenas textuais, até cinco fontes e cinco tópicos; não chama providers.

Os resultados não incluem embeddings, arquivos, drafts ou histórico de chat. A fonte contém um trecho em `summary` e os campos `match_type`/`match_label`; o resumo completo continua na API de prévia/fonte.

A prévia inclui `youtube_video_id` (ou `null`) para links de vídeos do YouTube. O player aparece acima do texto, sem autoplay, e é removido ao fechar ou trocar de prévia. Aceita watch, youtu.be, shorts, live e embed.

## Contexto e conexões de tópicos

`GET /api/topics/workspace?include_inbox=false` retorna `topics`, `edges`, `sources` e `scope`. A Library é o escopo padrão; `include_inbox=true` inclui capturas ainda não triadas. Focus é independente: só entra no padrão se o conteúdo também estiver na Library. Tópicos sem conteúdos no escopo continuam no catálogo, com `count: 0`.

Cada tópico inclui `definition` (o que pertence ao tópico), `personal_context` (interesses e perguntas do usuário), `count`, `source_ids` e `last_activity` (data de captura mais recente no escopo). Campos de contexto ausentes em registros antigos equivalem a texto vazio. `PUT /api/topics/{id}/context` salva os dois campos e `context_updated_at`, sem alterar a aprovação global. Limites: 12.000 caracteres de definição e 24.000 de contexto pessoal. Campos extras são rejeitados. Merge preserva os dois contextos, identificando o nome do tópico de origem.

Uma aresta tem `source` e `target` (IDs de tópicos), `kind: shared_sources`, `shared_count`, `source_ids` e `strength`. Para conjuntos A e B de fontes, `strength = |A ∩ B| / sqrt(|A| × |B|)`. Cada fonte conta uma vez; associações removidas, tópicos apagados e fontes inexistentes não participam. A porcentagem apresentada é força normalizada, não probabilidade. O grafo visual mostra até dez vizinhos mais fortes; a lista contém todas as conexões.

`GET /api/topics/{id}?include_inbox=false` é uma leitura compacta para clientes e agentes: retorna o tópico com seu contexto, as relações e a lista de fontes no escopo. Uma ligação é evidência de coocorrência no acervo, não uma relação conceitual validada. Para inspecionar a evidência, use os `source_ids` da aresta em `GET /api/sources/{id}`. Contexto pessoal representa a intenção do usuário; somente o texto da fonte sustenta afirmações factuais.

`POST /api/sources/{id}/relevance` gera ou atualiza a interpretação pessoal separadamente. Exige fonte pronta, usa o provider e idioma configurados, retorna `html`, `has_context`, `stale` e não modifica extração, resumo, tópicos ou embeddings. Retorna `409` se a fonte mudar durante a geração. O registro guarda `personal_relevance`, `relevance_context`, `relevance_signature`, `relevance_generated_at`. A assinatura permite avisar que o catálogo de contexto mudou; nenhuma atualização em massa acontece ao editar um tópico.


## Inline editing

- `PUT /api/sources/{id}/title` accepts `{ "title": "A meaningful name" }`. Trimmed title must have 1–500 characters; extra fields are rejected. Returns `{ "title": "…" }`, 404 for a missing source, 422 for invalid input, or 409 if the source disappears/changes identity during the update. Sets `title` and `title_override`; content, summary, vectors and processing state remain intact.
- `PATCH /api/topics/{id}/context` accepts either or both `definition` (up to 12,000 characters) and `personal_context` (up to 24,000). Missing fields are preserved; empty strings clear a field. Returns the updated topic. Existing `PUT` continues to replace both context fields.
- Topic rename uses the existing `PUT /api/topics/{id}` route.


## YouTube queue state

A source delayed by the YouTube cooldown remains `status: pending`, with `stage: youtube_wait`. `GET /api/sources/{id}` also returns `youtube_resume_at`, the earliest extraction eligibility recorded for that wait (actual start can be later while other work runs). The global deadline is internal queue state. Retrying or reprocessing respects it; no new client parameters are required.
