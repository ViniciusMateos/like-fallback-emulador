"""Funil de captação (13/11/2026, desenho do Vinicius): o LIKE vem primeiro e o FOLLOW vira a 2ª chamada.

    curtidores dos posts do chat ──► Like Fallback visita
        pública  → curte (stories, destaque, fotos) ......... etapa "curtido"
        privada  → não dá pra curtir ....................... etapa "pend_follow"
    Auto Follow (modo funil), a MESMA conta que tocou:
        "pend_follow" (privada)                  → segue
        "curtido" há 1+ dia e não segue o brechó → segue
        (quem não for seguido em 7 dias sai da fila de follow — o like já foi o toque)
    privada que aceitou o follow e em 1 dia não seguiu o brechó → o Like Fallback curte (fila própria
    da conta, reconfere ~1x por dia, no máximo 2 vezes, até 7 dias depois do follow — 15/11: da 3ª
    reconferência em diante ninguém mais aceitava)

Estado em funil.json (raiz do device, fora do git):
    pool    {user: {"origem": "chat:<chave do post>", "ts": colhido}}  curtidores ainda não tocados
    pessoas {user: {"conta", "etapa", "ts", "origem", "hist": [[ts, etapa, conta], ...]}}
    posts   {chave: {"ts", "nomes", "conta"}}                         posts do chat já colhidos
"""
import io
import json
import os
import re
import time

BASE = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
FUNIL_PATH = os.path.join(BASE, "funil.json")
AGENTE_LOG = os.path.join(BASE, "agente.log")
LF_ESTADO = os.path.join(BASE, "like_fallback_estado.json")

ESPERA_FOLLOW_H = 24          # curtido há pelo menos isso (e não seguiu o brechó) → entra na fila de follow
VALIDADE_FOLLOW_D = 7         # quem não foi seguido nesse prazo sai da fila de follow
ETAPAS_FINAIS = ("seguido", "ja_seguia", "descartado")

_RE_FOLLOW = re.compile(r"^\[\d\d:\d\d:\d\d\]   \+ @(\S+)  \(\d+/\d+\)")


def carregar():
    try:
        with open(FUNIL_PATH, "r", encoding="utf-8") as f:
            fu = json.load(f)
    except Exception:
        fu = {}
    fu.setdefault("pool", {})
    fu.setdefault("pessoas", {})
    fu.setdefault("posts", {})
    return fu


def salvar(fu):
    try:
        tmp = FUNIL_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(fu, f, ensure_ascii=False)
        os.replace(tmp, FUNIL_PATH)
    except Exception:
        pass


def marcar(fu, user, conta, etapa, origem=None, ts=None):
    """Move `user` pra `etapa` (pela `conta`), guardando o histórico. Tira do pool."""
    ts = ts or time.time()
    u = user.lower()
    p = fu["pessoas"].setdefault(u, {"conta": conta, "origem": origem, "hist": []})
    if origem and not p.get("origem"):
        p["origem"] = origem
    p["conta"] = conta or p.get("conta")
    p["etapa"] = etapa
    p["ts"] = ts
    p["hist"].append([round(ts), etapa, conta])
    fu["pool"].pop(u, None)
    return p


def _seguidos_frota():
    """Todo mundo que alguma conta da frota já seguiu (linhas '+ @user (k/n)' do agente.log)."""
    out = set()
    try:
        with io.open(AGENTE_LOG, encoding="utf-8", errors="replace") as f:
            for linha in f:
                m = _RE_FOLLOW.match(linha)
                if m:
                    out.add(m.group(1).lower())
    except Exception:
        pass
    return out


def tocados(fu, est):
    """Quem a frota já tocou de algum jeito (seguiu, curtiu, está no funil) ou já segue o brechó —
    ninguém daqui entra no pool de novo."""
    t = set(_seguidos_frota())
    for por_user in (est.get("curtidos") or {}).values():
        t.update(por_user)
    t.update(fu["pessoas"])
    t.update(est.get("seguidores") or {})
    return t


def adicionar_ao_pool(fu, est, nomes, origem):
    """Põe no pool os curtidores que ninguém da frota tocou. Devolve quantos entraram."""
    t = tocados(fu, est)
    agora = time.time()
    n = 0
    for u in nomes:
        u = (u or "").strip().lstrip("@").lower()
        if not u or u in t or u in fu["pool"] or u.startswith("quasenada") or u == "brechoquasenadaa":
            continue
        fu["pool"][u] = {"origem": origem, "ts": agora}
        n += 1
    return n


def tirar_do_pool(fu, est, n):
    """Os `n` primeiros do pool (ordem de colheita) que ainda não foram tocados. Saem do pool aqui —
    quem não for visitado (sem conferir, parada) volta com devolver_ao_pool."""
    t = tocados(fu, est)
    out = []
    for u in list(fu["pool"]):
        if len(out) >= n:
            break
        info = fu["pool"].pop(u)
        if u in t:
            continue
        out.append((u, info.get("origem")))
    return out


def devolver_ao_pool(fu, user, origem):
    fu["pool"].setdefault(user.lower(), {"origem": origem, "ts": time.time()})


def fila_follow(fu, conta, seguidores_brecho, agora=None):
    """Quem a `conta` deve seguir no modo funil: privadas que ela não pôde curtir + curtidas há 1+ dia
    que não seguiram o brechó — tudo tocado nos últimos 7 dias, do mais RECENTE pro mais antigo."""
    agora = agora or time.time()
    fila = []
    for u, p in fu["pessoas"].items():
        if p.get("conta") != conta or u in seguidores_brecho:
            continue
        idade = agora - float(p.get("ts") or 0)
        if idade > VALIDADE_FOLLOW_D * 86400:
            continue
        if p.get("etapa") == "pend_follow" or (p.get("etapa") == "curtido" and idade >= ESPERA_FOLLOW_H * 3600):
            fila.append((float(p.get("ts") or 0), u))
    fila.sort(reverse=True)
    return [u for _ts, u in fila]
