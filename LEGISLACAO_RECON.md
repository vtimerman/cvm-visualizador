# Reconhecimento da API de Legislação/Normas da CVM

> Gerado automaticamente pela tarefa agendada `cvm-legislacao-recon-retry` em **2026-07-07**.
> O portal `https://conteudo.cvm.gov.br/legislacao/index.html` **voltou ao ar** (HTTP 200) após
> o período de indisponibilidade de 2026-07-06 (HTTP 502 / "Portal temporariamente indisponível").
> Objetivo: documentar como coletar a lista de normas para construir a 3ª aba ("Normas") do visualizador.
> **Nenhuma alteração foi feita no app** — este arquivo é só o reconhecimento.

---

## 1. Resumo executivo (TL;DR)

- A busca de legislação é um **formulário POST** que devolve **HTML** (não JSON). Não há API REST/JSON.
- **Endpoint recomendado (stateless):** `POST https://conteudo.cvm.gov.br/legislacao/index.html`
  com os campos do formulário **+ `searchPage=N`**. Cada POST é autossuficiente (não precisa de
  cookie/sessão). Devolve a página inteira contendo os `<article>` de resultados.
- Existe também o endpoint AJAX interno `.../resultadoLegislacao.jsp` (fragmento menor), **mas ele é
  stateful** (depende da sessão criada pelo POST ao index) e se mostrou intermitente via `curl`.
  **Não use o `.jsp`** — prefira postar direto no `index.html`.
- **Paginação:** `searchPage=N` (1-based). `itensPagina` controla itens por página. Com
  **`itensPagina=100`** os 630 resultados desde 2020 cabem em **7 requisições** (vs. 63 com 10/página).
- **Filtro por data reproduz o enunciado:** `dataInicio=01/01/2020` ⇒ **630 resultados / 63 páginas**
  (com `itensPagina=10`). Sem data ⇒ **2590 resultados / 259 páginas** (desde 1976).
- Cada `<article>` traz **título + link para a página `.html` da norma**, **ementa**, **Data** e **Tipo**.
- O **link do PDF NÃO vem na listagem** — é preciso abrir a página `.html` da norma e extrair o
  `href` que aponta para `/export/sites/cvm/legislacao/.../anexos/NNN/arquivo.pdf`.

---

## 2. Endpoint e parâmetros

**Requisição:** `POST https://conteudo.cvm.gov.br/legislacao/index.html`
**Content-Type:** `application/x-www-form-urlencoded`
**Header necessário:** `User-Agent` de navegador (ex.: `Mozilla/5.0 ... Chrome/120 Safari/537.36`).
Sem User-Agent "de navegador" o portal pode bloquear.

| Campo                 | Exemplo / valores            | Observação |
|-----------------------|------------------------------|------------|
| `buscado`             | `true`                       | **Obrigatório** — sinaliza que uma busca foi feita. Sem isso não retorna resultados. |
| `contCategoriasCheck` | `7`                          | **Fixo = 7**. É a *contagem* de checkboxes de tipo no form (categoria0..categoria6), **não** um filtro. Mantenha 7. |
| `filtro`              | `todos` \| `qualquer` \| `exato` | Como casar os termos de `lastName`. Padrão `todos`. |
| `searchPage`          | `1`, `2`, `3`, ...           | **Número da página (1-based).** É assim que se pagina. |
| `itensPagina`         | `10` (padrão), `20`, `50`, `100` | Itens por página. Testado até 100 (funciona e reduz nº de páginas). |
| `ordenar`             | `recentes`                   | Ordenação (padrão: mais recentes primeiro). |
| `dataInicio`          | `01/01/2020` (DD/MM/AAAA)    | Opcional. Vazio = desde 07/12/1976 (`dataInicioBound`). |
| `dataFim`             | `01/07/2026` (DD/MM/AAAA)    | Opcional. Vazio = até a data corrente (`dataFimBound`). |
| `numero`              | `245` (máx. 5 dígitos)       | Opcional. Filtra pelo número da norma. |
| `lastName`            | termo (URL-encoded)          | Opcional. Busca por descrição/conteúdo. (No form o campo visível é `lastNameShow`/`termoShow`; o efetivo é `lastName`.) |
| `listaBuscaAside`     | ex.: `/legislacao/instrucoes/,/legislacao/deliberacoes/` | Opcional. Filtro por **tipo**, separado por vírgula (ver mapa na §4). Vazio = todos os tipos. |
| `tipos`, `tags`       | vazio                        | Filtros extras do painel lateral; deixar vazio. |

**Observação sobre datas:** na resposta há dois campos "bound" que informam o intervalo disponível:
`dataInicioBound=1976/12/07` e `dataFimBound=2026/07/01` (formato AAAA/MM/DD). Úteis para saber o alcance.

---

