# Configuração e operação

O Signal lê o `.env` da raiz do checkout. Variáveis já definidas no processo têm prioridade. Para instalações fora do checkout, defina `SIGNAL_HOME` para a pasta que contém seu `.env`. CLI, worker e servidor precisam compartilhar a configuração de banco e `SIGNAL_DATA_DIR`.

| Variável | Padrão |
| --- | --- |
| `SIGNAL_DB_URL` | `ws://127.0.0.1:8019/rpc` |
| `SIGNAL_DB_NAMESPACE` | `signal` |
| `SIGNAL_DB_DATABASE` | `signal` |
| `SIGNAL_DB_USER` / `SIGNAL_DB_PASSWORD` | `root` / `root` |
| `SIGNAL_API_URL` | `http://127.0.0.1:8020` |
| `SIGNAL_PASSWORD` | vazio; obrigatório ao escutar fora de loopback |
| `SIGNAL_HOST` | `127.0.0.1` |
| `SIGNAL_ALLOWED_HOSTS` | vazio; hosts adicionais separados por vírgula |
| `SIGNAL_LLM_PROVIDER` | `google` |
| `SIGNAL_LLM_MODEL` | `gemini-2.5-flash` |
| `SIGNAL_EMBEDDING_PROVIDER` | `google` |
| `SIGNAL_EMBEDDING_MODEL` | `gemini-embedding-001` |
| `SIGNAL_SUMMARY_LANGUAGE` | `Português` |
| `SIGNAL_URL_ENGINE` / `SIGNAL_DOCUMENT_ENGINE` | `simple` / `simple` |
| `SIGNAL_STT_PROVIDER` / `SIGNAL_STT_MODEL` | `google` / `gemini-2.5-flash` |
| `SIGNAL_DATA_DIR` | `.signal/` na raiz do projeto |

O provider `google` usa `GEMINI_API_KEY` ou `GOOGLE_API_KEY`, conforme o Esperanto. O Signal usa a API Python das bibliotecas; não depende dos CLIs de terceiros para gerar resumos.

Abra **Preferences** no inbox para escolher **Summary & chat language**, provider/modelo de **Summaries & chat** e provider/modelo de **Embeddings**. A interface fica em inglês; o idioma de resumos e conversas continua configurável, inicialmente português.

As preferências ficam no SurrealDB e têm prioridade sobre os defaults do `.env`. Salvar vale para próximas capturas e mensagens, inclusive na CLI e no worker, **sem reiniciar**. Cada job captura uma configuração no início; alterações durante o processamento não misturam modelos. Resumos existentes são preservados e o chat recupera trechos com o modelo de embeddings original da fonte.

**Fetch model suggestions** consulta somente o provider selecionado. A lista pode variar conforme a conta e o provider; é possível informar um model/deployment ID manualmente. O indicador **Key found** confirma apenas a presença de uma chave, não saldo nem acesso ao modelo. Chaves continuam exclusivamente no `.env`; alterações de chaves ou conexão do banco exigem reiniciar os processos.

Para trocar a porta, execute `uv run signal serve --port 8021` e ajuste `SIGNAL_API_URL`. O bind usa `SIGNAL_HOST` ou `--host`; um endereço fora de loopback exige `SIGNAL_PASSWORD`.

Para uma instalação hospedada, configure a origem pública HTTPS em `SIGNAL_API_URL`, uma senha longa em `SIGNAL_PASSWORD`, `SIGNAL_HOST=0.0.0.0` e os nomes aceitos em `SIGNAL_ALLOWED_HOSTS`. A senha cria a sessão web e confirma autorizações de agentes, mas não é enviada como token MCP. Cada cliente recebe seus próprios tokens OAuth, que podem ser revogados em **Connections**. Use um proxy reverso com TLS; cookies seguros são ativados quando `SIGNAL_API_URL` começa com `https://`.

No modo local sem senha, a interface continua disponível em loopback. O MCP HTTP remoto e sua descoberta OAuth são habilitados quando há senha. `uv run signal mcp` oferece o mesmo catálogo por stdio para clientes executados na própria máquina.

## Providers alternativos

É possível usar outros providers do Esperanto por configuração. Por exemplo, com uma chave OpenAI válida e com saldo:

