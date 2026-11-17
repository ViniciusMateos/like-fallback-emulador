# Changelog

## [1.1.0] — 2026-11-17

### Corrigido
- fix: o IG é fechado e aberto no começo de cada conta e a cada 60 perfis abertos (Like Fallback e modo funil) — cada perfil aberto por link ficava empilhado no app (1.200+ sem reiniciar), o Android do MuMu travava (load 6,5) e as visitas dobravam de tempo e saíam zeradas
- fix: depois dos stories, confere se voltou pro perfil da pessoa antes do destaque (perfil com 4 destaques saía sem nenhum); faixa de destaques na tela sem o item = espera carregar e, se a capa vier sem a descrição, toca o 1º destaque pela posição (em cima do nome dele)
- fix: o fim do feed é reconhecido pela posição dos corações e cabeçalhos — o tamanho do dump mudava com vídeo tocando e a conta ficava ~14 s rolando à toa
- fix: bloqueio de ação ("Tentar mais tarde — Limitamos a frequência…") ao abrir perfil para a conta na hora (antes dava "falha" a cada 14 s e seguia abrindo perfil com a conta bloqueada, segue23), sem descanso automático depois (quem decide quando a conta volta é o Vinicius); 5 perfis seguidos que não abrem param a conta; "falha" não vira visita (a pessoa continua na fila)
- fix: post do chat não é colhido de novo — nem na mesma passada (legenda só de emoji escapava do registro da frota e voltava 3x) nem nos 14 dias seguintes
- fix: foto zerada com o feed aberto diz o motivo no log (nenhum coração no feed, com a tela salva, ou fotos já curtidas)
- fix: privada é reconferida no máximo 2 vezes (antes: todo dia por 7 dias) — medido em 2388 reconferências: 42 aceitaram na 1ª, 6 na 2ª e nenhuma depois; a segue19 abria ~280 perfis em 13 min só reconferindo e fazia 2 visitas
- fix: modo funil — a janela "O teu pedido está pendente" depois de tocar Seguir conta como follow que não colou (fecha com OK; 4 seguidos param a conta) e, presa por cima do perfil seguinte, é fechada; antes ela ficava na frente de tudo e cada perfil dava "falha" a cada 13 s (segue31)
- fix: modo funil — 5 perfis seguidos que não abrem param a conta; bloqueio de ação fecha a janela antes de parar
- fix: `metricas.py` só conta quem foi tocado depois da 1ª leitura da base — antes, quem converteu antes dela saía da conta e quem não converteu ficava, e as contas antigas apareciam com 0% e o "só follow" com a taxa achatada

### Modificado
- update: `seguir_funil` é a 1ª parte de todo Auto Follow (não mais um modo à parte); o log diz "funil: N pessoa(s) pra @conta seguir primeiro"
- update: log do app mais limpo — os tempos de cada etapa, a reabertura do IG, as telas salvas e cada reconferência de privada vão só pro log técnico (agente.log); o app vê um resumo por lote ("N privados reconferidos")

### Documentação
- docs: README com a reconferência limitada e o funil no Auto Follow

## [1.0.0] — 2026-11-13

### Adicionado
- feat: bot Like Fallback no emulador — a conta que seguiu volta em quem ainda não segue o brechó (seguido há mais de 1 dia, mais recentes primeiro) e curte os stories, as telas de um destaque (até 3) e umas fotos
- feat: conferência em lote na lista de seguidores do brechó, com a lista "a seguir" da pessoa de reserva; sem como conferir = não curte
- feat: **funil** (o like vem primeiro, o follow é a 2ª chamada) — acabou a fila própria, a conta colhe os curtidores dos próximos posts do chat (mesma fronteira do Auto Follow, o post é consumido uma vez só, até 3 posts por entrada) e curte quem a frota nunca tocou; privada vai pra fila de follow do funil
- feat: modo funil do follow (`seguir_funil`) — a mesma conta segue as privadas que não pôde curtir e quem curtiu há 1+ dia e não seguiu o brechó (últimos 7 dias, mais recente primeiro), com conferência de soft-block e o log no formato do Auto Follow
- feat: privada que a conta seguiu é reconferida 1x por dia (até 7 dias depois do follow) — aceitou e não seguiu o brechó, a conta curte
- feat: seguidores novos do brechó lidos pela instância dele (lista própria em "mais recente", só do topo até os já conhecidos — segundos, não importa o tamanho do brechó); leitura que trava no meio não conta como completa
- feat: `metricas.py` — toques de todos os bots (follow, like, dm) por pessoa e taxa de conversão por caminho (só follow, follow → like, só like, like → follow) e por conta
- feat: visita rápida e sem delay artificial (~9 s só com fotos, ~22 s completa; privado ~1 s), com o tempo de cada etapa no log
- feat: visita robusta — espera a grade carregar (perfil sem posts sai na hora), privado com aviso atrasado conta como pulado, foto de perfil ampliada é fechada e o story tentado de novo, erro num perfil pula só o perfil e erro no meio da conta devolve o parcial; visita que saiu zerada volta pra fila uma vez

### Documentação
- docs: README do bot