## 3. Estrutura da resposta (como parsear)

A resposta é a página HTML inteira. O que importa:

### 3.1 Contagem total e nº de páginas
- Texto: `2590 resultados encontrados` (ou `630 resultados encontrados` com filtro de data).
  Regex: `([\d.]+)\s*resultados encontrados`
- Total de páginas: dentro do form oculto `#formParam`, `<input ... name="max" value="63">`.
  Regex: `name="max"\s+value="(\d+)"`

### 3.2 Form oculto de paginação (`#formParam`)
Reflete a busca corrente (bom para conferir os parâmetros ecoados):
```html
<form action="/system/modules/br.com.squadra.legislacao/elements/resultadoLegislacao.jsp"
      id="formParam" name="formParam" class="hide">
  <input name="searchPage" value="1"> <input name="lastName" value="">
  <input name="numero" value=""> <input name="filtro" value="todos">
  <input name="dataInicio" value=""> <input name="dataFim" value="">
  <input name="buscado" value="false"> <input name="contCategoriasCheck" value="7">
  <input name="itensPagina" value="10"> <input name="ordenar" value="recentes">
  <input name="dataInicioBound" value="1976/12/07"> <input name="dataFimBound" value="2026/07/01">
  <input name="listaBuscaAside" value=""> <input name="tipos" value=""> <input name="tags" value="">
</form>
```

### 3.3 Cada resultado é um `<article>`
Exemplo real (1º resultado):
```html
<article>
  <h3>
    <a href="/legislacao/resolucoes/resol245.html" title="Resolução CVM 245"> Resolução CVM 245</a>
  </h3>
  <div class="contentDesc">
    Altera a Resolução CVM nº 50, de 31 de agosto de 2021.
    (Publicada no DOU de 03.07.2026)
    - Resolução CVM 245: [HTML] / [PDF] / [DOC]
    Justificativa - Dispensa de Análise de Impacto Regulatório (AIR)
  </div>
  <div class="infoItem">
    <p><b>Data:</b> 01/07/2026</p>
    <p><b>Tipo:</b> Resoluções</p>
  </div>
</article>
```

**Campos e como extrair (por `<article>`):**
| Campo    | Origem | Regex sugerida |
|----------|--------|----------------|
| `titulo` | texto do `<a>` dentro do `<h3>` | `<h3>\s*<a[^>]*>([^<]*)</a>` |
| `link`   | `href` do mesmo `<a>` (página `.html` da norma; **relativo**, prefixar `https://conteudo.cvm.gov.br`) | `<h3>\s*<a[^>]*href="([^"]+)"` |
| `ementa` | texto de `<div class="contentDesc">` (limpar tags e o rodapé `[HTML] / [PDF] / [DOC]`) | `class="contentDesc">(.*?)</div>` (com DOTALL) |
| `data`   | após `<b>Data:</b>` | `<b>Data:</b>\s*(\d{2}/\d{2}/\d{4})` |
| `tipo`   | após `<b>Tipo:</b>` (categoria: Resoluções, Instruções, Deliberações, Ofícios Circulares, ...) | `<b>Tipo:</b>\s*([^<]+)` |

> **Atenção:** o `[HTML] / [PDF] / [DOC]` dentro de `contentDesc` é **texto puro**, sem `href`.
> O link real do documento está **na página da norma** (ver §5), não na listagem.

---

## 4. Mapa de tipos (checkboxes `categoria0..6` / `listaBuscaAside`)

Do formulário. Deixar TODOS desmarcados retorna todos os tipos (inclui **Resoluções**, que é o
tipo predominante desde 2021 e **não** tem checkbox próprio — a classificação real vem do campo
`<b>Tipo:</b>` de cada artigo).

| Campo       | Valor (`listaBuscaAside`)              | Rótulo |
|-------------|----------------------------------------|--------|
| `categoria0`| `/legislacao/instrucoes/`              | Instruções |
| `categoria1`| `/legislacao/pareceres-orientacao/`    | Pareceres de Orientação |
| `categoria2`| `/legislacao/deliberacoes/`            | Deliberações |
| `categoria3`| `/legislacao/decisoesconjuntas/`       | Decisões Conjuntas |
| `categoria4`| `/legislacao/oficios-circulares/`      | Ofícios Circulares |
| `categoria5`| `/legislacao/leis-decretos/`           | Leis e Decretos |
| `categoria6`| `/legislacao/notas-explicativas/`      | Notas Explicativas |

(Tipos que aparecem nos resultados mas não têm checkbox: **Resoluções**, **Ofício-Circular Conjunto** etc.
Classifique sempre pelo `<b>Tipo:</b>` do artigo.)

---

## 5. Como obter o LINK do PDF (e DOCX)

O `href` do `<article>` aponta para a **página HTML da norma**, ex.:
`https://conteudo.cvm.gov.br/legislacao/resolucoes/resol245.html`

