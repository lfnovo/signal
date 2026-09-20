# Salvar URLs do iPhone no Signal

## Criar o acesso

1. Entre no Signal por HTTPS e abra **Connections**.
2. Em **Capture from your iPhone**, informe `iPhone` e clique em **Create capture token**.
3. Copie o token mostrado. Ele só aparece nessa resposta; se perder, revogue e crie outro.

O token permite apenas enviar URLs. Não permite ler conteúdos, apagar, editar nem acessar o MCP.
Ele vale até ser revogado em Connections. Guarde-o no seu atalho; remova o token antes de
compartilhar uma cópia do atalho com outra pessoa.

## Montar o atalho

No app **Atalhos**, crie `Salvar no Signal`:

1. Nos detalhes, ative **Mostrar na Folha de Compartilhamento** e aceite **URLs** e
   **Páginas Web do Safari**.
2. Adicione **Obter URLs da Entrada**, usando **Entrada do Atalho**.
3. Se a lista estiver vazia, mostre um alerta “Compartilhe um link para salvar no Signal” e
   use **Parar Este Atalho**.
4. Adicione **Repetir com Cada Item** da lista de URLs.
5. Dentro da repetição, adicione **Obter Conteúdo de URL**. Use o endpoint exibido em Connections,
   por exemplo `https://signal.dev.supernovalabs.com.br/api/sources`.
6. Expanda a ação e configure:
   - Método: **POST**.
   - Cabeçalhos: `Authorization` com valor `Bearer SEU_TOKEN` (um espaço depois de Bearer).
   - Corpo da solicitação: **JSON**.
   - Campo `url`, tipo **Texto**, valor variável **Item da Repetição**.
7. Use **Obter Dicionário da Entrada** no resultado. Leia `source` e depois `id` com
   **Obter Valor do Dicionário**. Se não houver ID, mostre o campo `detail` como erro e pare.
8. Leia `created` do dicionário original. Se verdadeiro, mostre a notificação “Salvo no Inbox”.
   Caso contrário, mostre “Já estava salvo no Signal”.

Os nomes das ações podem variar com o idioma do iOS. O Atalhos envia o corpo JSON; não monte
JSON concatenando o texto da URL. Não coloque o token na URL nem no campo `url` do corpo.

## Usar e conferir

No Safari ou em outro app que compartilhe URLs, toque em **Compartilhar → Salvar no Signal**.
Na primeira execução, permita que o atalho acesse seu domínio Signal quando o iOS solicitar.
Abra o Inbox e confirme a captura; o processamento ocorre em segundo plano.
Compartilhar novamente o mesmo link não reprocessa nem move um item já salvo na Library.

Se houver erro de conexão, verifique a URL HTTPS e a conectividade do iPhone. Para `401`, confira
o header e o token; se revogado, crie outro. Um erro não deve gerar notificação de sucesso.
O atalho não depende de login no Safari nem de cookies.

Referência: [requisições de API no Atalhos — Apple](https://support.apple.com/en-euro/guide/shortcuts/apd58d46713f/ios).
