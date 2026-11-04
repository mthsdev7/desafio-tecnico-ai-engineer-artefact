# Desafio Técnico AI Engineer - Artefact

## Visão geral

Este projeto é um protótipo de atendimento com RAG (*Retrieval-Augmented
Generation*) e function calling. O agente escolhe entre buscar informações
semânticas nas políticas em PDF e consultar os dados estruturados dos CSVs com
SQL.

## Decisões técnicas

### Preparação dos documentos

O script lê os PDFs encontrados em `data/` com `pypdf` e cria um objeto
`Document` para cada página que contém texto, mantendo o nome do arquivo e o
número da página como metadados. O texto extraído é normalizado, substituindo
sequências de espaços e quebras de linha por um único espaço. Isso evita que
quebras de linha introduzidas pelo layout do PDF prejudiquem a busca semântica.

O `RecursiveCharacterTextSplitter` divide o texto em trechos de até
aproximadamente 2.000 caracteres, com sobreposição de 500 caracteres. A
sobreposição ajuda a preservar contexto quando uma informação fica na fronteira
entre trechos. O tamanho é medido em caracteres (`len`), não em tokens.

### Embeddings e busca vetorial

Foi escolhido `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
por gerar representações semânticas multilíngues adequadas para consultas em
português. O modelo roda localmente via Hugging Face, evitando uma chamada paga
a um serviço externo para cada embedding; na primeira execução, os pesos podem
precisar ser baixados.

O Chroma guarda os vetores no diretório `db/`, permitindo persistir a coleção
entre execuções. A coleção `loja_pdf_v2` usa distância cosseno. Antes de indexar,
o script remove da coleção os trechos das fontes que está carregando e os
adiciona novamente com IDs `emporio-<índice>`. Assim, cada execução reflete o
conteúdo atual dos PDFs, em vez de manter vetores antigos quando o texto muda.

A busca usa `similarity_search` e recupera os 7 trechos mais próximos. Esse
valor foi escolhido para incluir trechos relevantes que podem não aparecer nos
primeiros resultados; a normalização do texto também ajuda a classificação
semântica. A busca não usa pontuações de relevância: o fluxo precisa apenas dos
documentos recuperados para montar o contexto.

### Dados estruturados e agente SQL

Os CSVs em `data/` são importados para tabelas relacionais no SQLite
(`data/loja.sqlite3`): `categories`, `customers`, `order_items`, `orders`,
`products` e `promotions`. O banco é recriado a partir dos CSVs em cada
inicialização do programa, de modo que os arquivos permanecem como fonte da
verdade. Valores de preço, total, desconto, quantidade e identificadores são
armazenados como números; os demais campos são texto.

Os CSVs não são vetorizados. Para filtros exatos como categoria, preço, status e
estoque, SQL é mais apropriado do que busca vetorial: por exemplo, o agente pode
relacionar `products.category_id` a `categories.category_id`, exigir
`price_brl <= 1000`, `status = 'active'` e `stock_quantity > 0`. Chroma continua
sendo usado para recuperar texto sem estrutura, como as políticas do PDF.

O modelo usa function calling para escolher e chamar uma ferramenta de SQL ou
uma ferramenta de busca nas políticas. A ferramenta SQL aceita apenas consultas
de leitura (`SELECT`/`WITH`), usa uma conexão SQLite em modo somente leitura e
limita a resposta a 50 linhas. O agente também tem um limite de 5 rodadas de
ferramentas por pergunta, em um ciclo de decidir, chamar a ferramenta e
considerar o resultado antes de responder (estilo ReAct, sem expor raciocínio
interno). Para promoções ativas, consulta SQL; para campanhas previstas e
regras, consulta o PDF. Leituras da tabela `customers` e das colunas
`customer_id`, `tracking_code` e `notes` de `orders` são bloqueadas para não
expor dados pessoais ou sensíveis.

### Geração da resposta

Para este teste, o modelo de chat é `openai/gpt-oss-20b`, acessado pelo serviço
Groq através de `ChatGroq`. Essa escolha atende ao objetivo de validar o fluxo
do protótipo; não representa necessariamente a opção preferida para uso
cotidiano. Para projetos executados localmente, a preferência é utilizar modelos
locais via Ollama, evitando depender de uma API externa para gerar respostas.

A temperatura está definida como `0` para reduzir variações nas respostas. O
prompt instrui o assistente a responder em português, usar as informações
recuperadas, não inventar políticas e diferenciar campanhas previstas de
promoções confirmadas como ativas.

A chave `GROQ_API_KEY` é necessária para executar a configuração atual de teste
com Groq e é lida de uma variável de ambiente carregada do arquivo `.env`. A
chave não deve ser incluída no código nem versionada. Uma configuração com
Ollama local exigiria substituir a integração `ChatGroq` pela integração do
LangChain com Ollama e configurar o modelo local instalado.

## Validação atual

O fluxo foi executado com sucesso usando o PDF de políticas disponível em
`data/`. Em um teste sobre envio por Sedex, o agente respondeu corretamente que
a loja envia por Sedex e PAC para cidades fora da região metropolitana de Campo
Grande, informou o prazo de 2 a 5 dias úteis e mencionou o envio automático do
rastreamento por e-mail e WhatsApp.

Esse resultado confirma que a extração do PDF, a recuperação dos trechos
relevantes e a geração da resposta estão funcionando juntas para esse caso. O
agente usa o conteúdo textual extraído do PDF como base para responder às
perguntas sobre as políticas; isso não garante acerto em toda pergunta, e as
informações recuperadas podem não cobrir todos os casos ou refletir mudanças
posteriores no documento.

Também validei a importação dos seis CSVs e uma consulta SQL equivalente a
“Quais opções de violões disponíveis custando até R$ 1.000?”. O banco retornou
12 produtos ativos, em estoque e dentro do preço informado. Durante o teste
com o modelo, ele gerou uma condição `OR` sem parênteses, o que pela precedência
do SQL fazia os filtros de preço e estoque não se aplicarem a todas as
categorias. A ferramenta agora rejeita essa consulta com uma orientação para
corrigi-la, e o prompt explica como agrupar as alternativas. Também orienta a
buscar a categoria "Violões" pelo prefixo sem acento (`LIKE '%viol%'`), evitando
diferenças entre a forma singular da pergunta e o nome plural cadastrado. Após
essas correções, validei novamente a pergunta em linguagem natural com o Groq:
o agente consultou o SQL e apresentou os 12 produtos correspondentes.

## Uso do GitHub Copilot

Utilizei o GitHub Copilot como apoio durante o desenvolvimento para investigar
alguns pontos em que tive dificuldade ou não havia percebido o que estava
causando um erro. A ajuda foi útil para entender e corrigir problemas no fluxo
de busca e recuperação de informações do PDF e para documentar as decisões do
projeto. Fiz essa escolha para avançar na implementação sem gastar tempo
excessivo bloqueado nesses pontos; revisei e validei as alterações com testes
práticos, como a consulta sobre envio por Sedex.

## Instalação e execução no Windows

### Pré-requisitos

- Windows e Python 3.12 instalado. Essa é a versão usada para validar o projeto.
- Conexão com a internet na primeira instalação e execução: o `pip` baixa as
  dependências e o Sentence Transformers baixa os pesos do modelo de embeddings.
- Uma chave de API Groq válida. O código atual usa Groq para gerar as respostas;
  Ollama local ainda não está integrado.

Confira a instalação do Python no PowerShell:

```powershell
py --version
```

### Criar o ambiente e instalar as dependências

Abra o PowerShell na pasta do projeto. Se necessário, navegue até ela:

```powershell
cd "C:\Users\Matheus Neves\Desktop\ai engineer"
```

Crie um ambiente virtual e instale as dependências:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

O arquivo `requirements.txt` inclui LangChain, Chroma, Sentence Transformers,
PyPDF e a integração com Groq, entre outras dependências usadas pelo agente.
Use o Python do ambiente virtual nos comandos seguintes para garantir que essas
dependências estejam disponíveis.

### Configurar a chave Groq

Na raiz do projeto, crie um arquivo chamado `.env` e coloque nele sua chave:

```text
GROQ_API_KEY=sua_chave_groq
```

Substitua `sua_chave_groq` pela chave obtida na sua conta Groq. Não coloque
aspas ao redor do valor e não compartilhe nem versione esse arquivo. O `.env`
já está listado no `.gitignore`.

### Conferir os dados de entrada

Coloque os arquivos de dados na pasta `data/`:

- Arquivos `.csv` com cabeçalho na primeira linha. Os seis arquivos fornecidos
  são `categories`, `customers`, `order_items`, `orders`, `products` e
  `promotions`.
- Os arquivos `.pdf` com políticas que o agente deve consultar.

Não é preciso converter os CSVs manualmente. Ao iniciar, o programa recria
`data/loja.sqlite3` a partir de todos os arquivos `.csv` existentes em `data/`.
Esse banco é gerado automaticamente e está ignorado pelo Git; os CSVs são a
fonte dos dados. A ferramenta consulta SQL em modo somente leitura.

Na inicialização, o agente também extrai texto dos PDFs em `data/`, divide o
conteúdo em trechos e atualiza os embeddings da coleção Chroma no diretório
`db/`. PDFs escaneados como imagem, sem camada de texto, não são lidos por esta
extração.

### Iniciar o agente

Execute o comando a partir da raiz do projeto:

```powershell
.\.venv\Scripts\python.exe rag.py
```

Na primeira execução, aguarde o download dos pesos do modelo de embeddings.
Quando aparecer `Agente pronto`, digite uma pergunta e pressione Enter. Exemplos:

```text
Quais opções de violões disponíveis custando até R$ 1.000?
Vocês enviam por Sedex?
Qual é o prazo para trocar um instrumento com defeito?
```

Para encerrar, digite `sair`.

### Atualizar os dados

Edite ou substitua os CSVs e PDFs em `data/` e reinicie `rag.py`. O banco
SQLite e os trechos dos PDFs são atualizados na inicialização; não é necessário
executar uma etapa separada de ingestão. Execute sempre a partir da raiz do
projeto, pois o diretório `db/` do Chroma é relativo à pasta atual.

### Problemas comuns

- **`GROQ_API_KEY não encontrada`:** confira se o arquivo se chama exatamente
  `.env`, está na raiz do projeto e contém `GROQ_API_KEY=...`.
- **Erro de autenticação ou limite da API:** valide a chave, o acesso ao modelo
  configurado e os limites da sua conta Groq.
- **`No module named ...`:** instale as dependências no ambiente virtual e
  execute o projeto com `.\.venv\Scripts\python.exe rag.py`.
- **O agente não encontra um produto ou promoção:** confira os dados e os
  cabeçalhos do CSV em `data/`; o catálogo depende dos valores existentes nos
  arquivos.
- **Um PDF não aparece nas respostas:** confirme que ele está dentro de `data/`
  e que contém texto selecionável, não apenas páginas escaneadas.

## Limitações atuais

- O agente é executado em um loop interativo no terminal; ainda não há uma
  interface web ou API.
- O agente consulta as informações extraídas do PDF, mas não consulta sistemas
  externos para confirmar preços, estoque, situação de pedidos ou se promoções
  continuam ativas; para esses dados, depende dos CSVs locais.
- O estado de uma promoção é o campo `is_active` do CSV. O agente não confirma
  essa informação em um sistema externo.
- As consultas SQL do agente são somente de leitura; alterações nos dados devem
  ser feitas nos CSVs de origem.
