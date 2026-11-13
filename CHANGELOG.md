# Changelog

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
