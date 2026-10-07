# CLAUDE.md — acoes_fundamentalista

Orientação rápida pra qualquer sessão do Claude Code que abrir este repo.
Detalhes completos ficam no `README.md` — leia ele antes de qualquer
mudança maior.

## O que é

Coleta diária de dados fundamentalistas (Yahoo Finance) pras ~152 ações do
**IBrA** (Índice Brasil Amplo). Só acumula histórico por enquanto — sem
análise em cima ainda. 3 saídas por dia: `dados/snapshot_diario.csv`
(P/L, P/VP, ROE, margens, DY etc.), `dados/analyst_insights.csv`
(preço-alvo low/mean/high dos analistas + upside — é o foco principal do
projeto) e `dados/financeiro/` (DRE/balanço/fluxo de caixa por ticker,
sobrescrito a cada run). `gerar_panorama.py` monta um painel HTML estático
(`dados/panorama_de_alvos.html`) com os rankings do dia — é esse arquivo
que alimenta o artefato publicado (ver seção abaixo).

## Nasceu como subpasta do mia_telegram — repo separado desde 25/08/2026

Código e dados idênticos ao que era `mia_telegram/acoes_fundamentalista/`
(commit `75ced7a` de origem) — desmembrado por não ter nada a ver com o
pipeline de sinais de opções daquele repo. Nenhum caminho interno mudou,
só o workflow (removido o prefixo `acoes_fundamentalista/` dos comandos,
já que agora é a raiz do repo).

## Cuidado: `git push` direto na CI pode perder a rodada inteira

`download_fundamentals.py` demora 7+ minutos (150+ tickers no Yahoo
Finance) — tempo suficiente pra outro commit chegar em `main` enquanto o
job roda. Um `git push` cru nesse cenário falha com `! [rejected] ...
(fetch first)` e, como o runner é efêmero, **a coleta do dia inteira se
perde** (aconteceu de verdade em 25/08, durante teste manual com push
concorrente). Por isso `coleta_diaria.yml` faz `git fetch origin main &&
git rebase origin/main` antes do `git push` — não remover esse passo.

## Incidente 26/08: cron do GitHub Actions sumiu (não é bug daqui)

`coleta_diaria.yml` (19:00 BRT) não disparou em 26/08 — não atrasou, não
falhou, sumiu da fila do `on: schedule` inteiro (`gh run list` sem run pro
dia). Backfillado via `workflow_dispatch` manual ~21:34 BRT do mesmo dia
(ainda dentro da janela válida pra capturar o fechamento). **Mesmo
incidente em pelo menos +4 workflows de outros repos do usuário na mesma
janela ~21:30-22:11 UTC** (mia_telegram, hilo, api_OMQS,
api_OMQS_futuros) — forte indício de falha da fila do GitHub Actions, não
bug de código. Se `panorama_de_alvos.html` parecer com data atrasada,
checar `gh run list` por um dia útil inteiro ausente antes de investigar
código.

## Painel próprio — não é o Painel de Sinais

Este repo **não** alimenta o Painel de Sinais compartilhado (o de
mia_telegram/hilo/api_OMQS/opcoes-sinal-diario/api_OMQS_futuros). Tem
artefato próprio: **Panorama de Alvos**
(`https://claude.ai/code/artifact/92957051-3ba4-4940-bece-1af526ebe172`).
Diferente dos outros cards do Painel de Sinais (números colados à mão), o
`gerar_panorama.py` já produz o HTML completo pronto — atualizar o
artefato é só rodar o script de novo (ou puxar o commit automático do
workflow) e republicar o `dados/panorama_de_alvos.html` direto, sem
edição manual.

## Roda em runner próprio (não GitHub-hosted) desde 28/08

