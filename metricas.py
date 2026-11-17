"""Métricas de captação (13/11/2026, pedido do Vinicius: "tem que metrificar tudo e todos os bots pra
saber o que capta mais cliente").

Junta o que cada bot já registra e monta a linha do tempo de cada pessoa tocada pela frota:
    follow  → linhas '+ @user (k/n)' do agente.log (Auto Follow, chat ou funil)
    like    → like_fallback_estado.json (visitas com curtida/story/destaque)
    dm      → dms_feitos.json (DM Followers)
e cruza com os seguidores do brechó (like_fallback_estado.json → "seguidores", com a data em que cada um
apareceu na lista — relida 1x por dia pelo Like Fallback).

Conversão = a pessoa aparece seguindo o brechó DEPOIS do 1º toque. Quem já estava na 1ª leitura completa
da lista (09/11) e foi tocado antes dela não dá pra datar: fica separado e fora da taxa.

    python -m bots.like_fallback.metricas            # tabela no terminal + metricas_resumo.json
"""
import collections
import datetime
import json
import os
import statistics
import time

from bots.like_fallback import funil as fn
from bots.like_fallback import like_fallback as lf

BASE = lf.BASE
DMS_PATH = os.path.join(BASE, "dms_feitos.json")
RESUMO_PATH = os.path.join(BASE, "metricas_resumo.json")
MATURIDADE_D = 3          # coorte "madura": 1º toque há pelo menos isso (deu tempo de converter)


def _ler_json(p):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def toques():
    """{user: [(ts, tipo, conta), ...]} ordenado por data — todo toque de toda a frota."""
    out = collections.defaultdict(list)
    for conta, us in lf.seguidos_por_conta().items():
        for u, ts in us.items():
            out[u].append((ts, "follow", conta))
    est = lf.carregar()
    for conta, us in est["curtidos"].items():
        for u, e in us.items():
            if isinstance(e, dict) and "curtidas" in e and (e.get("curtidas") or e.get("stories")
                                                            or e.get("destaque")):
                out[u].append((float(e.get("ts") or 0), "like", conta))
    for conta, info in _ler_json(DMS_PATH).items():
        for u, ts in ((info or {}).get("enviados") or {}).items():
            out[u.lower()].append((float(ts or 0), "dm", conta))
    for u in out:
        out[u].sort()
    return out


def _caminho(tl):
    """Rótulo do caminho pela ORDEM dos toques de follow/like (DM vira um '+ dm')."""
    seq = []
    for _ts, tipo, _c in tl:
        if tipo in ("follow", "like") and (not seq or seq[-1] != tipo):
            seq.append(tipo)
    if not seq:
        return "só dm"
    base = {("follow",): "só follow", ("like",): "só like", ("follow", "like"): "follow → like",
            ("like", "follow"): "like → follow"}.get(tuple(seq[:2]), " → ".join(seq))
    return base + (" + dm" if any(t == "dm" for _x, t, _c in tl) else "")


