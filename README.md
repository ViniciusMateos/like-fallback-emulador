# Like Fallback (emulador)

Segunda chamada de atenção pra quem uma conta auxiliar seguiu e ainda não segue o
[@brechoquasenadaa](https://www.instagram.com/brechoquasenadaa/): a **mesma** conta que seguiu volta no
perfil da pessoa, curte os stories, abre um destaque e curte umas fotos. Roda no app real do Instagram
(uiautomator2) no MuMu Player.

Faz parte do [quase-nada-bots-device](https://github.com/ViniciusMateos/quase-nada-bots-device): fica em
`bots/like_fallback/` e usa os bots `auto_follow`, `brecho_scraper`, `garimpo` e `like_repost`.

## Por que

O que importa é a pessoa seguir o **brechó**, não a conta auxiliar. Muita gente segue a auxiliar de volta
e não segue o brechó, e a curtida é uma segunda chance de ela ir lá olhar (o fallback do follow). Em 09/11/2026 as contas ativas
tinham seguido 8.085 pessoas e 205 delas (2,5%) seguiam o brechó.

## Quem entra na fila

- Quem a conta seguiu há mais de `dias_min` dia (padrão 1): a lista sai do `agente.log` (cada
  `+ @user` do Auto Follow, com a data da run).
- Que **não** segue o brechó: a base é a lista de seguidores do brechó, lida uma vez pelo perfil público
  (2.692 de 2.713 em ~12 min) e guardada com a data de cada um.
- Que ainda não recebeu o like fallback daquela conta.
- **Ordem:** primeiro quem foi seguido até `dias_max` dias atrás (padrão 7), do mais recente pro mais
  antigo, porque pra essas pessoas o follow ainda tá fresco. Depois vem o acumulado antigo.
- Privada que a conta seguiu volta pra fila 1x por dia, até 7 dias depois do follow: se aceitou e não
  seguiu o brechó, a conta curte.

## Funil: o like primeiro, o follow depois

Acabou a fila própria (ou ela está vazia), a conta não para (`frio`, ligado por padrão):

1. **Colhe** os curtidores do próximo post do chat (`chat`, padrão `vaitomanoquasenada`), na mesma
   fronteira do Auto Follow: o post é consumido pela frota uma vez só (registro + ❤ no grupo).
2. Entra no **pool** só quem ninguém da frota tocou (seguiu, curtiu) e não segue o brechó.
3. Visita como sempre (confere o brechó em lote, curte stories, destaque e fotos):
   - pública curtida → etapa `curtido`;
   - privada → etapa `pend_follow` (não dá pra curtir; vai pro follow).
4. O **Auto Follow no modo funil** (campo `funil` do modo) segue, com a MESMA conta que tocou: as
   privadas `pend_follow` e quem foi curtido há 1+ dia e não seguiu o brechó. Só quem foi tocado nos
   últimos 7 dias, a mais recente primeiro — o like rende muito mais gente do que os 125 follows por
   dia, e quem não for seguido em 7 dias sai da fila (o like já foi o toque).
5. Privada que aceitou o follow e em 1 dia não seguiu o brechó → o Like Fallback curte (fila própria).

Estado em `funil.json` (raiz do device, fora do git): `pool`, `pessoas` (etapa, conta, origem e
histórico) e `posts` colhidos. Lógica do funil em `funil.py`.

## Métricas

A base de seguidores do brechó é atualizada pela **instância do próprio brechó**: na lista dos seus
seguidores o IG ordena por "Data em que começaste a seguir: mais recente", então o bot lê só do topo e
para quando aparecem 5 seguidos que já estavam na leitura anterior — custa segundos, não importa o
tamanho do brechó (a "Predefinição" não é por data). Roda no fim de cada story do brechó (a instância já
está ligada) e, se a última leitura tiver mais de 20 h, no começo do Like Fallback (o agente reserva a
instância do brechó, liga, lê e desliga; ocupada com story = fica pra próxima). Se a lista parar de
carregar antes dos conhecidos, a leitura fica marcada como incompleta e a próxima lê de novo até passar
dela. `metricas.py` junta os toques de todos os bots — follow (`agente.log`), like (este bot) e DM
(`dms_feitos.json`) — numa linha do tempo por pessoa:

```
python -m bots.like_fallback.metricas
```

Mostra, por caminho (só follow, follow → like, só like, like → follow, + dm) e por conta: quantos
foram tocados, quantos passaram a seguir o brechó depois do 1º toque e a taxa (também só com quem foi
tocado há 3+ dias). Quem já seguia antes do toque fica fora da taxa. Salva `metricas_resumo.json`.

## O que faz em cada perfil

1. Abre o perfil. Privado que não aceitou o follow é pulado na hora.
2. **Confere se já segue o brechó** (em lote, antes das visitas): abre a lista de **seguidores do
   brechó** uma vez e pesquisa o @ de cada pessoa do lote (20 por vez, ~2 s cada). Quem aparece sai da fila
   e conta como **conversão** daquela conta. Se a lista do brechó não abrir, o 2º caminho é pesquisar
   `brechoquasenadaa` na lista "a seguir" da pessoa. **Não deu pra conferir por nenhum = não curte** (fica
   pra próxima). A caixa de pesquisa muda de id conforme a conta (`search_edit_text` ou
   `row_search_edit_text`).
3. Curte os stories que estiverem no ar (até 4).
4. Abre o 1º destaque e **curte** as telas dele (até 3), como no story.
5. Curte até `curtidas` fotos (padrão 3), descendo o feed do perfil. Só toca no coração que ainda não
   está curtido. Antes espera a grade carregar (até 6 s; o cabeçalho chega antes das fotos) e sai na
   hora se o perfil tem 0 publicações.

Visita que sai zerada (sem foto, sem story e sem destaque) volta pra fila **uma vez**. O log diz o
motivo: `[sem posts]` ou `[grade não reconhecida]` (aí a tela fica salva em `debug_like_fallback/` pra
investigar o layout). Um erro num perfil (story fechando no meio, elemento que some) pula só aquele
perfil, nunca a conta.

Story e destaque vêm antes das fotos porque ficam no topo do perfil: depois das fotos a grade volta rolada
e o topo some.

## Estado

`like_fallback_estado.json` na raiz do device:

- `seguidores`: quem segue o brechó, com a data em que apareceu;
- `curtidos`: `{conta: {user: {ts, curtidas, stories, destaque}}}` (ou o motivo do pulo);
- `conversoes`: `{conta: {user: ts}}`, quem a conferência achou seguindo o brechó;
- `leituras`: cada leitura da lista de seguidores do brechó.

## Onde roda

No agente do `quase-nada-bots-device`, disparado pelo app dos Bots: seleciona as contas e roda. Modo
`padrão`: `perfis` (20 por conta), `curtidas` (3), `dias_min` (1), `dias_max` (7), `stories` e
`destaque` (ligados). Sem delay artificial (a navegação no emulador já é a simulação): o bot espera
só a tela responder. Um perfil leva ~20-35 s; privado, ~1 s.

```python
from bots.like_fallback import like_fallback

r = like_fallback.processar(d, log, conta, params, parar)
# → {perfis, curtidas, stories, destaques, ja_seguem, pulados, falhas, motivo}
```
