# -*- coding: utf-8 -*-
"""
generer_anonym_liste_fic.py
===========================
Génère SEULEMENT le fichier de paramétrage du programme SAS d'anonymisation du décisionnel
(Anonym_liste_fic_<domaine>.xlsx, onglet DOC).

C'est le DOSSIER DE DCP indiqué qui décide : une ligne par DCP présent dans ce dossier (les DCP
remplis à traiter), et seulement eux. L'inventaire de la cartographie sert uniquement à retrouver,
pour chaque DCP, le chemin du fichier plat correspondant (et son onglet, délimiteur, encodage).
Les autres DCP (dossier « tout », imports/exports non retenus) sont ignorés.

Rien n'est rescanné, aucun DCP n'est régénéré ni modifié.

Colonnes :
    A_TRAITER | CHEM_DCP | NOM_FIC_DCP | CHEM_FIC | NOM_FIC | DATE_FIC | FILTRE |
    DATA_VAR_ROW | ONGLET | DELIMITER | ENCODING
Rapprochement DCP <-> fichier plat : le nom du DCP est recalculé pour chaque fichier de
l'inventaire avec la MÊME fonction que la génération des DCP (empreinte du chemin + onglet).
Mise à A_TRAITER = 0 (surlignée en jaune, à vérifier) :
  * DCP sans fichier correspondant dans l'inventaire ;
  * fichier seulement écrit par les programmes (export : normalement pas à anonymiser) ;
  * fichier source introuvable sur disque ;
  * DCP dont des colonnes n'ont pas de Méthode_Anonymisation (signalé dans le contrôle).

Usage (depuis la racine des outils, venv activé) :
    python 03_anonymisation_dcp\\generer_anonym_liste_fic.py ^
        --inventaire 'M:\\...\\cartographie\\inventaire_fichiers_plats_Climatique.xlsx' ^
        --dcp 'M:\\...\\DCP_a_traiter\\Climatique' ^
        --domaine Climatique --date-effet 01/09/2026

Options :
    --sortie DOSSIER     où écrire le fichier (défaut : dossier parent du dossier DCP)
    --recursif           prendre aussi les DCP des sous-dossiers (par défaut : ce dossier seulement)
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

# Nom des DCP : on utilise la fonction de la cartographie du poste (01_inventaire_cartographie\cartographie)
# pour être sûr d'obtenir exactement les mêmes noms ; à défaut, la copie ci-dessous (même algorithme).
import hashlib
import re

ICI = Path(__file__).resolve().parent
_cands = [ICI.parent / "01_inventaire_cartographie", ICI, ICI.parent]
_cands += [p.parent.parent for p in ICI.parent.glob("*/cartographie/dcp_generator.py")]
_cands += [p.parent.parent for p in ICI.parent.glob("*/*/cartographie/dcp_generator.py")]
SOURCE_NOMS = "copie interne"
for cand in _cands:
    if (cand / "cartographie" / "dcp_generator.py").exists():
        sys.path.insert(0, str(cand))
        try:
            from cartographie.dcp_generator import _cle_physique, _nom_fichier_dcp  # noqa: F401
            SOURCE_NOMS = str(cand / "cartographie" / "dcp_generator.py")
        except Exception as e:  # noqa: BLE001
            print(f"[i] cartographie trouvée mais non importable ({type(e).__name__}: {e}) : copie interne utilisée")
        break

if SOURCE_NOMS == "copie interne":
    def _normaliser_chemin_phys(chemin: str) -> str:
        if not chemin:
            return ""
        p = str(chemin).strip().lower().rstrip("\\/")
        p = p.replace("/", "\\")
        p = re.sub(r"\\{2,}", r"\\", "\\\\" + p.lstrip("\\")) if p.startswith("\\") else re.sub(r"\\{2,}", r"\\", p)
        m = re.match(r"^\\\\([^\\]+)(\\.*)$", p)
        if m:
            p = "\\\\" + m.group(1).split(".")[0] + m.group(2)
        return p

    def _cle_physique(source_path: str, sheet: str) -> str:
        return _normaliser_chemin_phys(source_path) + "|" + (sheet or "").strip().upper()

    def _nom_fichier_dcp(chemin: str, onglet: str = "") -> str:
        base = Path(chemin).stem
        part = re.sub(r'[^A-Za-z0-9_-]', '_', base)
        if onglet:
            part += f"__{re.sub(r'[^A-Za-z0-9_-]', '_', onglet)}"
        h = hashlib.md5(_cle_physique(chemin, onglet or "").encode("utf-8", "replace")).hexdigest()[:8]
        return f"DCP_{part}__{h}.xlsx"

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


def lister_dcp(dossier: Path, recursif: bool) -> list[Path]:
    motif = dossier.rglob if recursif else dossier.glob
    return sorted(p for p in motif("DCP_*.xls*") if not p.name.startswith("~$"))


def methodes_manquantes(dcp: Path) -> str:
    """Nombre de colonnes sans Méthode_Anonymisation dans le DCP (lecture seule)."""
    try:
        wb = load_workbook(dcp, read_only=True, data_only=True)
        ws = wb["Données"] if "Données" in wb.sheetnames else wb.worksheets[0]
        lignes = ws.iter_rows(values_only=True)
        premiere = next(lignes, None)
        if premiere is None:
            wb.close()
            return "DCP vide"
        ent = [str(h or "").strip() for h in premiere]
        if "Méthode_Anonymisation" not in ent or "Nom de la donnée" not in ent:
            wb.close()
            return "colonnes Méthode_Anonymisation / Nom de la donnée absentes"
        im, inom = ent.index("Méthode_Anonymisation"), ent.index("Nom de la donnée")
        n = sum(1 for r in lignes if len(r) > inom and r[inom] and not (len(r) > im and r[im]))
        wb.close()
        return f"{n} colonne(s) sans méthode" if n else ""
    except Exception as e:  # noqa: BLE001
        return f"illisible ({type(e).__name__})"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Génère Anonym_liste_fic_<domaine>.xlsx pour les DCP d'un dossier.")
    ap.add_argument("--inventaire", required=True)
    ap.add_argument("--dcp", required=True, help="dossier des DCP remplis à traiter")
    ap.add_argument("--domaine", default="", help="suffixe du fichier : Anonym_liste_fic_<domaine>.xlsx")
    ap.add_argument("--sortie", help="dossier de sortie (défaut : dossier parent du dossier DCP)")
    ap.add_argument("--date-effet", default="", help="DATE_FIC, format jj/mm/aaaa (<= DATE_FIC du moteur SAS)")
    ap.add_argument("--recursif", action="store_true", help="inclure les sous-dossiers du dossier DCP")
    ap.add_argument("--sans-test-disque", action="store_true")
    a = ap.parse_args(argv)

    inv, dossier_dcp = Path(a.inventaire), Path(a.dcp)
    if not inv.is_file():
        sys.exit(f"[ERREUR] Inventaire introuvable : {inv}")
    if not dossier_dcp.is_dir():
        sys.exit(f"[ERREUR] Dossier DCP introuvable : {dossier_dcp}")
    dcps = lister_dcp(dossier_dcp, a.recursif)
    if not dcps:
        sys.exit(f"[ERREUR] Aucun fichier DCP_*.xlsx dans {dossier_dcp}"
                 + ("" if a.recursif else " (ajouter --recursif s'ils sont dans des sous-dossiers)"))
    occ = lire_inventaire(inv)

    # Toutes les combinaisons (fichier plat, onglet) de l'inventaire -> nom de DCP attendu
    fichiers: dict[str, dict] = {}
    for o in occ:
        chemin = o.get("chemin_resolu", "")
        if not chemin:
            continue
        if o.get("extension_inferee", "").upper() == "OUI" and o.get("extension") \
                and not chemin.lower().endswith(o["extension"].lower()):
            chemin += o["extension"]
        cle = _cle_physique(chemin, "").rstrip("|")
        f = fichiers.setdefault(cle, {"chemin": chemin, "occ": [], "entree": False})
        f["occ"].append(o)
        f["entree"] |= o.get("source_direction", "").upper() != "OUTPUT"
    par_nom: dict[str, tuple[dict, str]] = {}
    for f in fichiers.values():
        onglets = {o.get("sheet_resolu") or o.get("sheet") or "" for o in f["occ"]} | {""}
        for onglet in onglets:
            par_nom[_nom_fichier_dcp(f["chemin"], onglet).lower()] = (f, onglet)
    print(f"Noms de DCP calculés avec : {SOURCE_NOMS}")
    print(f"{len(dcps)} DCP à traiter dans {dossier_dcp} ; {len(fichiers)} fichier(s) plat(s) dans l'inventaire")

    lignes, controle = [], []
    for dcp in dcps:
        manque = methodes_manquantes(dcp)
        trouve = par_nom.get(dcp.name.lower())
        if not trouve:
            lignes.append({"A_TRAITER": 0, "CHEM_DCP": vers_unc(str(dcp.parent)), "NOM_FIC_DCP": dcp.name,
                           "CHEM_FIC": "", "NOM_FIC": "", "DATE_FIC": a.date_effet, "FILTRE": "",
                           "DATA_VAR_ROW": "", "ONGLET": "", "DELIMITER": "", "ENCODING": ""})
            controle.append([dcp.name, "", "", "À COMPLÉTER : aucun fichier plat de l'inventaire ne correspond "
                             "(inventaire d'un autre domaine ou DCP renommé ?)", manque])
            continue
        f, onglet = trouve
        chemin = f["chemin"]
        p = PureWindowsPath(chemin)
        src = [o for o in f["occ"] if o.get("source_direction", "").upper() != "OUTPUT"] or f["occ"]
        delim = next((o.get("delimiter") for o in src if o.get("delimiter")), "")
        enc = next((o.get("encoding") for o in src if o.get("encoding")), "")
        alertes = []
        if not f["entree"]:
            alertes.append("fichier seulement ÉCRIT par les programmes (export) : à anonymiser ?")
        if not a.sans_test_disque and not os.path.exists(chemin):
            alertes.append("fichier source introuvable sur disque")
        if manque:
            alertes.append(f"DCP : {manque}")
        lignes.append({"A_TRAITER": 0 if alertes else 1, "CHEM_DCP": vers_unc(str(dcp.parent)),
                       "NOM_FIC_DCP": dcp.name, "CHEM_FIC": str(p.parent), "NOM_FIC": p.name,
                       "DATE_FIC": a.date_effet, "FILTRE": "", "DATA_VAR_ROW": "", "ONGLET": onglet,
                       "DELIMITER": delim, "ENCODING": enc})
        controle.append([dcp.name, chemin, onglet, "À VÉRIFIER : " + " ; ".join(alertes) if alertes else "OK", manque])

    # ---- écriture (jamais d'écrasement)
    sortie = Path(a.sortie) if a.sortie else dossier_dcp.parent
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
    w2.append(["DCP", "Fichier plat (inventaire)", "Onglet", "Statut", "Remplissage du DCP"])
    for r in controle:
        w2.append(r)
    for j, w in enumerate([50, 70, 20, 70, 35], 1):
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
    print(f"  Contrôle ligne par ligne : {controle_path.name}")
    if not a.date_effet:
        print("  DATE_FIC vide : la remplir, ou relancer avec --date-effet jj/mm/aaaa")
    return 0


if __name__ == "__main__":
    sys.exit(main())