```dotenv
OPENAI_API_KEY=...
SIGNAL_LLM_PROVIDER=openai
SIGNAL_LLM_MODEL=gpt-4.1-mini
SIGNAL_EMBEDDING_PROVIDER=openai
SIGNAL_EMBEDDING_MODEL=text-embedding-3-small
```

Os nomes e modelos precisam ser suportados pelo provider e estar disponíveis na conta. Os vetores já existentes guardam o provider/modelo que os gerou; o chat usa esse mesmo modelo ao procurar trechos. Assim, trocar o padrão não mistura espaços vetoriais.

## Arquivos e extração

O limite por arquivo é **100 MB**. Uma cópia é guardada em `.signal/uploads/` antes de enfileirar. Você pode mover ou apagar o original sem quebrar o processamento. Faça backup do banco **e** da pasta `.signal/`.

Em **Preferences**, escolha os padrões de **Web pages**, **Documents & PDF links** e **Audio & video transcription**. Eles são compartilhados pela CLI, frontend e extensão.

- URLs: `simple`, `auto`, `jina`, `firecrawl` e `crawl4ai`.
- Documentos: `simple`, `auto` e `docling`. Docling reconhece layout, tabelas e OCR, mas é opcional por causa do tamanho de Torch e dos modelos. Instale pelo código com `uv sync --extra docling` ou construa uma imagem própria trocando o sync do Dockerfile para `uv sync --extra docling`; a primeira utilização pode baixar modelos e demorar mais. A imagem oficial usa o extrator `simple` por padrão e não inclui Docling.
- Firecrawl requer `FIRECRAWL_API_KEY`. Jina aceita `JINA_API_KEY`. Crawl4AI precisa de `CRAWL4AI_API_URL` (ou `CCORE_CRAWL4AI_API_URL`) apontando para um serviço, ou instalação opcional `content-core[crawl4ai]` com seus browsers configurados. `auto` segue a seleção do content-core conforme configuração disponível.
- Áudio/vídeo precisam de FFmpeg e credenciais do provider escolhido. O padrão do Signal é Google/Gemini, usando a chave Gemini existente. `SIGNAL_STT_PROVIDER` e `SIGNAL_STT_MODEL` definem os defaults; os antigos `CCORE_STT_*` também são aceitos como fallback. YouTube pode usar legendas existentes, sem chamar o modelo de transcrição.

Na página de uma fonte, abra **Not quite the full story?** e selecione outro motor ou modelo antes de **Reprocess this find**. Essa escolha vale apenas para essa execução; não muda os padrões. O arquivo guardado ou a URL original passa novamente pela extração, resumo e embeddings. O conteúdo publicado anterior permanece disponível até todas as etapas concluírem. Se falhar, **Try again** retoma os checkpoints da nova tentativa. A data de captura e o identificador permanecem iguais.

Conversas de extrações anteriores ficam arquivadas na página e não entram no contexto do novo chat. **Clear chat** apaga também esse histórico. O reprocessamento não mantém um arquivo de todas as extrações antigas; apenas protege a versão publicada enquanto a substituta está em andamento.

Extrações pesadas rodam em processo separado para manter a interface responsiva; cada extração tem limite de uma hora. Sites com paywall, login ou bloqueios podem impedir captura. A extensão transmite a URL, não a sessão autenticada nem uma cópia do DOM.

## Erros e recuperação

- **Queued**: aguardando o servidor ou `uv run signal worker`. Inicie um deles, ou capture sem `--enqueue` pela CLI.
- **Processing**: extraindo, resumindo ou gerando embeddings. A interface acompanha automaticamente.
- **Failed**: abra a fonte e clique em **Try again** após corrigir conexão/configuração. Conteúdo e resumo já salvos são reaproveitados.
- **Ready**: conteúdo, resumo e embeddings completos; chat disponível.

Interromper o processo mantém a captura no banco. Ao iniciar o próximo consumidor, o lock de processo permite recuperar trabalhos abandonados. Um único consumidor processa a fila por vez. Mantenha todos os processos na mesma máquina e pasta de dados; não execute múltiplos servidores Uvicorn com `--workers`.

Erros de provider não são despejados no frontend ou no log com seus corpos HTTP, evitando exposição de credenciais. Os logs identificam a fonte e a classe da exceção. Verifique saldo, quota e acesso ao modelo quando houver falha de IA.

