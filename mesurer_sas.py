# -*- coding: utf-8 -*-
"""Mesure la complexité RÉELLE d'un programme SAS (hors commentaires et lignes vides).

    python mesurer_sas.py <dossier ou fichiers .sas> --sortie mesures.csv [--lanceur LANCEUR.sas]

Les mesures sont brutes (comptages) : la pondération, le score, la classe et les JH sont
calculés dans le classeur Excel (feuille Paramètres), pour rester visibles et ajustables.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

ICI = Path(__file__).resolve().parent
for cand in (ICI.parent / "pacifica_migration", ICI.parent.parent / "pacifica_migration",
             ICI.parent / "04_transcription_sas_python" / "pacifica_migration"):
    if (cand / "agent" / "analyse_sas.py").exists():
        sys.path.insert(0, str(cand))
        break
from agent.analyse_sas import analyser, instructions, lire_sas, retirer_commentaires  # noqa: E402

DECLARATIF = re.compile(r"^\s*(informat|format|length|label|attrib|input|keep|drop|rename|retain\s+\w+\s*;)\b", re.I)
PROCS_STAT = {"logistic", "reg", "glm", "genmod", "mixed", "arima", "forecast", "esm", "princomp", "factor",
              "cluster", "fastclus", "discrim", "hpforest", "hpsplit", "nlin", "lifetest", "phreg", "surveyselect"}
LIBS_HISTO = re.compile(r"^(HISTO|ARCHI|HIST)", re.I)


def compter(motif, texte):
    return len(re.findall(motif, texte, re.I | re.M))


def mesurer(chemin: Path) -> dict:
    code = lire_sas(chemin)
    propre = retirer_commentaires(code)
    lignes_code = [l for l in propre.splitlines() if l.strip()]
    decl = sum(1 for l in lignes_code if DECLARATIF.match(l))
    stmts = instructions(propre)
    low = propre.lower()
    f = analyser(code, chemin.stem)

    sql_blocs = re.findall(r"proc\s+sql\b.*?\bquit\s*;", propre, re.I | re.S)
    jointures_expl = sum(compter(r"\b(inner|left|right|full|cross)?\s*join\b", b) for b in sql_blocs)
    # jointures implicites : FROM a, b  (virgules dans la clause FROM)
    jointures_impl = 0
    for b in sql_blocs:
        for m in re.finditer(r"\bfrom\b(.*?)(?=\bwhere\b|\bgroup\b|\border\b|\bhaving\b|;|\)\s*(?:as\b|;))", b, re.I | re.S):
            jointures_impl += max(0, len([x for x in m.group(1).split(",") if x.strip()]) - 1)
    requetes_sql = sum(compter(r"\bcreate\s+(table|view)\b|\binsert\s+into\b|\bselect\b.*?\binto\s*:", b) for b in sql_blocs)

    toutes_src = {f"{l['lib']}.{l['table']}" for l in f.lectures if l["lib"] not in ("WORK", "SASHELP")}
    toutes_out = {f"{e['lib']}.{e['table']}" for e in f.ecritures if e["lib"] not in ("WORK",)}
    sources = sorted(s for s in toutes_src if "&" not in s.split(".")[-1] and s not in toutes_out)
    sorties = sorted(s for s in toutes_out if "&" not in s.split(".")[-1])
    def dynamique(s):   # nom de table construit par macro (hors bibliothèques de snapshot DATE&x.)
        return "&" in s and not re.match(r"^(DATE|CLI|MER)\w*&", s, re.I)
    sources = [s for s in sources if not dynamique(s)]
    sorties = [s for s in sorties if not dynamique(s)]
    dynamiques = sorted(s for s in toutes_src | toutes_out if dynamique(s))
    # logique incrémentale / historisée : une table permanente lue AVANT d'être réécrite
    # (état du mois précédent), ou réécrite par un DATA step qui se relit (data X; set X ...;)
    def lignes_de(liste, nom):
        return [x["ligne"] for x in liste if f"{x['lib']}.{x['table']}" == nom]
    incrementales = []
    for s in sorted(toutes_out):
        if dynamique(s):
            continue
        lect, ecr = lignes_de(f.lectures, s) or [10 ** 9], lignes_de(f.ecritures, s)
        # data X; set X NOUVEAU; : ajout à l'historique (un simple « set X; » est une mise à jour sur place)
        n = re.escape(s)
        append = bool(re.search(rf"\bdata\s+{n}\b[^;]*;\s*set\s+{n}\b(?:\s*\([^)]*\))?\s+[^;\s(]", propre, re.I))
        if min(lect) < min(ecr) or append:
            incrementales.append(s)
    work_creees = sorted({e["table"] for e in f.ecritures if e["lib"] == "WORK"})
    fichiers_ext = [x for x in f.fichiers if x["role"] in ("import", "infile", "libname_excel", "filename")]
    exports = [x for x in f.fichiers if x["role"] in ("export", "file")]
    pieges = {p["code"] for p in f.pieges}

    return {
        "Programme": chemin.stem.replace("_desensibilise", ""),
        "Fichier": chemin.name,
        "Lignes_physiques": code.count("\n") + 1,
        "Lignes_code": len(lignes_code),
        "Lignes_declaratives": decl,
        "Lignes_logique": len(lignes_code) - decl,
        "Instructions": len(stmts),
        "Taux_commentaires_vides": round(1 - len(lignes_code) / max(1, code.count("\n") + 1), 2),
        "Tables_sources": len(sources),
        "Tables_sorties_perm": len(sorties),
        "Tables_WORK": len(work_creees),
        "Tables_dynamiques": len(dynamiques),
        "Tables_incrementales": len(incrementales),
        "DATA_steps": compter(r"(^|;)\s*data\s+(?!_null_)\w", low),
        "PROC_SQL": compter(r"\bproc\s+sql\b", low),
        "Requetes_SQL": requetes_sql,
        "Jointures": jointures_expl + jointures_impl,
        "Jointures_externes": sum(compter(r"\b(left|right|full)\s+(outer\s+)?join\b", b) for b in sql_blocs),
        "PROC_SORT": compter(r"\bproc\s+sort\b", low),
        "NODUPKEY": compter(r"\bnodup(key)?\b", low),
        "DISTINCT_BY_ALL": compter(r"\bselect\s+distinct\b|\bby\s+_all_\b", low),
        "FIRST_LAST": compter(r"\b(first|last)\.\w", low),
        "RETAIN": compter(r"\bretain\b", low),
        "LAG": compter(r"\blag\d*\s*\(", low),
        "MERGE": compter(r"(^|;)\s*merge\b", low),
        "Macros_definies": compter(r"%macro\b", low),
        "Boucles_macro": compter(r"%do\b", low),
        "Conditions_macro": compter(r"%if\b", low),
        "Macro_variables": len(f.macros_utilisees),
        "Macro_var_externes": len(f.macros_inconnues) + len(f.macros_date_sas),
        "SYMPUT_INTO": compter(r"\bcall\s+symputx?\b|\binto\s*:", low),
        "INCLUDE": len(f.includes),
        "Fichiers_externes_lus": len(fichiers_ext),
        "Exports": len(exports),
        "Tables_histo": sum(1 for s in sources if LIBS_HISTO.match(s)),
        "Conversions_implicites": compter(r"\binput\s*\(|\bput\s*\(", low),
        "Longueurs_fixes": compter(r"(^|;)\s*length\b|\bsubstr\s*\(|\bindex[cw]?\s*\(", low),
        "PROC_stats": sum(v for k, v in f.procs.items() if k in PROCS_STAT),
        "PROC_TRANSPOSE": f.procs.get("transpose", 0),
        "PROC_agregation": sum(v for k, v in f.procs.items() if k in ("means", "summary", "tabulate", "rank")),
        "PROC_controle": sum(v for k, v in f.procs.items() if k in ("freq", "print", "contents", "compare")),
        "ARRAY_HASH": compter(r"\barray\b|\bdeclare\s+hash\b|\.definekey\b", low),
        "Date_du_jour": 1 if re.search(r"\b(today|date)\s*\(\s*\)", re.sub(r"(?im)^.*temps_(debut|fin).*$", "", propre), re.I) else 0,
        "Macro_entre_apostrophes": 1 if "MACRO_ENTRE_APOSTROPHES" in pieges else 0,
        "CALL_EXECUTE_SYSTEME": 1 if re.search(r"\bcall\s+execute\b|(^|;)\s*x\s+['\"]|%sysexec|\bsystask\b|filename\s+\w+\s+dde\b",
                                               propre, re.I | re.M) else 0,
        "Sources_detail": " | ".join(sources),
        "Sorties_detail": " | ".join(sorties),
        "Incrementales_detail": " | ".join(incrementales),
        "Fichiers_detail": " | ".join(sorted({x["chemin"] for x in fichiers_ext + exports if x["chemin"]}))[:500],
        # « DDE » = dégât des eaux dans IRSI, pas l'échange dynamique Windows ; today() de chronométrage ignoré
        "Pieges_detectes": ", ".join(sorted(pieges - {"COMMANDE_SYSTEME", "DATE_DU_JOUR"})),
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("chemins", nargs="+")
    ap.add_argument("--sortie", default="mesures_sas.csv")
    a = ap.parse_args(argv)
    fichiers = []
    for c in a.chemins:
        p = Path(c)
        fichiers += sorted(p.glob("*.sas")) if p.is_dir() else [p]
    lignes = [mesurer(p) for p in fichiers]
    # similarité entre programmes (code sans commentaires) : un quasi-doublon coûte bien moins cher
    import difflib
    codes = {r["Programme"]: [l.strip() for l in retirer_commentaires(lire_sas(p)).splitlines() if l.strip()]
             for r, p in zip(lignes, fichiers)}
    for r in lignes:
        meilleur, nom = 0.0, ""
        for autre, c in codes.items():
            if autre == r["Programme"] or len(c) < 20:
                continue
            moi = codes[r["Programme"]]
            if min(len(moi), len(c)) / max(len(moi), len(c), 1) < 0.5:
                continue
            s = difflib.SequenceMatcher(None, moi, c, autojunk=False).ratio()
            if s > meilleur:
                meilleur, nom = s, autre
        r["Similarite_max"] = round(meilleur, 2)
        r["Similaire_a"] = nom if meilleur >= 0.6 else ""
    with open(a.sortie, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(lignes[0]), delimiter=";")
        w.writeheader(); w.writerows(lignes)
    print(f"{len(lignes)} programme(s) mesuré(s) -> {a.sortie}")


if __name__ == "__main__":
    main()
