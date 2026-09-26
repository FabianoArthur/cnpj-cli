# cnpj-etl

**O cadastro de empresas do Brasil inteiro na sua máquina, com um comando.** O `cnpj` baixa
as competências mensais de dados abertos de CNPJ publicadas pela Receita Federal e carrega
tudo em SQLite ou PostgreSQL. Ele tenta de novo quando falha, retoma de onde parou e
guarda o histórico mês a mês.

[English](README.md)

[![CI](https://github.com/FabianoArthur/script-cnpj/actions/workflows/ci.yml/badge.svg)](https://github.com/FabianoArthur/script-cnpj/actions/workflows/ci.yml)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![Licença: MIT](https://img.shields.io/badge/licen%C3%A7a-MIT-green)

![Sessão de terminal: cnpj download tenta de novo após um HTTP 503, cnpj sqlite carrega dez tabelas, cnpj lookup mostra uma empresa e cnpj validate confere três CNPJs](docs/assets/demo.png)

<sub>Saída real de `python scripts/demo.py`: a CLI roda contra um servidor local com dados
sintéticos, então todas as empresas da imagem são fictícias.</sub>

## Por que é interessante

Os dados são públicos, mas difíceis de usar. Cada mês é um arquivo de ~6 GB num
compartilhamento do governo que é lento e às vezes cai. Dentro vêm dezenas de CSVs em
latin-1, zipados e sem cabeçalho. Até o formato do arquivo muda de um mês para outro: às
vezes é tar.gz, às vezes é zip com o mesmo nome. O `cnpj` resolve tudo isso:

- **Download que sobrevive a rede ruim.** Backoff exponencial com jitter, respeita
  `Retry-After`, retoma do último byte com `Range` + `If-Range` (um arquivo republicado
  nunca é emendado em bytes velhos). O arquivo final só aparece por um rename atômico.
- **Educado por padrão.** Dois meses por vez (no máximo quatro), `User-Agent` que se
  identifica, e mês que já está no disco nunca é baixado de novo.
- **Extração segura.** Detecta o formato real pelos primeiros bytes, recusa path traversal
  e links, e reconhece uma página de erro HTML salva como `.tar.gz`.
- **Carga tudo-ou-nada.** O SQLite é montado num arquivo temporário e trocado no fim. O
  snapshot no PostgreSQL entra numa transação só. A carga com histórico usa uma transação
  por mês, então uma execução interrompida nunca deixa meio mês para trás.
- **Pronto para o CNPJ alfanumérico** (IN RFB 2.229/2024). O `cnpj validate` confere os
  dois formatos.

## Como funciona

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/how-it-works.pt-BR-dark.svg">
  <img alt="Fluxo: compartilhamento da Receita Federal via HTTPS para o cnpj download (retry, backoff, retomada, até 4 em paralelo), em data/AAAA-MM/dados.tar.gz, depois extração segura para CSV latin-1, depois três cargas: cnpj sqlite (um mês, depois o cnpj lookup imprime JSON, CSV ou tabela), cnpj postgres (um mês, troca o snapshot atomicamente) e cnpj bulk-load (todos os meses com histórico, uma transação por mês) no PostgreSQL" src="docs/assets/how-it-works.pt-BR-light.svg">
</picture>

## Instalação

Python 3.10 ou mais novo.

```bash
git clone https://github.com/FabianoArthur/script-cnpj.git
cd script-cnpj
python -m venv .venv && source .venv/bin/activate
pip install ".[postgres]"      # tire o [postgres] se só precisar de SQLite
cnpj --help
```

## Começo rápido

```bash
# Último mês fechado num SQLite (~6 GB de download, um arquivo .db)
cnpj sqlite
cnpj lookup 00.000.000/0001-91 --db data/cnpj_2026_08.db --format json

# Histórico completo no PostgreSQL
export DATABASE_URL=postgresql://usuario:senha@localhost:5432/cnpj
cnpj download                  # todo mês desde 2023-05 que ainda não está no disco
cnpj bulk-load                 # todo mês baixado, com a coluna competencia
```

Teste com poucos meses antes de partir para o histórico completo (centenas de GB):

```bash
cnpj download --start 2026-07 --end 2026-08
cnpj bulk-load --only 2026-07 2026-08
```

## Comandos

| Comando | O que faz |
|---|---|
| `cnpj download [--start --end --workers --dry-run]` | Baixa todo mês que falta em `--dir`. Pode rodar de novo à vontade: só baixa o que falta e retoma arquivos parciais. |
| `cnpj sqlite [AAAA-MM] [--only TABELA…] [--db CAMINHO]` | Um mês num arquivo SQLite (padrão: último mês fechado). Reaproveita o download se já estiver no disco. |
| `cnpj postgres [AAAA-MM] [--schema --only --csv-dir --logged]` | Um mês no PostgreSQL via `COPY`, trocando o snapshot anterior numa única transação. |
| `cnpj bulk-load [--only AAAA-MM… --reload --skip-indexes]` | Todo mês baixado nas mesmas tabelas, com a coluna `competencia`. Mês já carregado é pulado; `--reload` substitui. |
| `cnpj validate CNPJ… [--format]` | Confere os dígitos verificadores, numérico ou alfanumérico. Lê da entrada padrão com `-`. |
| `cnpj lookup CNPJ --db CAMINHO [--format]` | Um estabelecimento com a empresa, os sócios, o Simples e a descrição dos códigos. |

Formatos de saída: `table` (padrão), `json`, `jsonl`, `csv`. Log vai para stderr e dados
para stdout, então `cnpj lookup … --format json | jq` funciona. `-v` liga o log de debug e
`-q` mostra só avisos.

**Códigos de saída:** `0` sucesso · `1` falha (rede, banco, arquivo corrompido) ou CNPJ
inválido no `validate` · `2` erro de uso · `3` CNPJ não encontrado no `lookup` · `130`
interrompido.

**Configuração:** `--dir` / `CNPJ_DATA_DIR` (padrão `./data`), `--dsn` / `DATABASE_URL` /
as variáveis `PG*` do libpq, `--base-url` / `CNPJ_BASE_URL` para um espelho. Veja o
[`.env.example`](.env.example).

### Estrutura no disco

```
data/
├── 2026-07/dados.tar.gz
├── 2026-08/dados.tar.gz
├── 2026-09/.downloading          ← incompleto: retomado na próxima execução
│          dados.tar.gz.part
└── cnpj_2026_08.db               ← do `cnpj sqlite`
```

Pasta de mês sem `.downloading` conta como concluída.

### O que vai para o banco

`empresas`, `estabelecimentos`, `socios` e `simples`, mais as tabelas de código `cnaes`,
`municipios`, `naturezas`, `paises`, `qualificacoes` e `motivos`. Toda coluna é `TEXT` de
propósito: `cnpj_basico` mantém os zeros à esquerda, `capital_social` usa vírgula decimal e
as datas vêm como `AAAAMMDD`. Converta nas consultas ou em views tipadas. O `bulk-load`
põe `competencia` como primeira coluna das quatro tabelas principais e registra cada mês em
`competencias_carregadas`.

```sql
-- Empresas que existem em 2026-08 e não existiam em 2026-07
SELECT n.cnpj_basico, n.razao_social
FROM cnpj.empresas n
LEFT JOIN cnpj.empresas p
  ON p.cnpj_basico = n.cnpj_basico AND p.competencia = '2026-07'
WHERE n.competencia = '2026-08' AND p.cnpj_basico IS NULL;
```

### Tamanhos e tempos (aproximados)

| | Um mês | Histórico completo (~40 meses) |
|---|---|---|
| Download | ~6 GB | ~240 GB |
| PostgreSQL com índices | ~40 GB | ~1,5 TB |
| Tempo de download | 10–30 min | horas |
| Tempo de carga | 30–90 min | dias |

A carga de snapshot no PostgreSQL segura um lock exclusivo nas tabelas que está trocando
até o commit. Se isso importar, aponte as leituras para outro schema ou use o `bulk-load`.

## Migrando dos scripts antigos

| Script antigo | Agora |
|---|---|
| `python sync_months.py --output-dir D` | `cnpj download --dir D` |
| `python bulk_load_postgres.py --output-dir D` | `cnpj bulk-load --dir D` |
| `python cnpj_pipeline.py 2026-04 --output-dir D` | `cnpj sqlite 2026-04 --dir D` |
| `python cnpj_to_postgres.py 2026-04 --output-dir D --schema S` | `cnpj postgres 2026-04 --dir D --schema S` |

As pastas por mês e a tabela `competencias_carregadas` não mudaram, então downloads e
bancos existentes continuam valendo. Arquivos que os scripts antigos de um mês deixaram em
`D/downloads/cnpj_AAAA-MM.tar.gz` passam por uma checagem de integridade e são movidos para
`D/AAAA-MM/`, em vez de baixados de novo. `--output-dir` continua funcionando como apelido de
`--dir`. Saíram `--download-dir`, `--work-dir`, `--no-prompt`, `--skip-download` e a
pergunta interativa da pasta.

## Testes

```bash
pip install -e ".[dev,postgres]"
ruff check . && ruff format --check .
pytest
```

A suíte nunca acessa a internet. Os downloads batem num servidor HTTP local que pode ser
instruído a falhar com 503/429, derrubar a conexão no meio, ignorar `Range`, responder no
offset errado ou servir uma página HTML. Os dados são um dump sintético com o layout real
dos arquivos ([`tests/fakedump.py`](tests/fakedump.py)). Os testes de PostgreSQL rodam
contra um servidor de verdade quando `CNPJ_TEST_DSN` aponta para um banco descartável. A CI
sobe um, e o [CONTRIBUTING.md](CONTRIBUTING.md) mostra como subir com Docker numa linha.

`python scripts/demo.py` roda o fluxo inteiro offline. `python scripts/build_diagram.py`
gera de novo os diagramas acima.

## Fonte dos dados, termos e privacidade

- Os dados são publicados pela **Receita Federal do Brasil** como dados abertos
  ([dados.gov.br](https://dados.gov.br/dados/conjuntos-dados/cadastro-nacional-da-pessoa-juridica---cnpj)).
  Cite a fonte ao usar. Este projeto não tem vínculo com o governo.
- Seja gentil com o servidor público: mantenha `--workers` baixo e não coloque o
  `cnpj download` num loop apertado.
- A tabela `socios` traz nomes de pessoas reais. A fonte já mascara o CPF. Um banco montado
  com ela é dado pessoal pela LGPD: mantenha privado e use só para finalidade legítima.

## Licença

[MIT](LICENSE) © 2026 Fabiano Arthur
