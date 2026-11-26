# -*- coding: utf-8 -*-
"""
generer_anonym_liste_fic.py
===========================
Génère SEULEMENT le fichier de paramétrage du programme SAS d'anonymisation du décisionnel
(Anonym_liste_fic_<domaine>.xlsx, onglet DOC), à partir de ce qui existe déjà :

  * l'inventaire produit par la cartographie (inventaire_fichiers_plats*.xlsx, onglet Inventaire) ;
  * les DCP déjà générés et complétés (dossier, recherche dans les sous-dossiers).

Rien n'est rescanné, aucun DCP n'est régénéré ni modifié.

Une ligne par fichier SOURCE (et par onglet pour un Excel) :
    A_TRAITER | CHEM_DCP | NOM_FIC_DCP | CHEM_FIC | NOM_FIC | DATE_FIC | FILTRE |
    DATA_VAR_ROW | ONGLET | DELIMITER | ENCODING
Mêmes règles que generate_cartography.py --generate-parametrage :
  * les fichiers seulement écrits par les programmes (PROC EXPORT) sont exclus ;
  * les fichiers introuvables sur disque sont exclus ;
  * NOM_FIC_DCP est calculé avec la MÊME fonction que la génération des DCP, puis le DCP est
    cherché dans le dossier indiqué : CHEM_DCP = dossier où il se trouve réellement (en UNC).

Usage (depuis la racine des outils, venv activé) :
    python 03_anonymisation_dcp\\generer_anonym_liste_fic.py ^
        --inventaire 'M:\\...\\cartographie\\inventaire_fichiers_plats_Climatique.xlsx' ^
        --dcp 'M:\\...\\cartographie\\DCP\\Climatique' ^
        --domaine Climatique --date-effet 01/09/2026

Options :
    --sortie DOSSIER     où écrire le fichier (défaut : dossier de l'inventaire)
    --sous-dossier NOM   ne garder que les programmes situés sous ce sous-dossier
    --sans-test-disque   ne pas vérifier l'existence des fichiers sources
Un fichier existant n'est jamais écrasé : le nouveau est daté.
Un contrôle ligne par ligne est écrit à côté : controle_Anonym_liste_fic_<domaine>.xlsx
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path, PureWindowsPath

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

# Réutilise le code de la cartographie (01_inventaire_cartographie) : même nom de DCP, même UNC.
ICI = Path(__file__).resolve().parent
for cand in (ICI.parent / "01_inventaire_cartographie", ICI, ICI.parent):
    if (cand / "cartographie" / "dcp_generator.py").exists():
        sys.path.insert(0, str(cand))
        break
try:
    from cartographie.dcp_generator import _cle_physique, _nom_fichier_dcp
except ImportError:
    sys.exit("[ERREUR] Module cartographie introuvable : placer ce script dans 03_anonymisation_dcp\\ "
             "du kit (à côté de 01_inventaire_cartographie\\).")
try:
    from generate_cartography import _vers_unc_pour_sas as vers_unc
except Exception:  # noqa: BLE001
    def vers_unc(c):  # conversion lecteur -> UNC indisponible : chemin gardé tel quel
        return c

COLONNES = ["A_TRAITER", "CHEM_DCP", "NOM_FIC_DCP", "CHEM_FIC", "NOM_FIC",
            "DATE_FIC", "FILTRE", "DATA_VAR_ROW", "ONGLET", "DELIMITER", "ENCODING"]


def lire_inventaire(chemin: Path) -> list[dict]:
    wb = load_workbook(chemin, read_only=True, data_only=True)
    if "Inventaire" not in wb.sheetnames:
        sys.exit(f"[ERREUR] Onglet « Inventaire » absent de {chemin.name}")
    lignes = wb["Inventaire"].iter_rows(values_only=True)
    entetes = [str(h or "").strip() for h in next(lignes)]
    requis = {"source_direction", "chemin_resolu", "chemin_script"}
    if not requis <= set(entetes):
        sys.exit(f"[ERREUR] Colonnes manquantes dans l'inventaire : {sorted(requis - set(entetes))}")
    out = [{k: ("" if v is None else str(v).strip()) for k, v in zip(entetes, r)} for r in lignes]
    wb.close()
    return [o for o in out if any(o.values())]


def indexer_dcp(dossier: Path) -> dict[str, list[Path]]:
    index: dict[str, list[Path]] = {}
    for p in dossier.rglob("DCP_*.xls*"):
        if not p.name.startswith("~$"):
            index.setdefault(p.name.lower(), []).append(p)
    return index


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Génère Anonym_liste_fic_<domaine>.xlsx depuis l'inventaire et les DCP existants.")
    ap.add_argument("--inventaire", required=True)
    ap.add_argument("--dcp", required=True, help="dossier des DCP (sous-dossiers inclus)")
    ap.add_argument("--domaine", default="", help="suffixe du fichier : Anonym_liste_fic_<domaine>.xlsx")
    ap.add_argument("--sortie", help="dossier de sortie (défaut : dossier de l'inventaire)")
    ap.add_argument("--date-effet", default="", help="DATE_FIC, format jj/mm/aaaa (<= DATE_FIC du moteur SAS)")
    ap.add_argument("--sous-dossier", help="ne garder que les programmes situés sous ce sous-dossier")
    ap.add_argument("--sans-test-disque", action="store_true")
    a = ap.parse_args(argv)

    inv, dossier_dcp = Path(a.inventaire), Path(a.dcp)
    if not inv.is_file():
        sys.exit(f"[ERREUR] Inventaire introuvable : {inv}")
    if not dossier_dcp.is_dir():
        sys.exit(f"[ERREUR] Dossier DCP introuvable : {dossier_dcp}")
    occ = lire_inventaire(inv)
    if a.sous_dossier:
        cle = "\\" + a.sous_dossier.strip("\\/").lower() + "\\"
        occ = [o for o in occ if cle in "\\" + o["chemin_script"].replace("/", "\\").lower() + "\\"]
    index = indexer_dcp(dossier_dcp)
    print(f"{len(occ)} occurrence(s) lue(s), {sum(len(v) for v in index.values())} DCP trouvé(s) sous {dossier_dcp}")

    # Regroupement par fichier physique (comme fichiers_uniques dans la cartographie)
    fichiers: dict[str, dict] = {}
    for o in occ:
        chemin = o.get("chemin_resolu", "")
        if not chemin:
            continue
        if o.get("extension_inferee", "").upper() == "OUI" and o.get("extension") \
                and not chemin.lower().endswith(o["extension"].lower()):
            chemin += o["extension"]
        cle = _cle_physique(chemin, "").rstrip("|")
        f = fichiers.setdefault(cle, {"chemin": chemin, "entrees": [], "sorties": 0})
        if o.get("source_direction", "").upper() == "OUTPUT":
            f["sorties"] += 1
        else:
            f["entrees"].append(o)

    lignes, controle = [], []
    for f in sorted(fichiers.values(), key=lambda x: PureWindowsPath(x["chemin"]).name.lower()):
        chemin = f["chemin"]
        p = PureWindowsPath(chemin)
        if not f["entrees"]:
            controle.append([p.name, chemin, "", "", "EXCLU : fichier seulement écrit par les programmes"])
            continue
        if not a.sans_test_disque and not os.path.exists(chemin):
            controle.append([p.name, chemin, "", "", "EXCLU : fichier source introuvable sur disque"])
            continue
        onglets = sorted({o.get("sheet_resolu") or o.get("sheet") for o in f["entrees"]
                          if o.get("sheet_resolu") or o.get("sheet")}) or [""]
        delim = next((o.get("delimiter") for o in f["entrees"] if o.get("delimiter")), "")
        enc = next((o.get("encoding") for o in f["entrees"] if o.get("encoding")), "")
        for onglet in onglets:
            nom_dcp = _nom_fichier_dcp(chemin, onglet)
            trouves = index.get(nom_dcp.lower(), [])
            if len(trouves) == 1:
                statut, chem_dcp, a_traiter = "OK", vers_unc(str(trouves[0].parent)), 1
            elif len(trouves) > 1:
                statut = "À VÉRIFIER : DCP présent en plusieurs exemplaires (" + \
                         " | ".join(str(t.parent) for t in trouves) + ")"
                chem_dcp, a_traiter = vers_unc(str(trouves[0].parent)), 0
            else:
                statut, chem_dcp, a_traiter = "À VÉRIFIER : DCP introuvable dans le dossier indiqué", "", 0
            lignes.append({"A_TRAITER": a_traiter, "CHEM_DCP": chem_dcp, "NOM_FIC_DCP": nom_dcp,
                           "CHEM_FIC": str(p.parent), "NOM_FIC": p.name, "DATE_FIC": a.date_effet,
                           "FILTRE": "", "DATA_VAR_ROW": "", "ONGLET": onglet,
                           "DELIMITER": delim, "ENCODING": enc})
            controle.append([p.name, chemin, onglet, nom_dcp, statut])

    # ---- écriture (jamais d'écrasement)
    sortie = Path(a.sortie) if a.sortie else inv.parent
    sortie.mkdir(parents=True, exist_ok=True)
    suffixe = f"_{a.domaine}" if a.domaine else ""
    cible = sortie / f"Anonym_liste_fic{suffixe}.xlsx"
    if cible.exists():
        cible = sortie / f"Anonym_liste_fic{suffixe}_{datetime.now():%Y%m%d_%H%M}.xlsx"

    wb = Workbook()
    ws = wb.active
    ws.title = "DOC"  # onglet attendu par le programme SAS hérité
    for j, c in enumerate(COLONNES, 1):
        cell = ws.cell(row=1, column=j, value=c)
        cell.font = Font(name="Arial", bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
        cell.alignment = Alignment(horizontal="center")
    jaune = PatternFill("solid", fgColor="FFF2CC")
    for i, l in enumerate(lignes, 2):
        for j, c in enumerate(COLONNES, 1):
            cell = ws.cell(row=i, column=j, value=l[c])
            cell.font = Font(name="Arial", size=10)
            if l["A_TRAITER"] == 0:
                cell.fill = jaune
    for j, w in enumerate([10, 60, 45, 60, 35, 14, 10, 14, 20, 10, 10], 1):
        ws.column_dimensions[get_column_letter(j)].width = w
    ws.freeze_panes = "A2"

    wc = Workbook()
    w2 = wc.active
    w2.title = "Controle"
    w2.append(["Fichier", "Chemin source", "Onglet", "DCP attendu", "Statut"])
    for r in controle:
        w2.append(r)
    for j, w in enumerate([35, 70, 20, 50, 70], 1):
        w2.column_dimensions[get_column_letter(j)].width = w
    controle_path = cible.with_name("controle_" + cible.name)
    try:
        wb.save(cible)
        wc.save(controle_path)
    except PermissionError:
        sys.exit("[ERREUR] Fichier ouvert dans Excel : le fermer et relancer.")

    ok = sum(1 for l in lignes if l["A_TRAITER"] == 1)
    print(f"\n{cible}\n  {len(lignes)} ligne(s) : {ok} prête(s) (A_TRAITER=1), "
          f"{len(lignes) - ok} à vérifier (A_TRAITER=0, surlignées en jaune)")
    print(f"  Exclusions et contrôles : {controle_path.name}")
    if not a.date_effet:
        print("  DATE_FIC vide : la remplir, ou relancer avec --date-effet jj/mm/aaaa")
    return 0


if __name__ == "__main__":
    sys.exit(main())