`coleta_diaria.yml` usa
`runs-on: [self-hosted, self-hosted-acoes_fundamentalista]`. Migrado
depois de 2 dias seguidos (27-28/08) de atraso grave na fila
compartilhada do GitHub Actions em outro repo do usuário (ver
`omqs_futuros_5tf/CLAUDE.md` pro relato completo — inclui o histórico do
bug de `git push` concorrente desse repo também). Roda numa VPS dedicada
(DigitalOcean, mesma máquina de `mia_telegram`, `api_OMQS`,
`api_OMQS_futuros` e `opcoes-sinal-diario`). Secrets continuam nos GitHub
Secrets deste repo, normal. Se o runner sumir do ar, ver a seção de
troubleshooting no `CLAUDE.md` do `omqs_futuros_5tf` (mesmo procedimento
pra qualquer um dos runners dessa VPS).

**31/08:** `on:schedule` removido de `coleta_diaria.yml` (disparo nativo
do GitHub provou ser fonte de risco em outros repos da VPS, não rede de
segurança — ver `omqs_futuros_5tf/CLAUDE.md`). `workflow_dispatch`
continua disponível. Como o gatilho real agora é só o cron da VPS, o
horário não vive mais no yml — fica só em `/etc/cron.d/gh-triggers` na
VPS (ver nota abaixo pra horário atual).

**01/09: movido pra madrugada (03:00 BRT), fora do cluster de fechamento.**
Antes rodava 19:00 BRT, junto com mia_telegram/api_OMQS/api_OMQS_futuros
no mesmo horário de fechamento da B3 — pedido do usuário pra não
competir por recurso na VPS de 1GB. Como este job não depende de dado
intradiário da B3 (só fundamentos do Yahoo Finance, que não mudam depois
do fechamento), não tinha motivo real pra rodar logo após o pregão.
Exigiu um fix antes de mover: `HOJE` em `download_fundamentals.py`
calculava a data em **UTC**, não BRT — rodando de madrugada, UTC já
tinha virado o dia seguinte, rotulando o snapshot com a data errada.
Corrigido pra usar BRT e, quando rodar antes das 9h (mercado ainda
fechado), assumir que está capturando o fechamento do **dia útil
anterior** (`pd.tseries.offsets.BDay(1)`, pula fim de semana
automaticamente — segunda de madrugada aponta pra sexta, não domingo).

## 07/10: teste de convergência preço→alvo (`analise_upside.py`)

Primeira análise em cima do histórico acumulado. Testa "comprar o que está
mais distante do alvo rende mais". Resultado da medição exploratória sobre
as 6 primeiras semanas (24/08→06/10), **que não entrou no repo e não deve
ser tratado como conclusão**: spread bruto de +50 pp entre decil de maior e
menor upside, mas concentrado num único dia (05/10, universo +8,55%, 137 de
148 em alta, domésticas +24% e exportadoras caindo). Cesta de maior upside
tinha beta ~2,0 contra ~0,3 da outra ponta, e o spread correlacionou +0,975
com a direção do mercado — padrão de beta, não de convergência.

Três armadilhas encontradas no caminho, todas tratadas no script:

- **Beta do Yahoo é inútil aqui.** O campo `beta` do `snapshot_diario.csv`
  (5 anos mensal) deu 0,47 para uma cesta que realizou ~2,0. Não usar.
- **Beta estimado dentro do período de teste vicia o resultado** contra a
  hipótese de convergência (absorve no beta a convergência que ocorreu em
  dia de alta). Por isso o script estima em janela anterior.
- **`preco_atual` não é ajustado por proventos** — vem de
  `info["currentPrice"]`. Toda análise feita direto em cima do
  `analyst_insights.csv` herda esse viés, que infla o spread porque as
  pagadoras se concentram na ponta de baixo upside. O script baixa série
  própria com `auto_adjust=True` em `dados/precos_historico.csv`.
- `dividend_yield` do snapshot é inconfiável (mediana 6,3%, máximo 71,3%).

Nada disso está respondido: com janelas de 21 pregões faltam ~13 janelas
pra ter poder. O placar (`dados/placar_upside.csv`) acumula sozinho a cada
coleta. Ver `README.md` pra o desenho do teste.

## Estrutura

Ver `README.md`, seção "Estrutura", pra lista completa de arquivos.