## Topics e uma nova perspectiva

**Topics** no menu reúne tópicos oficiais (verde, ✓) e sugeridos pela IA (violeta, ✧). Toda nova geração de resumo extrai também até cinco tópicos; o prompt pede de três a cinco, permitindo menos quando a fonte não tem temas suficientes. Somente nomes oficiais entram no catálogo enviado ao LLM, e novos nomes podem ser propostos no idioma configurado.

Clique num tópico para ver suas fontes e **Make official**, ou abra **Manage topic** para renomear, fazer merge ou excluir. Essas ações são globais. Merge preserva a condição oficial quando qualquer um dos tópicos já era aprovado. Excluir um tópico não apaga os conteúdos.

Na fonte, os tópicos são editados inline. Digite em **Find or create a topic** para filtrar o catálogo em tempo real, selecione um resultado ou crie um tópico oficial quando não houver um tópico com o mesmo nome. Correspondências parciais continuam aparecendo como sugestões, sem impedir a criação. As setas e Enter também selecionam resultados. No hover ou foco do tópico, **×** remove a associação e **⌖** mantém uma sugestão nas próximas gerações; esses controles ficam sempre visíveis em telas de toque. As alterações são salvas sem recarregar a página. Uma associação escolhida manualmente permanece nas próximas gerações. Aprovar um tópico globalmente não transforma todas as suas associações automáticas em escolhas manuais. Uma associação removida manualmente não reaparece por sugestão automática; você pode adicioná-la de novo explicitamente.

**Regenerate**, ao lado de **The Short Version**, abre um campo opcional para orientar a geração: por exemplo, dar mais peso à prática e menos ao contexto histórico. A orientação vale para essa geração, é guardada para retentativas e não muda o prompt global. Resumo, seu embedding e sugestões são publicados juntos quando prontos; em caso de erro, o resumo e tópicos anteriores ficam disponíveis. Conteúdo extraído, vetores do conteúdo e histórico de chat são preservados.

As fontes antigas recebem tópicos ao usar **Regenerate** ou **Reprocess**; não há migração automática com chamadas ao LLM. **Reprocess** agora é um link discreto ao lado de **The Full Story** e abre as opções de extração num modal.

## Inbox e Library: triagem

Toda captura entra no **Inbox**, inclusive quando já está **Ready**. O estado Inbox/Library representa sua decisão de manter o conteúdo e é independente do processamento da IA. Fontes anteriores a essa funcionalidade também aparecem no Inbox.

No topo da página da fonte, ao lado do link de voltar, o seletor **Inbox / Library** permite aceitar o conteúdo ou devolver ao Inbox. O botão compacto de lixeira abre a confirmação de exclusão. A data de captura, resumo, tópicos e conversas são preservados. Capturar novamente uma duplicata não a devolve ao Inbox.

**Delete** abre uma confirmação e exclui definitivamente a fonte, seus vetores, conversa, associações de tópicos e a cópia do arquivo guardada pelo Signal. O arquivo original e os tópicos globais não são apagados. Resultados de jobs em andamento não podem recriar uma fonte excluída nem sobrescrever uma nova captura do mesmo endereço/arquivo.

Inbox e Library mostram os tópicos de cada conteúdo como links. As páginas de tópicos incluem ambas as coleções, identificadas como **Inbox** e **Library**.

## Focus

Use **☆ Focus** no topo de uma fonte para marcar algo que merece atenção depois. A estrela fica preenchida quando ativa; clicar novamente remove a marca sem excluir nem mover a fonte. A alteração salva sem recarregar a página.

**Focus** no menu reúne marcados de Inbox e Library. A marca é independente da triagem e permanece ao mover entre coleções, regenerar ou capturar uma duplicata. Fontes existentes começam sem marca. Uma estrela também identifica os marcados nas listagens.

As listagens também oferecem triagem inline: **Inbox / Library**, estrela de **Focus** e lixeira na própria linha. O estado de processamento aparece discretamente junto à data. Mover, marcar ou excluir atualiza a lista sem abrir a fonte; a lixeira pede confirmação com o título do conteúdo antes da exclusão definitiva.

## Atalhos de teclado

