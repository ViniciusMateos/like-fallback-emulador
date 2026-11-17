"""Like Fallback (09/11/2026, pedido do Vinicius) — fase 1: quem merece um like.

A ideia: a pessoa que uma conta auxiliar seguiu e que, passado mais de 1 dia, ainda NÃO segue o
@brechoquasenadaa recebe uma "segunda chamada de atenção" da MESMA conta que seguiu (curtidas em
fotos, stories e um destaque — fase 2). Ninguém mais é excluído: quem seguiu a auxiliar de volta mas
não o brechó também entra, porque o que conta é seguir o brechó.

Fase 1 (este arquivo):
  - quem cada conta seguiu e quando: sai do agente.log (linhas "+ @user (k/n)" + data da run);
  - quem segue o brechó: a lista de seguidores do perfil público do brechó, lida por uma conta
    auxiliar (não usa a conta do brechó), guardada com a data em que cada um apareceu;
  - a fila de like de cada conta = seguidos por ela há > `min_horas` que não estão nos seguidores.
Estado: like_fallback_estado.json na raiz do device (gitignored)."""
import collections
import datetime
import io
import json
import os
import re
import time
import urllib.request

import random

from bots.auto_follow import auto_follow as af
from bots.auto_follow.auto_follow import _attr, _bounds, _dump, _log, _sleep, rid
from bots.brecho_scraper import brecho_scraper as bs
from bots.garimpo import garimpo as gp
from bots.like_repost import like_repost as lr
from bots.like_fallback import funil as fn

BASE = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
ESTADO_PATH = os.path.join(BASE, "like_fallback_estado.json")
AGENTE_LOG = os.path.join(BASE, "agente.log")
CONFIG_PATH = os.path.join(BASE, "agente_config.json")
BRECHO = "brechoquasenadaa"

_RE_JOB = re.compile(r"\[agente\] job recebido: (run-\d+) \((.*)\)")
_RE_CONTA = re.compile(r"\[conta\] (quasenadasegue\d+)")
_RE_FOLLOW = re.compile(r"^\[(\d\d):(\d\d):(\d\d)\]   \+ @(\S+)  \(\d+/\d+\)")


# ─────────────────────────────── estado ───────────────────────────────
def carregar():
    try:
        with open(ESTADO_PATH, "r", encoding="utf-8") as f:
            est = json.load(f)
    except Exception:
        est = {}
    est.setdefault("seguidores", {})       # {user: ts em que apareceu na lista do brechó}
    est.setdefault("leituras", [])         # [{ts, total, novos}]
    est.setdefault("curtidos", {})         # fase 2: {conta: {user: {ts, curtidas, stories, destaque}}}
    est.setdefault("conversoes", {})       # {conta: {user: ts}} — achado seguindo o brechó na conferência
    return est


def salvar(est):
    try:
        tmp = ESTADO_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(est, f, ensure_ascii=False)
        os.replace(tmp, ESTADO_PATH)
    except Exception:
        pass


# ─────────────────────────────── quem a frota seguiu ───────────────────────────────
def _inicio_das_runs():
    """{run-id: epoch de início} pelo /runs/history do backend (a linha do log só tem a hora)."""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            c = json.load(f)
        req = urllib.request.Request(c["backend_url"].rstrip("/") + "/runs/history",
                                     headers={"Authorization": "Bearer " + c["backend_token"]})
        h = json.loads(urllib.request.urlopen(req, timeout=30).read())
        h = h if isinstance(h, list) else h.get("runs") or []
        return {r["id"]: float(r["started_at"]) for r in h if r.get("id") and r.get("started_at")}
    except Exception:
        return {}


def seguidos_por_conta():
    """{conta: {user: epoch do follow}} — todos os follows do auto-follow que estão no agente.log.
    A data vem do início da run; a hora, da própria linha (virou a meia-noite = dia seguinte)."""
    inicio = _inicio_das_runs()
    out = collections.defaultdict(dict)
    run, auto, conta, base = None, False, None, None
    ultimo_seg = None
    with io.open(AGENTE_LOG, encoding="utf-8", errors="replace") as f:
        for linha in f:
            m = _RE_JOB.search(linha)
            if m:
                run, auto, conta = m.group(1), "auto-follow" in m.group(2), None
                ts = inicio.get(run)
                base = datetime.datetime.fromtimestamp(ts) if ts else None
                ultimo_seg = None
                continue
            if not auto:
                continue
            m = _RE_CONTA.search(linha)
            if m:
                conta = m.group(1)
                continue
            m = _RE_FOLLOW.match(linha)
            if not (m and conta and base):
                continue
            h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
            seg = h * 3600 + mi * 60 + s
            dia = base.replace(hour=0, minute=0, second=0, microsecond=0)
            if seg < base.hour * 3600 + base.minute * 60 - 60:   # passou da meia-noite na run
                dia += datetime.timedelta(days=1)
            ts = (dia + datetime.timedelta(seconds=seg)).timestamp()
            u = m.group(4).lower()
            out[conta].setdefault(u, ts)                      # o 1º follow é o que vale
    return out


# ─────────────────────────────── quem segue o brechó ───────────────────────────────
def _linhas_da_lista(xml):
    return [(_bounds(m.group(0))[1], _attr(m.group(0), "text").strip().lower())
            for m in re.finditer(r"<node[^>]*/?>", xml)
            if _attr(m.group(0), "resource-id").endswith(":id/follow_list_username")
            and _attr(m.group(0), "text") and _bounds(m.group(0))]


