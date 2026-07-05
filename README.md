# Visualizador de Audiências Particulares da CVM

App web (Streamlit) para consultar as Audiências Particulares da CVM, com
**login por senha**, **busca** por data/assunto/pessoa e **coleta automática**
a cada 30 minutos rodando na nuvem (GitHub Actions).

## Peças

| Arquivo | Função |
|---|---|
| `app.py` | O visualizador (Streamlit): login, filtros, tabela, gráficos, export CSV. |
| `scraper.py` | Coletor: baixa as audiências e grava no banco `audiencias.db` (SQLite). |
| `audiencias.db` | A base de dados (versionada no repositório). |
| `.github/workflows/coleta.yml` | Rotina que roda `scraper.py atualizar` a cada 30 min e faz commit da base. |
| `requirements.txt` | Dependências (Streamlit, pandas, requests). |
| `.streamlit/config.toml` | Tema claro/escuro. |

## Rodar localmente

```bash
python -m streamlit run app.py
# senha de teste: ver .streamlit/secrets.toml
```

Comandos do coletor:
```bash
python scraper.py atualizar          # pega novos IDs acima do topo
python scraper.py backfill 1 33000   # coleta o histórico completo
python scraper.py stats              # resumo da base
```

## Publicar (uma vez)

### 1. Enviar para o GitHub
```bash
gh auth login                 # autentica no GitHub (uma vez)
gh repo create cvm-visualizador --public --source=. --push
```

### 2. Publicar o app no Streamlit Cloud
1. Acesse **share.streamlit.io** e entre com a conta GitHub.
2. **New app** → escolha o repositório `cvm-visualizador`, arquivo `app.py`.
3. Em **Advanced settings → Secrets**, cole:
   ```toml
   app_password = "SUA_SENHA_FORTE_AQUI"
   ```
4. **Deploy**. Em ~1 min o app fica no ar numa URL pública, pedindo a senha.

### 3. Ligar a coleta automática
- No GitHub, aba **Actions**, habilite os workflows.
- (Opcional) rode **Coleta CVM → Run workflow → modo: backfill** uma vez para
  completar todo o histórico na nuvem. Depois disso, o cron de 30 min mantém.

## Notas
- São **dados públicos** da CVM. A senha protege o *acesso ao app*, não os dados.
- O repositório é público para ter minutos de Actions ilimitados (a base fica
  visível, mas é informação pública). Para deixá-lo privado, reduza o cron para
  1×/hora (limite do plano grátis do GitHub Actions).