Os atalhos não exigem modificador e ficam suspensos durante digitação, composição de texto e outros diálogos. Comandos habituais do navegador, como Cmd/Ctrl+F e Cmd/Ctrl+L, continuam livres.

| Tecla | Ação |
| --- | --- |
| `1`, `2`, `3`, `4` | Inbox, Library, Focus, Topics |
| `↑` / `↓` | Selecionar conteúdo ou tópico anterior/próximo |
| `←` / `→` | Percorrer controles da linha ou cartões de tópicos |
| `Enter` | Abrir o item selecionado |
| `Espaço` | Abrir/fechar prévia rápida |
| `I` / `L` / `F` | Mover para Inbox / Library / alternar Focus |
| `Cmd+Delete` ou `Cmd+Backspace` | Excluir com confirmação; no Windows/Linux, usar Ctrl |
| `Esc` | Fechar prévia ou limpar seleção |
| `?` | Abrir ajuda de atalhos |

Na prévia, ↑/↓ percorrem os itens da página e I/L/F continuam disponíveis. Ela mostra o resumo ou, na ausência dele, o começo do conteúdo extraído (até 16.000 caracteres). Os atalhos de ação usam a mesma lógica e confirmação dos botões. Tab continua funcionando normalmente. É possível desativar os atalhos de tecla única na ajuda; a preferência fica neste navegador. Cmd/Ctrl+Delete continua disponível fora de campos de texto.

## Busca global

Use o campo no topo de qualquer página ou pressione **/**. Ao digitar duas letras, o Signal sugere tópicos e fontes por palavras-chave, sem chamadas de embeddings. Enter abre a página de resultados e executa o modo escolhido.

- **Hybrid** (padrão): combina posições dos rankings textual e semântico.
- **Keywords**: exige todos os termos em título, tópicos associados, resumo ou conteúdo extraído. Frases entre aspas devem aparecer juntas. Acentos e caixa são ignorados; nomes e números respeitam limites de palavra. Título e tópicos têm mais peso.
- **Semantic**: compara a pergunta com embeddings dos resumos e dos trechos. Usa o melhor trecho por fonte e mostra cada fonte uma vez.

Tópicos também são encontrados por significado: nome, definição e interesse pessoal são comparados semanticamente, e a relevância dos conteúdos associados pode sugerir o tópico. O grupo de tópicos mostra até 12 sugestões. Resultados de fontes são paginados e exibem um trecho com indicação de correspondência textual, semântica ou ambas.

Filtros: Inbox/Library/ambos, somente Focus, link/arquivo e tópico. Ao filtrar, sugestões de tópicos também se restringem aos conteúdos daquele escopo. Sem filtros, nomes de tópicos sem fontes também podem ser encontrados. Chats não participam da busca.

Cada grupo de fontes usa o seu modelo de embeddings original. Nome, definição e interesse pessoal dos tópicos usam o modelo de embeddings atual das preferências. Consultas e textos de contexto são mantidos em cache em memória por cinco minutos; não há reindexação do acervo. Falhas de um provider não impedem os resultados dos demais; avisos indicam cobertura parcial. Fontes ainda sem vetores só entram na busca textual.

A primeira versão faz comparação local em Python, com leitura do catálogo filtrado no SurrealDB, adequada ao acervo pessoal. Não usa índice vetorial aproximado. Há três chamadas de embeddings concorrentes no máximo, timeout de 25 segundos por lote e cortes mínimos de similaridade para descartar candidatos fracos. Os escores são sinais de ordenação, não probabilidades de relevância. Resultados não disparam polling semântico periódico; uma nova busca ou ação explícita atualiza a lista.

## Falhas de processamento

Os logs do worker incluem ID da fonte, etapa, provider/modelo configurado, mensagem original
da exceção e traceback completo (incluindo exceções encadeadas, sem variáveis locais).
Valores de segredos conhecidos no ambiente e senhas configuradas são mascarados.
A página da fonte também mostra a etapa e a mensagem original com essa proteção.
Depois de corrigir a configuração, use Retry. Falhas anteriores continuam com a mensagem
antiga até uma nova tentativa. A versão 0.1.6 melhora o diagnóstico; não altera modelos nem
reprocessa automaticamente o acervo.