def ler_seguidores_brecho(d, log=None, parar=None, teto=8000):
    """Abre o perfil público do brechó (com a conta auxiliar logada), toca em "seguidores" e lê a lista
    inteira rolando. Devolve (set de usernames, total que o perfil diz) ou (None, total) se não abriu."""
    if gp.abrir_perfil(d, BRECHO, log) != "ok":
        return None, None
    total = gp.ler_seguidores(_dump(d))
    alvo = d(resourceId=rid("profile_header_followers_stacked_familiar"))
    if not alvo.exists:
        return None, total
    alvo.click()
    _sleep(3.0, 4.0)
    try:
        W, H = d.window_size()
    except Exception:
        W, H = 900, 1600
    vistos, parado, prev = set(), 0, None
    t0 = time.time()
    while len(vistos) < teto:
        if parar and parar():
            break
        xml = _dump(d)
        linhas = _linhas_da_lista(xml)
        for _y, u in linhas:
            vistos.add(u)
        atual = tuple(u for _y, u in linhas)
        parado = parado + 1 if atual == prev else 0
        if parado >= 3:
            break
        prev = atual
        if len(vistos) and len(vistos) % 300 < 12:
            _log(log, f"  seguidores do brechó: {len(vistos)} lidos ({int(time.time() - t0)} s)")
        d.swipe(W // 2, int(H * 0.86), W // 2, int(H * 0.22), 0.25)
        _sleep(0.8, 1.2)
    for _ in range(2):
        d.press("back")
        _sleep(0.6, 0.9)
    return vistos, total


def atualizar_seguidores(d, est, log=None, parar=None):
    """Lê a lista do brechó e grava quem apareceu (com a data da 1ª vez). Devolve quantos lidos."""
    lidos, total = ler_seguidores_brecho(d, log, parar)
    if not lidos:
        _log(log, "não consegui ler a lista de seguidores do brechó")
        return 0
    agora = time.time()
    novos = [u for u in lidos if u not in est["seguidores"]]
    for u in novos:
        est["seguidores"][u] = agora
    est["leituras"].append({"ts": agora, "lidos": len(lidos), "total": total, "novos": len(novos)})
    salvar(est)
    _log(log, f"seguidores do brechó: li {len(lidos)} de {total} — {len(novos)} novo(s) desde a última leitura")
    return len(lidos)


def _ultima_leitura_completa(est):
    """Quando a base foi lida INTEIRA até os já conhecidos pela última vez (leitura que travou no meio,
    com a lista sem carregar, não conta — a próxima tem que ler até passar dela)."""
    return max((x.get("ts", 0) for x in est["leituras"] if x.get("completa", True)), default=0)


def leitura_vencida(est=None, horas=20):
    """A base de seguidores do brechó não é lida (completa) há mais de `horas`?"""
    est = est or carregar()
    return time.time() - _ultima_leitura_completa(est) > horas * 3600


def _ordenar_mais_recente(d, log=None):
    """Na lista dos PRÓPRIOS seguidores: deixa em "Data em que começaste a seguir: mais recente" (a
    "Predefinição" NÃO é por data — os 12 primeiros dela estavam todos na base velha, 13/11)."""
    hdr = d(resourceId=rid("sorting_entry_row_option"))
    try:
        txt = ((hdr.info.get("text") or "") + " " + (hdr.info.get("contentDescription") or "")).lower() \
            if hdr.exists else ""
    except Exception:
        txt = ""
    if "mais recente" in txt:
        return True
    ico = d(resourceId=rid("sorting_entry_row_icon"))
    if not ico.exists:
        return False
    ico.click()
    op = d(textContains="mais recente")
    if not op.wait(timeout=4):
        d.press("back")
        return False
    op.click()
    _sleep(1.5, 2.0)
    return True


def ler_novos_seguidores(d, log=None, parar=None, teto=5000):
    """SEGUIDORES NOVOS DO BRECHÓ pela instância DELE (13/11): na lista dos próprios seguidores o IG ordena
    por "mais recente" — lê do TOPO e para quando aparecem 5 seguidos que já estavam na leitura anterior
    (conversão que a conferência achou no meio do dia NÃO conta como parada: ela entrou na base depois).
    Custa segundos, não importa o tamanho do brechó. Grava cada novo com a hora da leitura (resolução de
    1 dia pra métrica). Devolve quantos novos, ou None se não abriu."""
    est = carregar()
    ult = _ultima_leitura_completa(est)
    if abrir_perfil(d, BRECHO, log) != "ok":
        _log(log, "seguidores do brechó: não abri o perfil dele")
        return None
    alvo = d(resourceId=rid("profile_header_followers_stacked_familiar"))
    if not alvo.exists:
        _log(log, "seguidores do brechó: não achei o contador de seguidores")
        return None
    alvo.click()
    if not d(resourceId=rid("follow_list_username")).wait(timeout=8):
        _log(log, "seguidores do brechó: a lista não abriu")
        d.press("back")
        return None
    if not _ordenar_mais_recente(d, log):
        _log(log, "seguidores do brechó: não consegui ordenar por mais recente — não leio (sairia errado)")
        d.press("back")
        return None
    try:
        W, H = d.window_size()
    except Exception:
        W, H = 900, 1600
    vistos, novos, velhos, parado, prev = set(), [], 0, 0, None
    while len(vistos) < teto and velhos < 5:
        _chk(parar)
        linhas = _linhas_da_lista(_dump(d))
        for _y, u in linhas:
            if u in vistos:
                continue
            vistos.add(u)
            if u not in est["seguidores"]:
                novos.append(u)
                velhos = 0
            elif est["seguidores"][u] <= ult + 120:
                velhos += 1                       # já estava na leitura anterior
                if velhos >= 5:
                    break
            else:
                velhos = 0                        # conversão achada hoje pela conferência: segue lendo
        atual = tuple(u for _y, u in linhas)
        if atual == prev:
            # a lista carrega por PÁGINAS (spinner no fim): 3 leituras iguais em ~2 s pararam a 1ª leitura
            # no 25º nome (13/11) — e quem ficou depois seria perdido pra sempre (a próxima leitura para
            # nos conhecidos do topo). Espera a página chegar antes de concluir que acabou.
            parado += 1
            if parado >= 6:
                break
            # sobe um pouco e desce de novo: força o IG a pedir a próxima página (às vezes ela não vem
            # sozinha — 3 de 4 leituras de 13/11 travaram no 25º nome)
            d.swipe(W // 2, int(H * 0.40), W // 2, int(H * 0.75), 0.2)
            _sleep(1.0, 1.4)
            d.swipe(W // 2, int(H * 0.86), W // 2, int(H * 0.30), 0.2)
            _sleep(1.5, 2.0)
            continue
        else:
            parado = 0
        prev = atual
        if velhos < 5:
            d.swipe(W // 2, int(H * 0.86), W // 2, int(H * 0.30), 0.25)
            _sleep(0.7, 0.9)
    agora = time.time()
    for u in novos:
        est["seguidores"][u] = agora
    completa = velhos >= 5                      # chegou nos que já estavam na leitura anterior
    est["leituras"].append({"ts": agora, "lidos": len(vistos), "novos": len(novos), "fonte": "brecho",
                            "completa": completa})
    salvar(est)
    for _ in range(2):
        d.press("back")
        _sleep(0.5, 0.8)
    _log(log, f"seguidores do brechó: {len(novos)} novo(s) desde a última leitura (li {len(vistos)} do topo)"
              + ("" if completa else " — a lista parou de carregar antes dos conhecidos: a próxima lê de novo"))
    return len(novos)


# ─────────────────────────────── a fila de like ───────────────────────────────
def _refazer(e):
    """Visita que saiu zerada (0 foto, 0 story, sem destaque) volta pra fila UMA vez: até 11/11 a grade
    era conferida antes de carregar e o perfil com post passava como vazio."""
    return (isinstance(e, dict) and "curtidas" in e and not e.get("curtidas") and not e.get("stories")
            and not e.get("destaque") and not e.get("revisita"))


# 15/11: medido no log — 2388 reconferências acharam 48 privadas que aceitaram: 42 na 1ª, 6 na 2ª e NENHUMA da
# 3ª em diante. Reconferir todo dia por 7 dias reabria centenas de perfis por conta por dia (~20 min e peso no
# limite de ações do IG) pra nada; agora são no máximo 2 reconferências por privada.
RECONF_MAX = 2


def _reconferir_privado(e, ts_follow, agora):
    """Privada que a conta seguiu: reconfere se aceitou o follow (13/11, funil) — ~1x por dia, até RECONF_MAX
    vezes e até 7 dias depois do follow. Aceitou e não seguiu o brechó = a conta curte. `n` = quantas vezes a
    pessoa já foi vista privada (1 = a 1ª visita, sem reconferência ainda)."""
    try:
        vistas = int(e.get("n") or 1) if isinstance(e, dict) else 1
    except (TypeError, ValueError):
        vistas = 1
    return (isinstance(e, dict) and e.get("r") == "privado" and vistas <= RECONF_MAX
            and agora - float(e.get("ts") or 0) >= 20 * 3600 and agora - ts_follow <= 7 * 86400)


def fila_de_like(conta, est, seguidos=None, min_horas=24, max_dias=0):
    """Quem `conta` seguiu há mais de `min_horas` e não segue o brechó (nem já recebeu o like dela).
    Regra do Vinicius (09/11): primeiro os follows de até `max_dias` dias, do MAIS RECENTE pro mais
    antigo (o follow ainda tá fresco); acabou, segue pro acumulado antigo, também do mais recente."""
    seguidos = seguidos if seguidos is not None else seguidos_por_conta()
    agora = time.time()
    limite = agora - min_horas * 3600
    feitos = est["curtidos"].get(conta, {})
    fila = [(ts, u) for u, ts in seguidos.get(conta, {}).items()
            if ts <= limite and u not in est["seguidores"]
            and (u not in feitos or _refazer(feitos[u]) or _reconferir_privado(feitos[u], ts, agora))
            and not u.startswith(("quasenada", BRECHO))]
    fila.sort(reverse=True)
    if max_dias:
        corte = agora - max_dias * 86400
        fila = [x for x in fila if x[0] >= corte] + [x for x in fila if x[0] < corte]
    return [u for _ts, u in fila]


def panorama(est, seguidos=None, min_horas=24):
    """Por conta: seguidos, quantos viraram seguidores do brechó, tamanho da fila de like."""
    seguidos = seguidos if seguidos is not None else seguidos_por_conta()
    out = {}
    for conta, us in seguidos.items():
        viraram = sum(1 for u in us if u in est["seguidores"])
        out[conta] = {"seguidos": len(us), "seguem_brecho": viraram,
                      "fila": len(fila_de_like(conta, est, seguidos, min_horas))}
    return out


# ═══════════════════════════════ fase 2: o like ═══════════════════════════════
PADRAO = {
    "perfis": 20,          # perfis por conta por run
    "curtidas": 3,         # fotos curtidas por perfil
    "dias_min": 1,         # só quem foi seguido há pelo menos isso
    "dias_max": 7,         # prioridade: seguidos até isso (mais recente primeiro); depois o acumulado
    "stories": True,       # curte os stories que estiverem no ar
    "destaque": True,      # abre um destaque
    "max_stories": 4,
    "max_destaque": 3,     # telas do destaque curtidas
    "pausa": (0.0, 0.0),   # sem delay artificial (09/11): a navegação no emulador já é a simulação
    "frio": True,          # fila própria acabou → curte os curtidores dos posts do chat (funil, 13/11)
    "chat": "vaitomanoquasenada",
    "ler_seguidores": True,  # lê os seguidores NOVOS do brechó pela instância dele, 1x por dia (métrica)
}
PRIVADO_TXT = ("esta conta é privada", "este perfil é privado", "this account is private", "this profile is private")
INDISPONIVEL_TXT = ("página não está disponível", "pagina nao esta disponivel", "isn't available", "não foi encontrad",
                    "utilizador não encontrado", "user not found")


class Parado(Exception):
    pass


class _Bloqueio(Exception):
    """Bloqueio de ação no meio do lote: sai dos dois laços (o motivo já está no res)."""


def _cfg(params):
    c = dict(PADRAO)
    for k, v in (params or {}).items():
        if k not in PADRAO or v in (None, ""):
            continue
        if isinstance(PADRAO[k], bool):
            c[k] = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "sim", "on")
        elif isinstance(PADRAO[k], int):
            try:
                c[k] = max(0, int(v))
            except (TypeError, ValueError):
                pass
        elif isinstance(PADRAO[k], str):
            c[k] = str(v).strip() or PADRAO[k]
    return c


def _chk(parar):
    if parar and parar():
        raise Parado()


def _tocar(d, bb):
    d.click((bb[0] + bb[2]) // 2, (bb[1] + bb[3]) // 2)


def _fechar_ok(d):
    try:
        d(text="OK").click_exists(timeout=1)
    except Exception:
        pass


def abrir_perfil(d, user, log=None):
    """Perfil por link, SEM desistir de perfil sem posts (o scraper desiste). 'ok' | 'privado' |
    'indisponivel' | 'falha'."""
    try:
        d.shell(f"am start -a android.intent.action.VIEW -d https://www.instagram.com/{user}/ "
                f"-p {af.IG_PKG}")
    except Exception:
        return "falha"
    for _ in range(30):
        _sleep(0.35, 0.45)
        xml = _dump(d)
        low = xml.lower()
        # 14/11 (segue23): "Tentar mais tarde — Limitamos a frequência…" por cima do perfil — sem isto o bot
        # dava "falha" a cada 14 s e seguia abrindo perfil com a conta bloqueada
        if af._pedido_pendente(xml):
            # 15/11 (segue31): a janela do "pedido pendente" de um follow anterior fica presa por cima de
            # TODO perfil aberto depois (falha a cada 13 s até o IG reabrir) — fecha e segue
            _fechar_ok(d)
            continue
        blk = af._bloqueio(xml)
        if blk:
            return f"bloqueio:{blk}"
        if any(k in low for k in INDISPONIVEL_TXT):
            return "indisponivel"
        titulo = re.search(r'resource-id="[^"]*:id/action_bar_title"[^>]*text="([^"]*)"', xml) or \
            re.search(r'text="([^"]*)"[^>]*resource-id="[^"]*:id/action_bar_title"', xml)
        if titulo and titulo.group(1).strip().lower() == user.lower() and \
                ":id/profile_header_following_stacked_familiar" in xml:
            if any(k in low for k in PRIVADO_TXT):
                return "privado"
            return "ok"
    return "falha"


# "Sem resultados — Limitamos determinados elementos que podes ver…": a lista DAQUELE perfil não abre pra
# nós (dom.fiuza, 11/11) — não é limite da conta (a segue26 abre a lista do brechó normal)
LISTA_LIMITADA_KW = ("limitamos determinados elementos", "limitámos determinados elementos",
                     "we limit how many", "para proteger a nossa comunidade")


def segue_o_brecho(d, log=None):
    """No perfil ABERTO: abre "a seguir", pesquisa o brechó e vê se ele aparece na LISTA (o texto da
    própria pesquisa também tem o nome — só vale o follow_list_username). True/False; None se não deu."""
    alvo = d(resourceId=rid("profile_header_following_stacked_familiar"))
    if not alvo.exists:
        return None
    alvo.click()
    busca, achou = None, False
    for _ in range(20):                     # espera a busca (id muda por conta) OU o aviso de lista limitada
        busca = _busca_da_lista(d)
        if busca is not None:
            achou = True
            break
        try:
            low = _dump(d).lower()
        except Exception:
            low = ""
        if any(k in low for k in LISTA_LIMITADA_KW):
            d.press("back")
            _sleep(0.3, 0.4)
            return "limitada"
        _sleep(0.3, 0.4)
    if not achou:
        d.press("back")
        return None
    busca.click()
    try:
        busca.set_text(BRECHO)
    except Exception:
        d.send_keys(BRECHO)
    r = None
    for _ in range(20):
        _sleep(0.3, 0.4)
        xml = _dump(d)
        nomes = [_attr(m.group(0), "text").strip().lower() for m in re.finditer(r"<node[^>]*/?>", xml)
                 if _attr(m.group(0), "resource-id").endswith(":id/follow_list_username")]
        if BRECHO in nomes:
            r = True
            break
        if "nenhum utilizador" in xml.lower() or "no users found" in xml.lower() or "nenhum resultado" in xml.lower():
            r = False
            break
    for _ in range(4):        # teclado + lista → volta pro perfil
        if d(resourceId=rid("profile_header_following_stacked_familiar")).exists:
            break
        d.press("back")
        _sleep(0.3, 0.4)
    return r


def _coracoes(xml, H):
    """Botões de curtir do feed visíveis inteiros: [(bounds, curtido)] de cima pra baixo."""
    out = []
    for m in re.finditer(r"<node[^>]*/?>", xml):
        t = m.group(0)
        if not _attr(t, "resource-id").endswith(":id/row_feed_button_like"):
            continue
        bb = _bounds(t)
        if bb and bb[1] > 130 and bb[3] < H - 60:
            out.append((bb, _attr(t, "content-desc").strip().lower() == "gostei"))
    return sorted(out, key=lambda x: x[0][1])


_SEM_POSTS_TXT = ("ainda não há publicações", "ainda nao ha publicacoes", "no posts yet",
                  "sem publicações", "sem publicacoes")


def _n_posts(xml):
    """Nº de publicações do cabeçalho do perfil, ou None. Vem no content-desc do contador ("4publicações")
    OU só no texto do valor (profile_header_familiar_post_count_value, com o desc vazio — que_isso_brou,
    12/11: 0 posts, grade ainda em shimmer, passava como "grade não reconhecida")."""
    m = re.search(r'content-desc="(\d[\d.\s]*)\s*publica', xml)
    if not m:
        m = (re.search(r'text="([^"]*)"[^>]*resource-id="[^"]*:id/profile_header_familiar_post_count_value"', xml)
             or re.search(r'resource-id="[^"]*:id/profile_header_familiar_post_count_value"[^>]*text="([^"]*)"', xml))
    if not m:
        return None
    n = re.sub(r"\D", "", m.group(1))
    return int(n) if n else None


def _esperar_grade(d, timeout=6.0):
    """Espera a grade do perfil carregar: o cabeçalho chega antes das fotos, e conferir na hora dava
    '0 curtidas' em perfil com post (11/11: 15 de 124 visitas, todas 1-2 s depois do perfil anterior).
    True = tem miniatura; False = perfil sem posts (contador 0 / aviso) ou não carregou no tempo."""
    global _grade_motivo
    fim = time.time() + timeout
    while True:
        try:
            bb = bs._primeira_celula_grid(d)
            if bb:
                _grade_motivo = ""
                return bb                            # bounds da 1ª miniatura (o toque usa direto)
            xml = _dump(d)
        except Exception:
            xml = ""
        low = xml.lower()
        if any(k in low for k in PRIVADO_TXT):       # o aviso de privado chega DEPOIS do cabeçalho (12/11)
            _grade_motivo = "privado"
            return False
        if any(k in low for k in _SEM_POSTS_TXT):
            _grade_motivo = "sem posts"
            return False
        if _n_posts(xml) == 0:
            _grade_motivo = "sem posts"
            return False
        if time.time() >= fim:
            if not xml:                              # dump vazio = o device sumiu (12/11), não é layout
                _grade_motivo = "sem tela (device fora)"
                return False
            if f'package="{af.IG_PKG}"' not in xml:  # tela inicial do Android = o IG fechou (12/11)
                _grade_motivo = "o IG saiu da tela"
                return False
            # tem post mas a grade não foi reconhecida (11/11: nicollesda, ocaueh_) — guarda a tela pra ver
            _grade_motivo = "grade não reconhecida"
            try:
                pasta = os.path.join(BASE, "debug_like_fallback")
                os.makedirs(pasta, exist_ok=True)
                with open(os.path.join(pasta, time.strftime("%Y%m%d_%H%M%S") + ".xml"), "w",
                          encoding="utf-8") as f:
                    f.write(xml)
                _grade_motivo += " (tela salva em debug_like_fallback)"
            except Exception:
                pass
            return False
        _sleep(0.4, 0.5)


_grade_motivo = ""


def _bloqueio_1x(d):
    """Mesma checagem do gp._bloqueio (challenge, conta suspensa, verificação humana, bloqueio de AÇÃO)
    com UMA leitura de tela — o do Garimpo lê 3 vezes e era chamado 2x por perfil (12/11, rapidez)."""
    try:
        act = ((d.app_current() or {}).get("activity") or "").lower()
        if "challenge" in act:
            return "desafio"
        xml = _dump(d)
    except Exception:
        return None
    low = xml.lower()
    if any(k in low for k in af.SUSPENSA_KW):
        return "suspensa"
    if any(k in low for k in af.DESAFIO_HUMANO_KW):
        return "desafio_humano"
    b = af._bloqueio(xml)
    return f"bloqueio:{b}" if b else None


def _salvar_debug(d, prefixo, xml=None):
    """Guarda a tela atual em debug_like_fallback/ (pra investigar depois). Devolve o sufixo do log."""
    try:
        xml = xml if xml is not None else _dump(d)
        if not xml:
            return " (sem tela: device fora?)"
        pasta = os.path.join(BASE, "debug_like_fallback")
        os.makedirs(pasta, exist_ok=True)
        with open(os.path.join(pasta, f"{prefixo}_{time.strftime('%Y%m%d_%H%M%S')}.xml"), "w",
                  encoding="utf-8") as f:
            f.write(xml)
        return " (tela salva em debug_like_fallback)"
    except Exception:
        return ""


def _abrir_feed(d, cel):
    """Toca a 1ª miniatura e espera o feed do perfil (corações row_feed_button_like). 2 tentativas
    curtas (o toque às vezes se perde com a grade ainda assentando). Devolve o dump do feed ou None."""
    for tent in range(2):
        if tent:
            if not d(resourceId=rid("profile_header_following_stacked_familiar")).exists:
                d.press("back")
                _sleep(0.3, 0.4)
            cel = bs._primeira_celula_grid(d) or cel
        _tocar(d, cel)
        for _ in range(12):
            _sleep(0.25, 0.3)
            xml = _dump(d)
            if ":id/row_feed_button_like" in xml:
                return xml
    return None


def curtir_fotos(d, conta, n, log=None, parar=None):
    """Abre a 1ª miniatura e, no feed do perfil, toca nos corações ainda em "Gosto" que aparecem e rola
    (só toca no que não está curtido — o 2º toque descurtiria). Devolve quantas curtiu."""
    global _grade_motivo
    _grade_motivo = ""
    if n <= 0:
        return 0
    cel = _esperar_grade(d)
    if not cel:
        return 0
    try:
        W, H = d.window_size()
    except Exception:
        W, H = 900, 1600
    # 12/11 (rapidez): toca a miniatura que a espera já achou e confere o feed com leituras curtas — o
    # bs.entrar_no_feed do scraper re-acha a célula e espera 0,5-0,8 s antes + 0,8-1,3 s por conferência.
    # Ele fica de reserva se o toque rápido não abrir o feed.
    xml = _abrir_feed(d, cel)
    if xml is None:
        # 12/11: a reserva do scraper (bs.entrar_no_feed, até 3 tentativas) levou ~45 s no borgexsz1 e
        # também não curtiu nada — agora guarda a tela pra ver o que abriu e segue
        _grade_motivo = "o feed não abriu" + _salvar_debug(d, "feed")
        d.press("back")
        return 0
    feitas, parado, prev, tocados = 0, 0, None, set()
    viu_coracao, viu_livre = False, False
    for _ in range(n * 3 + 4):
        _chk(parar)
        cs = _coracoes(xml, H)
        viu_coracao = viu_coracao or bool(cs)
        viu_livre = viu_livre or any(not ok for _bb, ok in cs)
        livres = [bb for bb, ok in cs if not ok and bb not in tocados]
        if livres:
            # toca os corações livres da tela de uma vez e confere todos numa leitura só (antes: até
            # 5 leituras por foto). Só toca o que está em "Gosto" e nunca o mesmo 2x — não descurte.
            alvo = livres[:n - feitas]
            for bb in alvo:
                tocados.add(bb)
                _tocar(d, bb)
                _sleep(0.15, 0.2)
            _sleep(0.3, 0.35)
            xml = _dump(d)
            ok_agora = {bb for bb, ok in _coracoes(xml, H) if ok}
            feitas += sum(1 for bb in alvo if bb in ok_agora)
            if feitas >= n:
                break
            continue
        # 13/11: a assinatura era o tamanho do dump — vídeo tocando / "há X min" mudam o texto, o fim do
        # feed nunca era reconhecido e a conta ficava ~14 s rolando à toa. Agora: a POSIÇÃO dos corações e
        # dos cabeçalhos dos posts (só muda se o feed andou)
        assin = tuple(bb for bb, _ok in cs) + tuple(
            m.group(1) for m in re.finditer(
                r'resource-id="[^"]*:id/(?:row_feed_profile_header|row_feed_photo_profile_name)"[^>]*bounds="([^"]*)"',
                xml))
        parado = parado + 1 if assin == prev else 0
        if parado >= 2:                              # o feed não anda mais = acabaram os posts
            break
        prev = assin
        tocados.clear()                              # rolou: as posições antigas não valem mais
        d.swipe(W // 2, int(H * 0.78), W // 2, int(H * 0.28), 0.2)
        _sleep(0.45, 0.5)
        xml = _dump(d)
    if not feitas:                                   # feed aberto e nada curtido: diz o porquê (13/11)
        if not viu_coracao:
            _grade_motivo = "nenhum coração no feed" + _salvar_debug(d, "fotos0", xml)
        elif not viu_livre:
            _grade_motivo = "as fotos já estavam curtidas"
    d.press("back")                                  # feed → perfil (o próximo perfil abre por link)
    return feitas

def _perfil_aberto(d, user):
    """O perfil de `user` está aberto na frente (título = @ e o cabeçalho do perfil na tela)?"""
    try:
        xml = _dump(d)
    except Exception:
        return False
    t = (re.search(r'resource-id="[^"]*:id/action_bar_title"[^>]*text="([^"]*)"', xml)
         or re.search(r'text="([^"]*)"[^>]*resource-id="[^"]*:id/action_bar_title"', xml))
    return bool(t and t.group(1).strip().lower() == user.lower()
                and ":id/profile_header_following_stacked_familiar" in xml
                and "expanded_profile_pic_container" not in xml)


def _fechar_foto_ampliada(d):
    """Fecha a foto de perfil ampliada (expanded_profile_pic, com "Partilhar perfil"/"Código QR") se ela
    estiver por cima do perfil. True se fechou."""
    try:
        if d(resourceId=rid("expanded_profile_pic_container")).exists:
            d.press("back")
            _sleep(0.3, 0.4)
            return True
    except Exception:
        pass
    return False


def curtir_stories(d, user, maximo, log=None, parar=None):
    """Toca no avatar (só quando ele diz que tem história) e curte os stories da pessoa, um por um,
    até `maximo` ou até o viewer sair dela. Devolve quantos curtiu."""
    av = d(resourceId=rid("row_profile_header_imageview"))
    desc = _desc(av)                              # o avatar some/re-renderiza entre o exists e o info (11/11)
    if "hist" not in desc and "story" not in desc:
        return 0
    abriu = False
    for _ in range(2):
        try:
            av.click()
        except Exception:
            return 0
        if d(resourceId=rid("reel_viewer_title")).wait(timeout=4):
            abriu = True
            break
        # 12/11: o toque abriu a FOTO DE PERFIL ampliada em vez do story — ela fica por
        # cima e trava destaque e fotos. Fecha e tenta de novo uma vez.
        if not _fechar_foto_ampliada(d):
            break
    if not abriu:
        _fechar_foto_ampliada(d)
        return 0
    feitas = _curtir_viewer(d, maximo, user.lower(), parar)
    for _ in range(3):
        if d(resourceId=rid("profile_header_following_stacked_familiar")).exists:
            break
        d.press("back")
        _sleep(0.3, 0.4)
    return feitas


def _tela_viewer(d):
    """(dono, bounds do coração, já curtido?) do story/destaque aberto — numa leitura de tela só.
    dono "" = o viewer fechou."""
    try:
        xml = _dump(d)
    except Exception:
        return "", None, False
    dono, like, ja = "", None, False
    for m in re.finditer(r"<node[^>]*/?>", xml):
        t = m.group(0)
        r = _attr(t, "resource-id")
        if r.endswith(":id/reel_viewer_title"):
            dono = _attr(t, "text").strip().lower()
        elif r.endswith(":id/toolbar_like_button"):
            like = _bounds(t)
            ja = "reagir com gosto" not in _attr(t, "content-desc").lower()
    return dono, like, ja


def _curtir_viewer(d, maximo, dono=None, parar=None):
    """Curte as telas do story/destaque aberto até `maximo`, passando uma por uma; para quando o viewer
    sai do dono (`dono` None = o da 1ª tela, caso do destaque). 12/11 (rapidez): 1 leitura de tela por
    tela + toque por coordenada (antes ~8 chamadas por story). Só toca coração em "Reagir com Gosto"."""
    try:
        W, H = d.window_size()
    except Exception:
        W, H = 900, 1600
    feitas = 0
    for _ in range(maximo + 3):
        _chk(parar)
        atual, like, ja = _tela_viewer(d)
        if not atual:
            break
        if dono is None:
            dono = atual
        if atual != dono:
            break                                    # passou pra outra pessoa / o destaque acabou
        if like and not ja:
            _tocar(d, like)
            feitas += 1
            _sleep(0.25, 0.3)
        if feitas >= maximo:
            break
        d.click(int(W * 0.88), int(H * 0.42))        # próxima tela
        _sleep(0.35, 0.45)
    return feitas


def _desc(sel):
    try:
        return (sel.info.get("contentDescription") or "").lower() if sel.exists else ""
    except Exception:
        return ""


def _ponto_1o_destaque(xml):
    """(x, y) pra tocar a capa do 1º destaque da faixa pela posição: o nome (":id/label") fica embaixo da
    capa — toca no meio da capa, entre o topo da faixa e o nome. None se não achou a faixa/nome."""
    faixa = None
    for m in re.finditer(r"<node[^>]*/?>", xml):
        t = m.group(0)
        r = _attr(t, "resource-id")
        if r.endswith((":id/highlights_reel_tray_recycler_view", ":id/highlights_tray")):
            faixa = faixa or _bounds(t)
    if not faixa:
        return None
    nomes = []
    for m in re.finditer(r"<node[^>]*/?>", xml):
        t = m.group(0)
        bb = _bounds(t)
        if (_attr(t, "resource-id").endswith(":id/label") and bb and faixa[0] <= bb[0] and bb[2] <= faixa[2]
                and faixa[1] <= bb[1] and bb[3] <= faixa[3]):
            nomes.append(bb)
    if not nomes:
        return None
    n = min(nomes, key=lambda b: b[0])                # o 1º da esquerda
    return (n[0] + n[2]) // 2, (faixa[1] + n[1]) // 2


def abrir_destaque(d, maximo=3, log=None, parar=None):
    """Abre o 1º destaque e CURTE as telas dele (até `maximo`), passando uma por uma; volta pro perfil.
    Devolve quantas curtiu, ou None se não tinha destaque / não abriu."""
    alvo = d(descriptionContains="História em destaque de")
    try:
        if not alvo.exists:
            xml = _dump(d)
            if ":id/highlights_tray" not in xml and ":id/highlights_reel_tray" not in xml:
                return None                          # perfil sem destaque
            # 13/11 (perfil com 4 destaques saiu sem nenhum): a faixa de destaques está na tela mas o item não — espera ele carregar
            if alvo.wait(timeout=2.5):
                alvo.click()
            else:
                # 13/11 (kpone9d): o item está na faixa (com o nome embaixo) mas a capa veio SEM a descrição
                # "História em destaque de…" — toca pela posição: no meio da capa, logo acima do nome
                xml = _dump(d)
                ponto = _ponto_1o_destaque(xml)
                if not ponto:
                    _tec(log, "  (destaque: a faixa está na tela mas não achei o destaque"
                              + _salvar_debug(d, "destaque", xml) + ")")
                    return None
                _tec(log, "  (destaque sem descrição: toco o 1º da faixa pela posição)")
                d.click(*ponto)
        else:
            alvo.click()
    except Exception:
        return None
    if not d(resourceId=rid("reel_viewer_title")).wait(timeout=5):
        for _ in range(3):
            if d(resourceId=rid("profile_header_following_stacked_familiar")).exists:
                break
            d.press("back")
            _sleep(0.3, 0.4)
        return None
    feitas = _curtir_viewer(d, maximo, None, parar)  # o título do destaque é o nome dele, não o @
    for _ in range(4):
        if d(resourceId=rid("profile_header_following_stacked_familiar")).exists:
            break
        d.press("back")
        _sleep(0.3, 0.4)
    return feitas

def _busca_da_lista(d):
    """Caixa de pesquisa da lista de seguidores — o id muda conforme a conta."""
    for i in ("search_edit_text", "row_search_edit_text"):
        b = d(resourceId=rid(i))
        try:
            if b.exists:
                return b
        except Exception:
            pass
    return None


def abrir_seguidores_brecho(d, log=None):
    """Perfil do brechó → lista de seguidores. 'ok' (caixa de pesquisa na tela) | 'limitada' | None."""
    if abrir_perfil(d, BRECHO, log) != "ok":
        return None
    alvo = d(resourceId=rid("profile_header_followers_stacked_familiar"))
    if not alvo.exists:
        return None
    alvo.click()
    for _ in range(25):
        if _busca_da_lista(d) is not None:
            return "ok"
        try:
            low = _dump(d).lower()
        except Exception:
            low = ""
        if any(k in low for k in LISTA_LIMITADA_KW):
            d.press("back")
            return "limitada"
        _sleep(0.3, 0.4)
    d.press("back")
    return None


def pesquisar_seguidor(d, user):
    """Na lista de seguidores do brechó ABERTA: pesquisa o @. True = segue; False = não segue; None = não
    deu pra saber (lista limitada/sumiu/sem resposta)."""
    b = _busca_da_lista(d)
    if b is None:
        return None
    try:
        b.click()
        b.set_text(user)
    except Exception:
        return None
    for _ in range(16):
        _sleep(0.3, 0.4)
        try:
            xml = _dump(d)
        except Exception:
            continue
        low = xml.lower()
        if any(k in low for k in LISTA_LIMITADA_KW):        # tem "sem resultados" também — vem antes
            return None
        nomes = [_attr(m.group(0), "text").strip().lower() for m in re.finditer(r"<node[^>]*/?>", xml)
                 if _attr(m.group(0), "resource-id").endswith(":id/follow_list_username")]
        if user.lower() in nomes:
            return True
        if "sem resultados" in low or "nenhum utilizador" in low or "no users found" in low:
            return False
    return None


def conferir_lote(d, usuarios, log=None, parar=None):
    """Confere um lote na lista de seguidores do brechó (abre uma vez, pesquisa um por um).
    Devolve ({user: True/False/None}, estado da lista)."""
    out = {u: None for u in usuarios}
    st = abrir_seguidores_brecho(d, log)
    if st == "ok":
        for u in usuarios:
            _chk(parar)
            out[u] = pesquisar_seguidor(d, u)
    for _ in range(4):
        if d(resourceId=rid("profile_header_following_stacked_familiar")).exists:
            break
        d.press("back")
        _sleep(0.3, 0.4)
    return out, st


def colher_posts(d, log, nome_chat="vaitomanoquasenada", parar=None, teto=400, max_posts=3, min_nomes=150,
                 ja_colhidos=()):
    """FONTE FRIA (13/11, funil): abre os próximos posts pendentes do chat — a MESMA fronteira do Auto
    Follow (o post é consumido pela frota uma vez só: registro + ❤) —, lê os curtidores da folha
    "Gostos" rolando e marca cada post como feito. Colhe até `max_posts` posts ou `min_nomes` nomes por
    entrada no chat (chegar na fronteira leva ~2,5 min). Devolve [(chave, [usernames]), ...]."""
    colhidos, total = [], 0
    reg = af._feitos_carregar()
    if not af.abrir_thread(d, nome_chat, log):
        _log(log, "  não abri o chat pra colher curtidores")
        return colhidos
    if af.ir_para_fronteira(d, log, reg=reg, parar_flag=parar) is None:
        _log(log, "  não achei a fronteira do chat")
        return colhidos
    # 14/11 (anneloiise_ colhido 3x seguidas): post de legenda fraca (só emoji/pontuação) não entra no
    # registro da frota e o ❤ nem sempre gruda — sem isto ele volta a ser "pendente" na hora. `pulados`
    # guarda o que já foi visto NESTA passada; `ja_colhidos` (do funil, 14 dias) o que outras passadas colheram
    pulados = set(ja_colhidos or ())
    for _ in range(60):
        _chk(parar)
        af._assentar(d)
        posts, blk = af.posts_na_tela(d)
        if blk:
            _log(log, f"  !! bloqueio no chat: {blk}")
            return colhidos
        y_fr = max((p["y1"] for p in posts if p["reagido"] or af._feito_recente(reg, p["chave"])), default=None)
        pend = [p for p in posts if not p["reagido"] and p["ehpost"] and p["inteiro"]
                and p["chave"] not in pulados and not af._feito_recente(reg, p["chave"])
                and (y_fr is None or p["y1"] > y_fr)]
        if not pend:
            if not af._descer_ctrl(d):
                return colhidos                          # cheguei no post mais novo: não tem mais
            continue
        post = pend[0]
        _log(log, f"  colhendo curtidores do post de {post['autor']}…")
        d.click(*post["tap"])
        _sleep(2.0, 2.5)
        lc = d(resourceId=rid(af.LIKE_COUNT))
        if not lc.wait(timeout=8):
            _log(log, "  as curtidas não apareceram — pulo esse post nesta conta")
            pulados.add(post["chave"])
            d.press("back")
            _sleep(1.0, 1.5)
            continue
        lc.click()
        d(resourceId=rid(af.ROW_FOLLOW)).wait(timeout=8)
        try:
            _w, H = d.window_size()
        except Exception:
            H = 1600
        nomes, prev, parado = [], None, 0
        while len(nomes) < teto:
            _chk(parar)
            vis, _b = af._ler_folha(_dump(d), int(H * 0.14), int(H * 0.95))
            for u in vis:
                if u not in nomes:
                    nomes.append(u)
            atual = tuple(vis)
            parado = parado + 1 if (atual and atual == prev) or not atual else 0
            if parado >= 3:
                break
            prev = atual
            af._scroll_folha(d)
            _sleep(0.6, 0.8)
        d.press("back")
        _sleep(0.8, 1.2)
        d.press("back")
        _sleep(1.0, 1.4)
        if not nomes:
            _log(log, "  a folha de curtidores veio vazia — pulo esse post nesta conta")
            pulados.add(post["chave"])
            continue
        af._marcar_feito(reg, post["chave"])           # consumido pela frota (o Auto Follow pula)
        pulados.add(post["chave"])
        try:
            ok_coracao = af.marcar_post(d, getattr(log, "tecnico", None) or log, post["chave"])
            _tec(log, f"  reação no post de {post['autor']}: {'ok' if ok_coracao else 'não grudou'}")
        except Exception:
            pass
        colhidos.append((post["chave"], nomes))
        total += len(nomes)
        if len(colhidos) >= max_posts or total >= min_nomes:
            return colhidos
    return colhidos


def _texto_botao_follow(xml):
    """(texto em minúsculas, bounds) do botão Seguir/A seguir do cabeçalho do perfil aberto."""
    for m in re.finditer(r"<node[^>]*/?>", xml):
        t = m.group(0)
        if _attr(t, "resource-id").endswith(":id/profile_header_follow_button"):
            return _attr(t, "text").strip().lower(), _bounds(t)
    return "", None


_JA_SEGUE_TXT = ("a seguir", "pedido enviado", "solicitado", "following", "requested")
REABRIR_IG_CADA = 60      # perfis abertos por link até fechar e abrir o IG (memória do app, 13/11)


def _tec(log, msg):
    """Linha TÉCNICA: só no agente.log, não no log do app (13/11: "o log tem que ficar mais limpo")."""
    f = getattr(log, "tecnico", None)
    if f:
        f(msg)
    else:
        _log(log, msg)


def _limpar_ig(d, log, motivo):
    """Fecha e abre o IG (a conta continua a mesma) — cada perfil aberto por link fica empilhado no app."""
    try:
        af.reabrir_ig(d, getattr(log, "tecnico", None) or log, motivo)
    except Exception:
        pass


def seguir_funil(d, log, conta, limite=0, entre=(1.0, 2.0), parar=None, simular=False):
    """AUTO FOLLOW NO MODO FUNIL (13/11, desenho do Vinicius): a conta segue quem ELA tocou pelo Like
    Fallback — privadas que não deu pra curtir + curtidas há 1+ dia que não seguiram o brechó (tocadas nos
    últimos 7 dias, a mais recente primeiro). Abre o perfil pelo link e toca 'Seguir'; confere se colou
    (4 seguidos que o IG desfaz = soft-block). O log sai no MESMO formato do auto-follow ('+ @user (k/n)'):
    o agente conta por ele e o Like Fallback descobre por ele quem a conta seguiu."""
    est = carregar()
    fu = fn.carregar()
    fila = fn.fila_follow(fu, conta, est["seguidores"])
    _log(log, f"funil: {len(fila)} pessoa(s) pra @{conta} seguir primeiro (privadas que ela não pôde curtir + "
              f"curtidas há 1+ dia que não seguiram o brechó)")
    feitos, nao_colou, abertos, falhas_seg = 0, 0, 0, 0

    def _fim(ok, motivo):
        return {"ok": ok, "motivo": motivo, "follows": feitos, "posts": 0}

    def _travou(blk):
        """Bloqueio de ação na tela: fecha (OK) pra não ficar por cima da próxima conta e para."""
        _fechar_ok(d)
        _log(log, f"!! BLOQUEIO: {blk} — parando")
        return _fim(False, f"bloqueio:{blk}")

    if fila:
        _limpar_ig(d, log, "começo da conta: IG com a memória limpa")
    for u in fila:
        if parar and parar():
            return _fim(True, "parado")
        if limite and feitos >= limite:
            return _fim(True, "limite")
        abertos += 1
        if abertos % REABRIR_IG_CADA == 0:
            _limpar_ig(d, log, f"{abertos} perfis abertos: limpa a memória do IG")
        try:
            r = abrir_perfil(d, u, log)
        except Exception as e:
            r = f"erro:{str(e)[:60]}"
        if r.startswith("bloqueio"):
            return _travou(r.split(":", 1)[-1])
        if r == "falha" or r.startswith("erro"):
            # 15/11 (segue31): uma janela na frente faz TODO perfil "falhar" — 5 seguidas, para a conta
            falhas_seg += 1
            _tec(log, f"  - @{u}: {r}")
            if falhas_seg >= 5:
                _log(log, f"!! @{conta}: 5 perfis seguidos não abriram — paro esta conta")
                return _fim(False, "falhas_seguidas")
            continue
        falhas_seg = 0
        if r not in ("ok", "privado"):
            fn.marcar(fu, u, conta, "descartado")
            fn.salvar(fu)
            _log(log, f"  - @{u}: {r}")
            continue
        xml = _dump(d)
        if af._pedido_pendente(xml):                 # janela presa de um follow anterior: fecha
            _fechar_ok(d)
            xml = _dump(d)
        blk = af._bloqueio(xml)
        if blk:
            return _travou(blk)
        txt, bb = _texto_botao_follow(xml)
        if txt in _JA_SEGUE_TXT:
            fn.marcar(fu, u, conta, "seguido")
            fn.salvar(fu)
            _log(log, f"  = @{u}: a conta já seguia ({txt})")
            continue
        if not txt.startswith("seguir") or not bb:
            _log(log, f"  ? @{u}: não achei o botão de seguir ({txt or 'nenhum'}) — pulo")
            continue
        if simular:
            feitos += 1
            _log(log, f"  + @{u}  ({feitos}/{limite or feitos}) · simulado")
            continue
        _tocar(d, bb)
        _sleep(*entre)
        xml2 = _dump(d)
        if af._dialogo_revisao(xml2):               # "revê esta conta antes de seguires" → confirma
            af._confirmar_revisao(d, xml2)
            _sleep(1.0, 1.5)
            xml2 = _dump(d)
        pendente = af._pedido_pendente(xml2)
        blk2 = af._bloqueio(xml2)
        if blk2:
            return _travou(blk2)
        if pendente:
            # 15/11 (segue31): "O teu pedido está pendente — algumas contas preferem rever manualmente os
            # seguidores" logo depois do toque = o IG segurou ESSE follow (sinal de limite). Fecha a janela
            # (senão ela fica por cima de todo perfil seguinte) e conta como não colou: 4 seguidos param.
            _fechar_ok(d)
        txt2, _bb = _texto_botao_follow(xml2)
        if pendente or txt2.startswith("seguir"):
            nao_colou += 1
            _log(log, f"  ~ {u}: NÃO colou ({nao_colou}) — "
                      + ("o IG segurou o follow (pedido pendente)" if pendente else "IG desfez o follow"))
            if nao_colou >= 4:
                _log(log, "  !! SOFT-BLOCK: IG desfazendo os follows — parando (conta no limite)")
                return _fim(False, "softblock")
            continue
        nao_colou = 0
        feitos += 1
        fn.marcar(fu, u, conta, "seguido")
        fn.salvar(fu)
        _log(log, f"  + @{u}  ({feitos}/{limite or feitos})")
    return _fim(True, "fim")


def processar(d, log, conta, params=None, parar=None, rotulo=""):
    """Like Fallback de uma conta: até `perfis` pessoas da fila dela. Devolve o saldo."""
    cfg = _cfg(params)
    est = carregar()
    seguidos = seguidos_por_conta()
    fila = fila_de_like(conta, est, seguidos, cfg["dias_min"] * 24, cfg["dias_max"])
    rot = f"{rotulo} " if rotulo else ""
    res = {"perfis": 0, "curtidas": 0, "stories": 0, "destaques": 0, "ja_seguem": 0, "pulados": 0,
           "falhas": 0, "sem_conferir": 0, "frios": 0, "pend_follow": 0, "motivo": "fim"}
    meta = cfg["perfis"] or 20
    LOTE = 20
    fu = fn.carregar()
    _log(log, f"like fallback: {len(fila)} pessoa(s) na fila de @{conta} (seguidas há mais de "
              f"{cfg['dias_min']} dia(s) e que não seguem o brechó)"
              + (f"; depois, curtidores do chat (pool: {len(fu['pool'])})" if cfg["frio"] else ""))
    _log(log, f"[progress] 0 {meta} {rot}iniciando")

    def _funil(u, origem, etapa):
        if origem:                                   # só quem veio da fonte fria entra no funil
            fn.marcar(fu, u, conta, etapa, origem)
            fn.salvar(fu)

    def _lotes():
        for j in range(0, len(fila), LOTE):
            yield [(u, None) for u in fila[j:j + LOTE]]
        if not cfg["frio"]:
            return
        _log(log, "fila própria acabou — sigo pros curtidores dos posts do chat (funil)")
        vazios = 0
        while True:
            lote = fn.tirar_do_pool(fu, est, LOTE)
            if len(lote) < LOTE:
                recentes = {k for k, v in fu["posts"].items()
                            if time.time() - float((v or {}).get("ts") or 0) < 14 * 86400}
                colhidos = colher_posts(d, log, cfg["chat"], parar, ja_colhidos=recentes)
                if colhidos:
                    for chave, nomes in colhidos:
                        n = fn.adicionar_ao_pool(fu, est, nomes, f"chat:{chave}")
                        fu["posts"][chave] = {"ts": time.time(), "nomes": len(nomes), "novos": n, "conta": conta}
                        _log(log, f"  post de {chave.split('|')[0]}: {len(nomes)} curtidor(es), {n} que a frota "
                                  f"nunca tocou")
                        vazios = vazios + 1 if not n else 0
                    lote += fn.tirar_do_pool(fu, est, LOTE - len(lote))
                elif not lote:
                    _log(log, "  sem post novo no chat pra colher — acabou a fonte fria")
                    return
            fn.salvar(fu)
            if not lote:
                if vazios >= 5:
                    _log(log, "  5 posts seguidos sem ninguém novo — paro a fonte fria")
                    return
                continue
            yield lote

    restantes = []
    abertos = 0
    falhas_seg = 0
    _limpar_ig(d, log, "começo da conta: IG com a memória limpa")
    try:
        # a métrica de conversão (seguidores novos do brechó) é lida pela instância do brechó, no agente
        # (_ler_seguidores_pelo_brecho) e depois de cada story do brechó — não aqui (13/11)
        for lote_o in _lotes():
            _chk(parar)
            if res["perfis"] >= meta:
                restantes = list(lote_o)
                break
            restantes = list(lote_o)
            lote = [u for u, _o in lote_o]
            origens = dict(lote_o)
            # 1) CONFERE o lote na lista de seguidores do brechó (o processo certo — 11/11)
            conf, st_lista = conferir_lote(d, lote, log, parar)
            if st_lista != "ok":
                _log(log, f"  (a lista de seguidores do brechó não abriu nesta conta: {st_lista or 'falhou'} — "
                          f"confiro pela lista \"a seguir\" de cada um)")
            reconf = 0                           # privadas reconferidas neste lote (vão pro log técnico)
            for u in lote:
                _chk(parar)
                if res["perfis"] >= meta:
                    res["motivo"] = "meta"
                    break
                origem = origens.get(u)
                restantes = [x for x in restantes if x[0] != u]
                segue = conf.get(u)
                if segue is True:
                    res["ja_seguem"] += 1
                    agora = time.time()
                    est["seguidores"].setdefault(u, agora)
                    if origem:                       # frio que já seguia: não é conversão de ninguém
                        _funil(u, origem, "ja_seguia")
                        _log(log, f"  * @{u} já segue o brechó (veio do chat) — pulo")
                        continue
                    est["conversoes"].setdefault(conta, {})[u] = agora
                    salvar(est)
                    _log(log, f"  * @{u} JÁ SEGUE o brechó — conversão de @{conta}")
                    continue
                abertos += 1
                if abertos % REABRIR_IG_CADA == 0:
                    _limpar_ig(d, log, f"{abertos} perfis abertos: limpa a memória do IG")
                t0 = time.time()                     # tempo por etapa vai pro log (12/11, rapidez)
                try:
                    r = abrir_perfil(d, u, log)
                except Parado:
                    raise
                except Exception as e:
                    r = f"erro:{str(e)[:60]}"
                t_abrir = time.time() - t0
                if r.startswith("bloqueio"):
                    res["motivo"] = r
                    _log(log, f"!! @{conta}: bloqueio de ação do IG ({r.split(':', 1)[-1]}) — paro esta conta")
                    raise _Bloqueio()
                if r == "falha" or r.startswith("erro"):
                    # não grava como visitado (a pessoa continua na fila); 5 seguidas = tem algo na frente
                    falhas_seg += 1
                    res["pulados"] += 1
                    _tec(log, f"  - @{u}: {r}")
                    if falhas_seg >= 5:
                        res["motivo"] = "falhas_seguidas"
                        _log(log, f"!! @{conta}: 5 perfis seguidos não abriram — paro esta conta")
                        raise _Bloqueio()
                    continue
                falhas_seg = 0
                if r != "ok":
                    res["pulados"] += 1
                    ant = est["curtidos"].get(conta, {}).get(u)
                    n_priv = (int(ant.get("n", 1)) + 1) if isinstance(ant, dict) and ant.get("r") == "privado" else 1
                    est["curtidos"].setdefault(conta, {})[u] = {"ts": time.time(), "r": r, "n": n_priv}
                    salvar(est)
                    if r == "privado" and origem:
                        res["pend_follow"] += 1
                    _funil(u, origem, "pend_follow" if r == "privado" else "descartado")
                    if r == "privado" and n_priv > 1 and not origem:
                        # 13/11 ("o log tem que ficar mais limpo"): reconferência de privada vai pro técnico; o app
                        # vê só o resumo do lote
                        reconf += 1
                        _tec(log, f"  - @{u}: privado (reconferência {n_priv})")
                    else:
                        _log(log, f"  - @{u}: {r}" + (" (vai pro follow do funil)" if r == "privado" and origem else ""))
                    continue
                if segue is None:                    # 2º caminho: a lista "a seguir" da pessoa
                    try:
                        segue = segue_o_brecho(d, log)
                    except Parado:
                        raise
                    except Exception:
                        segue = None
                    if segue == "limitada":
                        segue = None
                    if segue is True:
                        res["ja_seguem"] += 1
                        agora = time.time()
                        est["seguidores"].setdefault(u, agora)
                        if origem:
                            _funil(u, origem, "ja_seguia")
                            continue
                        est["conversoes"].setdefault(conta, {})[u] = agora
                        salvar(est)
                        _log(log, f"  * @{u} JÁ SEGUE o brechó — conversão de @{conta}")
                        continue
                    if segue is None:                # nenhum caminho conferiu: NÃO curte, fica pra próxima
                        res["sem_conferir"] += 1
                        if origem:
                            fn.devolver_ao_pool(fu, u, origem)
                            fn.salvar(fu)
                        _log(log, f"  ? @{u}: não deu pra conferir se segue o brechó — não curto (fica pra próxima)")
                        continue
                # story e destaque PRIMEIRO: ficam no topo do perfil; depois das fotos a grade volta rolada e
                # o topo (avatar/destaques) some da tela (santsx, 09/11)
                t0 = time.time()
                try:
                    st = curtir_stories(d, u, cfg["max_stories"], log, parar) if cfg["stories"] else 0
                except Parado:
                    raise
                except Exception as e:
                    st = 0
                    _log(log, f"  ! @{u}: erro nos stories ({str(e)[:60]}) — sigo")
                t_st = time.time() - t0
                t0 = time.time()
                _fechar_foto_ampliada(d)             # nada por cima do perfil antes do destaque
                if st and not _perfil_aberto(d, u):  # o viewer do story pode ter saído pra outro lugar
                    abrir_perfil(d, u, log)
                try:
                    de = abrir_destaque(d, cfg["max_destaque"], log, parar) if cfg["destaque"] else None
                except Parado:
                    raise
                except Exception as e:
                    de = None
                    _log(log, f"  ! @{u}: erro no destaque ({str(e)[:60]}) — sigo")
                _fechar_foto_ampliada(d)             # ...nem antes das fotos
                if not d(resourceId=rid("profile_header_following_stacked_familiar")).exists:
                    abrir_perfil(d, u, log)              # voltou pra outro lugar: reabre o perfil pras fotos
                t_de = time.time() - t0
                t0 = time.time()
                b = _bloqueio_1x(d)
                if b:
                    res["motivo"] = b
                    raise _Bloqueio()
                t_bl = time.time() - t0
                t0 = time.time()
                try:
                    c = curtir_fotos(d, conta, cfg["curtidas"], log, parar)
                except Parado:
                    raise
                except Exception as e:
                    c = 0
                    res["falhas"] += 1
                    _log(log, f"  ! @{u}: erro nas fotos ({str(e)[:60]}) — sigo")
                t_fo = time.time() - t0
                t0 = time.time()
                b = _bloqueio_1x(d)
                if b:
                    res["motivo"] = b
                    raise _Bloqueio()
                t_bl += time.time() - t0
                if not c and not st and de is None and _grade_motivo == "privado":
                    # privado que o abrir_perfil não pegou (o aviso chegou depois): pulado, não é visita
                    res["pulados"] += 1
                    est["curtidos"].setdefault(conta, {})[u] = {"ts": time.time(), "r": "privado"}
                    salvar(est)
                    if origem:
                        res["pend_follow"] += 1
                    _funil(u, origem, "pend_follow")
                    _log(log, f"  - @{u}: privado" + (" (vai pro follow do funil)" if origem else ""))
                    continue
                res["perfis"] += 1
                res["curtidas"] += c
                res["stories"] += st
                res["destaques"] += int(de is not None)
                res["stories"] += de or 0                   # tela de destaque curtida conta como story curtido
                revisita = u in est["curtidos"].get(conta, {})     # 2ª visita (a 1ª saiu zerada)
                est["curtidos"].setdefault(conta, {})[u] = {"ts": time.time(), "conferiu": True,
                                                             "curtidas": c, "stories": st,
                                                             "destaque": de is not None, "destaque_curtidas": de or 0,
                                                             "revisita": revisita}
                salvar(est)
                if origem:
                    res["frios"] += 1
                _funil(u, origem, "curtido")
                curto = _grade_motivo.split(" (")[0] if (not c and _grade_motivo) else ""
                _log(log, f"  + @{u}: {c} curtida(s), {st} story(s)"
                          + (f", 1 destaque ({de} curtida(s))" if de is not None else "")
                          + (f" [{curto}]" if curto else ""))
                _tec(log, f"    @{u} t: abrir {t_abrir:.1f} story {t_st:.1f} destaque {t_de:.1f} fotos {t_fo:.1f} "
                          f"checagem {t_bl:.1f}s" + (f" | {_grade_motivo}" if not c and _grade_motivo else ""))
                _log(log, f"[progress] {res['perfis']} {meta} {rot}@{u}")
                _sleep(*cfg["pausa"])
            if reconf:
                _log(log, f"  ({reconf} privado(s) reconferido(s): continuam privados)")
            if res["perfis"] >= meta:
                break
        if res["perfis"] >= meta:
            res["motivo"] = "meta"
        elif res["motivo"] == "fim":
            res["motivo"] = "fila_vazia"
    except _Bloqueio:
        pass
    except Parado:
        res["motivo"] = "parado"
    except Exception as e:
        # 12/11: o device caiu no meio (segue23 com ~70 perfis feitos) e o erro subia pro agente, que
        # zerava o saldo da conta — devolve o PARCIAL com o motivo do erro (o estado já foi salvo por perfil)
        res["motivo"] = f"erro:{str(e)[:120]}"
        _log(log, f"  !! erro no meio da conta ({str(e)[:120]}) — fica o parcial: {res['perfis']} perfil(is)")
    finally:
        # quem saiu do pool e não chegou a ser visitado (meta, parada, erro) volta pro pool
        for u_r, o_r in restantes:
            if o_r:
                fn.devolver_ao_pool(fu, u_r, o_r)
        fn.salvar(fu)
        try:
            for _ in range(3):
                d.press("back")
                _sleep(0.4, 0.7)
            af.garantir_perfil(d, log)
        except Exception:
            pass
    if res["frios"] or res["pend_follow"]:
        _log(log, f"@{conta}: {res['frios']} curtido(s) vindos do chat, {res['pend_follow']} privado(s) pro "
                  f"follow do funil")
    if res["sem_conferir"]:
        _log(log, f"@{conta}: {res['sem_conferir']} perfil(is) sem como conferir o brechó — não curti, ficam pra próxima")
    _log(log, f"[progress] {res['perfis']} {meta} {rot}concluído")
    return res