def calcular(agora=None):
    agora = agora or time.time()
    est = lf.carregar()
    seg = est["seguidores"]
    leituras = sorted(x.get("ts", 0) for x in est["leituras"])
    base_lida = leituras[0] if leituras else agora
    tl_all = toques()
    fu = fn.carregar()

    por_caminho = collections.defaultdict(lambda: {"tocados": 0, "convertidos": 0, "dias": []})
    por_conta = collections.defaultdict(lambda: {"tocados": 0, "convertidos": 0})
    maduros = collections.defaultdict(lambda: {"tocados": 0, "convertidos": 0})
    ja_seguiam, antes_base = 0, 0
    for u, tl in tl_all.items():
        t0 = tl[0][0]
        if t0 <= base_lida:
            # 16/11: tocado ANTES da 1ª leitura sai da conta inteira — quem converteu antes dela não tem data
            # (já estava na base) e só os que NÃO converteram ficavam: as contas antigas saíam com 0% e o
            # "só follow" com a taxa achatada (9788 tocados → 0,15%; só os novos: 0,52%)
            antes_base += 1
            continue
        if u in seg and seg[u] < t0:
            ja_seguiam += 1                          # já seguia antes do 1º toque (o auto-follow não filtra)
            continue
        cam = _caminho(tl)
        conv = u in seg and seg[u] >= t0
        c = por_caminho[cam]
        c["tocados"] += 1
        conta = tl[0][2]
        por_conta[conta]["tocados"] += 1
        if conv:
            c["convertidos"] += 1
            c["dias"].append((seg[u] - t0) / 86400)
            por_conta[conta]["convertidos"] += 1
        if agora - t0 >= MATURIDADE_D * 86400:
            maduros[cam]["tocados"] += 1
            maduros[cam]["convertidos"] += int(conv)

    def _taxa(x):
        return round(100.0 * x["convertidos"] / x["tocados"], 2) if x["tocados"] else 0.0

    etapas = collections.Counter(p.get("etapa") for p in fu["pessoas"].values())
    return {
        "gerado_em": agora,
        "base_seguidores": {"primeira_leitura": base_lida, "ultima_leitura": leituras[-1] if leituras else None,
                            "seguidores_conhecidos": len(seg)},
        "caminhos": {k: {"tocados": v["tocados"], "convertidos": v["convertidos"], "taxa": _taxa(v),
                         "dias_ate_converter": round(statistics.median(v["dias"]), 1) if v["dias"] else None}
                     for k, v in sorted(por_caminho.items(), key=lambda kv: -kv[1]["tocados"])},
        "caminhos_maduros": {k: {**v, "taxa": _taxa(v)} for k, v in maduros.items()},
        "contas": {k: {**v, "taxa": _taxa(v)} for k, v in sorted(por_conta.items())},
        "ja_seguiam_antes_do_toque": ja_seguiam,
        "tocados_antes_da_1a_leitura": antes_base,
        "funil": {"pool": len(fu["pool"]), "posts_colhidos": len(fu["posts"]), "etapas": dict(etapas)},
    }


def _data(ts):
    return datetime.datetime.fromtimestamp(ts).strftime("%d/%m %H:%M") if ts else "-"


def imprimir(r):
    b = r["base_seguidores"]
    print(f"Seguidores do brechó conhecidos: {b['seguidores_conhecidos']} (1ª leitura {_data(b['primeira_leitura'])},"
          f" última {_data(b['ultima_leitura'])})")
    print(f"\n{'caminho':24} {'tocados':>8} {'converteram':>12} {'taxa':>7} {'dias até':>9}")
    for k, v in r["caminhos"].items():
        print(f"{k:24} {v['tocados']:>8} {v['convertidos']:>12} {v['taxa']:>6}% {str(v['dias_ate_converter'] or '-'):>9}")
    print(f"\nSó quem foi tocado há {MATURIDADE_D}+ dias (deu tempo de converter):")
    for k, v in r["caminhos_maduros"].items():
        print(f"  {k:22} {v['tocados']:>8} {v['convertidos']:>8} {v['taxa']:>6}%")
    print(f"\n{'conta (1º toque)':22} {'tocados':>8} {'converteram':>12} {'taxa':>7}")
    for k, v in r["contas"].items():
        print(f"{k:22} {v['tocados']:>8} {v['convertidos']:>12} {v['taxa']:>6}%")
    print(f"\nfora da taxa: {r['ja_seguiam_antes_do_toque']} já seguiam o brechó antes do toque, "
          f"{r['tocados_antes_da_1a_leitura']} tocados antes da 1ª leitura (sem data)")
    f = r["funil"]
    print(f"funil: pool {f['pool']}, {f['posts_colhidos']} post(s) colhido(s), etapas {f['etapas']}")


def main():
    r = calcular()
    with open(RESUMO_PATH, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=1)
    imprimir(r)


if __name__ == "__main__":
    main()