Baixe essa página e extraia o link do documento. Os `href` úteis encontrados nela:
```
/export/sites/cvm/legislacao/resolucoes/anexos/200/resol245.pdf                       <- PDF principal
/export/sites/cvm/legislacao/resolucoes/anexos/200/resol245_Justificativa_DispensaAIR.pdf  <- anexo
/legislacao/resolucoes/anexos/200/resol245.docx                                       <- DOCX
```
- **PDF principal:** primeiro `href` casando `^/export/sites/cvm/legislacao/.*\.pdf$` cujo *basename*
  bate com o da página (`resol245.pdf` ↔ `resol245.html`). Regex geral:
  `href="(/export/sites/cvm/legislacao/[^"]+\.pdf)"`.
- Sempre prefixar `https://conteudo.cvm.gov.br`.
- O segmento `anexos/NNN/` (ex.: `200/`) é um *bucket* por centena do número da norma e **não é
  derivável** com segurança sem abrir a página — por isso a página da norma precisa ser buscada.
- Nem toda norma terá PDF/DOCX; trate ausências (algumas só têm versão `[HTML]`, que é a própria
  página da norma).

**Custo:** para o backfill "desde 2020" são ~630 normas ⇒ ~7 requisições de listagem (itensPagina=100)
+ ~630 requisições às páginas das normas (uma por norma, para pegar o PDF). Compatível com o padrão já
usado em `atas_*`/`informativos_*` (que também baixam PDFs). Recomendo `time.sleep(~0.25s)` entre chamadas.

---

## 6. Exemplos de `curl` testados (2026-07-07)

```bash
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Página 1, desde 2020, 100 itens por página (7 páginas cobrem os 630 resultados)
curl -A "$UA" -X POST "https://conteudo.cvm.gov.br/legislacao/index.html" \
  --data-urlencode "buscado=true" --data-urlencode "contCategoriasCheck=7" \
  --data-urlencode "filtro=todos" --data-urlencode "dataInicio=01/01/2020" \
  --data-urlencode "searchPage=1" --data-urlencode "itensPagina=100" \
  --data-urlencode "ordenar=recentes"

# Página da norma -> link do PDF
curl -A "$UA" "https://conteudo.cvm.gov.br/legislacao/resolucoes/resol245.html" \
  | grep -oE 'href="(/export/sites/cvm/legislacao/[^"]+\.pdf)"'
```

Resultados observados:
- `dataInicio=01/01/2020` ⇒ `630 resultados encontrados`, `max=63` (10/pág) — bate com o enunciado.
- Sem data ⇒ `2590 resultados encontrados`, `max=259`.
- `itensPagina=20/50/100` ⇒ `max=32/13/7`, respectivamente.
- `searchPage=1` (1º título "Resolução CVM 245") ≠ `searchPage=2` (1º título "Ofício Circular CVM/SMI 02/2026").

---

## 7. Proposta de coleta (a construir depois, com o Vladimir)

Seguindo o padrão do projeto (`<fonte>_baixar.py` + `<fonte>_seed.py` + `<fonte>.db` + aba no `app.py`):

- **`legislacao_baixar.py`** — pagina o `index.html` (itensPagina=100), extrai os `<article>`,
  e para cada norma abre a página `.html` para pegar o PDF/DOCX. Modos `incremental` (poucas páginas,
  para na 1ª sem novidades) e `backfill` (varre tudo). Chave primária `url` (o link `.html` da norma).
- **`legislacao.db`** — tabela sugerida:
  ```sql
  CREATE TABLE IF NOT EXISTS legislacao(
    url TEXT PRIMARY KEY,      -- link .html da norma (absoluto)
    tipo TEXT,                 -- Resoluções, Instruções, Deliberações, ...
    numero TEXT,               -- extraído do título quando houver
    titulo TEXT,
    data TEXT,                 -- DD/MM/AAAA
    data_iso TEXT,             -- AAAA-MM-DD
    ementa TEXT,
    pdf_url TEXT,              -- /export/.../*.pdf (absoluto) ou vazio
    docx_url TEXT,
    coletado_em TEXT);
  ```
- **`app.py`** — 3ª aba "Normas": filtro por tipo, período, número e busca textual na ementa; link para
  o PDF/página da norma. (Não implementado ainda.)

---

## 8. Anotações / decisões automáticas desta execução

- Preferi o POST a `index.html` (stateless) em vez do `resultadoLegislacao.jsp` (stateful/intermitente).
- `itensPagina=100` escolhido para reduzir o nº de requisições da listagem.
- Não baixei o corpo das normas nem populei banco — escopo desta tarefa era só o reconhecimento.
- Datas no formato de entrada DD/MM/AAAA; os "bounds" da resposta vêm em AAAA/MM/DD.
