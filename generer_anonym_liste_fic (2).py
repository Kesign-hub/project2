# -*- coding: utf-8 -*-
"""
generer_anonym_liste_fic.py
===========================
Génère SEULEMENT le fichier de paramétrage du programme SAS d'anonymisation du décisionnel
(Anonym_liste_fic_<domaine>.xlsx, onglet DOC).

C'est le DOSSIER DE DCP indiqué qui décide : une ligne par DCP présent dans ce dossier (les DCP
remplis / validés), et seulement eux. L'inventaire de la cartographie sert uniquement à retrouver,
pour chaque DCP, le fichier plat correspondant (chemin, onglet, délimiteur, encodage).
Rien n'est rescanné, aucun DCP n'est modifié.

Colonnes de l'onglet DOC :
    A_TRAITER | CHEM_DCP | NOM_FIC_DCP | CHEM_FIC | NOM_FIC | DATE_FIC | FILTRE |
    DATA_VAR_ROW | ONGLET | DELIMITER | ENCODING

Tables SAS (.sas7bdat) : NOM_FIC = nom de la table SANS extension, CHEM_FIC = dossier (règle du programme
du décisionnel : pas d'extension = DBMS SAS, lecture via un LIBNAME sur CHEM_FIC).

Rapprochement DCP -> fichier plat, à partir du NOM du DCP :
    Ok_DCP_<fichier>__<onglet>.xlsx, DCP_<fichier>.xlsx, DCP_<fichier>_V2_12102026.xlsx,
    DCP_<fichier> A REVOIR.xlsx, DCP_<fichier>__<onglet>__<empreinte>.xlsx ...
    -> le préfixe (Ok_, DCP_), les mentions de version / date / « A REVOIR » et l'empreinte sont
       ignorés ; <fichier> et <onglet> sont comparés aux fichiers de l'inventaire (caractères
       spéciaux remplacés par « _ », comme à la génération des DCP).
    -> si l'inventaire ne précise pas d'onglet (import de la 1re feuille), l'onglet est repris du
       nom du DCP et son vrai nom est relu dans le fichier source quand il est accessible.
Plusieurs DCP pour le même fichier / onglet : un seul est retenu (pas « A REVOIR », puis préfixe
Ok_, puis le plus récent) ; les autres sont listés comme écartés dans le contrôle.

A_TRAITER = 0 (ligne surlignée en jaune, à vérifier) quand :
    * aucun fichier de l'inventaire ne correspond au DCP ;
    * le fichier n'est qu'ÉCRIT par les programmes (export) ;
    * le fichier source est introuvable sur disque.
Le contrôle indique aussi, pour information, le nombre de colonnes sans Méthode_Anonymisation.

Usage (depuis la racine des outils, venv activé ; chemins et noms avec espaces ENTRE APOSTROPHES) :
    python 03_anonymisation_dcp\\generer_anonym_liste_fic.py ^
        --inventaire 'M:\\...\\inventaire_fichiers_plats_Satisfaction client.xlsx' ^
        --dcp 'M:\\...\\DCP\\Satisfaction client DPO' ^
        --domaine 'Satisfaction client' --date-effet 01/11/2026 --sortie 'C:\\...\\sortie'
Options :
    --sortie DOSSIER     où écrire (défaut : dossier parent du dossier DCP)
    --recursif           prendre aussi les DCP des sous-dossiers
    --sans-test-disque   ne pas vérifier l'existence des fichiers sources
Un fichier existant n'est jamais écrasé : le nouveau est daté.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import warnings
from datetime import datetime
from pathlib import Path, PureWindowsPath

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

COLONNES = ["A_TRAITER", "CHEM_DCP", "NOM_FIC_DCP", "CHEM_FIC", "NOM_FIC",
            "DATE_FIC", "FILTRE", "DATA_VAR_ROW", "ONGLET", "DELIMITER", "ENCODING"]
EXT_EXCEL = (".xlsx", ".xlsm", ".xls")


# ------------------------------------------------------------------------------- utilitaires
def assainir(texte: str) -> str:
    """Même transformation que la génération des DCP : tout caractère hors [A-Za-z0-9_-] -> « _ »."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", texte or "")


def cle_nom_dcp(nom: str) -> tuple[str, bool]:
    """Nom de fichier DCP -> (clé « fichier__onglet », marqué A REVOIR ?)."""
    s = re.sub(r"\.xls[xm]?$", "", nom, flags=re.I)
    a_revoir = bool(re.search(r"a[ _-]*revoir", s, re.I))
    s = re.sub(r"^.*?DCP_", "", s, count=1, flags=re.I)                     # Ok_DCP_, DCP_ ...
    for motif in (r"[ _-]*a[ _-]*revoir$", r"__[0-9a-f]{8}$", r"_V\d+(_\d{6,8})?$", r"_\d{8}$",
                  r"[ _-]*\(\d+\)$"):
        s = re.sub(motif, "", s, flags=re.I)
    return s, a_revoir


_UNC: dict[str, str | None] = {}


def vers_unc(chemin: str) -> str:
    """M:\\... -> \\\\serveur\\partage\\... (via « net use ») ; inchangé si impossible."""
    c = str(chemin)
    if c.startswith("\\\\") or len(c) < 2 or c[1] != ":" or os.name != "nt":
        return c
    lecteur = c[:2].upper()
    if lecteur not in _UNC:
        _UNC[lecteur] = None
        try:
            r = subprocess.run(["net", "use", lecteur], capture_output=True, timeout=5)
            texte = (r.stdout or b"").decode("cp850", errors="replace")
            for ligne in texte.splitlines():
                pos = ligne.find("\\\\")
                if pos >= 0:
                    _UNC[lecteur] = ligne[pos:].strip().rstrip("\\/")
                    break
        except Exception:  # noqa: BLE001
            pass
    m = _UNC[lecteur]
    return m + "\\" + c[2:].lstrip("\\/") if m else c


def onglets_reels(chemin: str) -> list[str] | None:
    if not chemin.lower().endswith(EXT_EXCEL[:2]):
        return None
    try:
        wb = load_workbook(chemin, read_only=True)
        noms = list(wb.sheetnames)
        wb.close()
        return noms
    except Exception:  # noqa: BLE001
        return None


def methodes_manquantes(dcp: Path) -> str:
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
        tot = vides = 0
        for r in lignes:
            if len(r) > inom and r[inom]:
                tot += 1
                vides += not (len(r) > im and r[im])
        wb.close()
        return f"{vides} / {tot} colonne(s) sans méthode"
    except Exception as e:  # noqa: BLE001
        return f"illisible ({type(e).__name__})"


def lire_inventaire(chemin: Path) -> list[dict]:
    wb = load_workbook(chemin, read_only=True, data_only=True)
    if "Inventaire" not in wb.sheetnames:
        sys.exit(f"[ERREUR] Onglet « Inventaire » absent de {chemin.name}")
    lignes = wb["Inventaire"].iter_rows(values_only=True)
    entetes = [str(h or "").strip() for h in next(lignes)]
    if not {"source_direction", "chemin_resolu"} <= set(entetes):
        sys.exit("[ERREUR] Colonnes source_direction / chemin_resolu absentes de l'inventaire")
    out = [{k: ("" if v is None else str(v).strip()) for k, v in zip(entetes, r)} for r in lignes]
    wb.close()
    return [o for o in out if o.get("chemin_resolu")]


# ------------------------------------------------------------------------------- programme
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Génère Anonym_liste_fic_<domaine>.xlsx pour les DCP d'un dossier.")
    ap.add_argument("--inventaire", required=True)
    ap.add_argument("--dcp", required=True, help="dossier des DCP remplis à traiter")
    ap.add_argument("--domaine", default="")
    ap.add_argument("--sortie")
    ap.add_argument("--date-effet", default="")
    ap.add_argument("--recursif", action="store_true")
    ap.add_argument("--sans-test-disque", action="store_true")
    a = ap.parse_args(argv)

    inv, dossier_dcp = Path(a.inventaire), Path(a.dcp)
    if not inv.is_file():
        sys.exit(f"[ERREUR] Inventaire introuvable : {inv}")
    if not dossier_dcp.is_dir():
        sys.exit(f"[ERREUR] Dossier DCP introuvable : {dossier_dcp}")
    motif = dossier_dcp.rglob if a.recursif else dossier_dcp.glob
    dcps = sorted(p for p in motif("*.xls*") if "dcp" in p.name.lower() and not p.name.startswith("~$"))
    if not dcps:
        sys.exit(f"[ERREUR] Aucun DCP (*DCP*.xlsx) dans {dossier_dcp}")

    # Fichiers plats de l'inventaire, regroupés par chemin
    fichiers: dict[str, dict] = {}
    for o in lire_inventaire(inv):
        chemin = o["chemin_resolu"]
        ext = o.get("extension", "")
        if o.get("extension_inferee", "").upper() == "OUI" and ext and not chemin.lower().endswith(ext.lower()):
            chemin += ext
        f = fichiers.setdefault(chemin.lower(), {"chemin": chemin, "occ": [], "onglets": set(), "entree": False})
        f["occ"].append(o)
        if o.get("sheet_resolu") or o.get("sheet"):
            f["onglets"].add(o.get("sheet_resolu") or o.get("sheet"))
        f["entree"] |= o.get("source_direction", "").upper() != "OUTPUT"
    exact: dict[str, list[tuple[dict, str]]] = {}     # « fichier__onglet » ou « fichier »
    par_fichier: dict[str, list[dict]] = {}            # « fichier » seul
    for f in fichiers.values():
        st = assainir(PureWindowsPath(f["chemin"]).stem).lower()
        par_fichier.setdefault(st, []).append(f)
        for og in f["onglets"] | {""}:
            k = st + ("__" + assainir(og).lower() if og else "")
            exact.setdefault(k, []).append((f, og))

    def rapprocher(cle: str):
        """-> (fichier, onglet, onglet_à_relire) ou None"""
        brut, cle = cle, cle.lower()
        if cle in exact:
            f, og = exact[cle][0]
            return f, og, False
        # onglet absent de l'inventaire : « fichier__onglet » avec fichier connu sans onglet
        pos = [m.start() for m in re.finditer("__", cle)]
        for p in reversed(pos):
            st, og = cle[:p], brut[p + 2:]
            for f in par_fichier.get(st, []):
                return f, og, True
        return None

    print(f"{len(dcps)} DCP dans {dossier_dcp} ; {len(fichiers)} fichier(s) plat(s) dans l'inventaire")
    retenus: dict[tuple, list] = {}
    controle = []
    for dcp in dcps:
        cle, a_revoir = cle_nom_dcp(dcp.name)
        r = rapprocher(cle)
        if not r:
            retenus[("?", dcp.name)] = [(dcp, None, "", False, a_revoir)]
            continue
        f, og, relire = r
        retenus.setdefault((f["chemin"].lower(), og.lower()), []).append((dcp, f, og, relire, a_revoir))

    lignes = []
    for (_, _), groupe in sorted(retenus.items(), key=lambda kv: kv[1][0][0].name.lower()):
        groupe.sort(key=lambda x: (x[4], not x[0].name.lower().startswith("ok_"), -x[0].stat().st_mtime))
        dcp, f, og, relire, _ = groupe[0]
        for autre in groupe[1:]:
            controle.append([autre[0].name, f["chemin"] if f else "", og, "ÉCARTÉ : autre DCP pour le même fichier / onglet "
                             f"(retenu : {dcp.name})", methodes_manquantes(autre[0])])
        info = methodes_manquantes(dcp)
        if f is None:
            lignes.append({"A_TRAITER": 0, "CHEM_DCP": vers_unc(str(dcp.parent)), "NOM_FIC_DCP": dcp.name,
                           "CHEM_FIC": "", "NOM_FIC": "", "DATE_FIC": a.date_effet, "FILTRE": "", "DATA_VAR_ROW": "",
                           "ONGLET": "", "DELIMITER": "", "ENCODING": ""})
            controle.append([dcp.name, "", "", "À COMPLÉTER : aucun fichier de l'inventaire ne correspond à ce nom", info])
            continue
        chemin = f["chemin"]
        p = PureWindowsPath(chemin)
        alertes = []
        if relire and og:
            vrais = onglets_reels(chemin)
            corr = [n for n in (vrais or []) if assainir(n).lower() == og.lower()]
            if corr:
                og = corr[0]
            else:
                alertes.append("onglet repris du nom du DCP, à vérifier dans le fichier")
        if not f["entree"]:
            alertes.append("fichier seulement ÉCRIT par les programmes (export)")
        if not a.sans_test_disque and not os.path.exists(chemin):
            alertes.append("fichier source introuvable sur disque")
        bloquant = [x for x in alertes if not x.startswith("onglet")]
        src = [o for o in f["occ"] if o.get("source_direction", "").upper() != "OUTPUT"] or f["occ"]
        # Table SAS : le programme du décisionnel (F2_100_TRT_FICHIER) prend l'extension de NOM_FIC comme DBMS
        # et passe en mode SAS quand NOM_FIC n'a PAS d'extension (lecture _ANSRC.<NOM_FIC>, libname sur CHEM_FIC).
        est_table_sas = p.suffix.lower() == ".sas7bdat"
        nom_fic = p.stem if est_table_sas else p.name
        lignes.append({"A_TRAITER": 0 if bloquant else 1, "CHEM_DCP": vers_unc(str(dcp.parent)),
                       "NOM_FIC_DCP": dcp.name, "CHEM_FIC": vers_unc(str(p.parent)) if est_table_sas else str(p.parent),
                       "NOM_FIC": nom_fic,
                       "DATE_FIC": a.date_effet, "FILTRE": "", "DATA_VAR_ROW": "", "ONGLET": og or "",
                       "DELIMITER": next((o.get("delimiter") for o in src if o.get("delimiter")), ""),
                       "ENCODING": next((o.get("encoding") for o in src if o.get("encoding")), "")})
        controle.append([dcp.name, chemin, og, "À VÉRIFIER : " + " ; ".join(alertes) if alertes else "OK", info])

    # ------------------------------------------------------------------- écriture
    sortie = Path(a.sortie) if a.sortie else dossier_dcp.parent
    sortie.mkdir(parents=True, exist_ok=True)
    suffixe = f"_{a.domaine}" if a.domaine else ""
    cible = sortie / f"Anonym_liste_fic{suffixe}.xlsx"
    if cible.exists():
        cible = sortie / f"Anonym_liste_fic{suffixe}_{datetime.now():%Y%m%d_%H%M}.xlsx"

    wb = Workbook()
    ws = wb.active
    ws.title = "DOC"  # onglet attendu par le programme SAS
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
    for j, w in enumerate([10, 60, 50, 60, 40, 12, 10, 14, 25, 10, 10], 1):
        ws.column_dimensions[get_column_letter(j)].width = w
    ws.freeze_panes = "A2"

    wc = Workbook()
    w2 = wc.active
    w2.title = "Controle"
    w2.append(["DCP", "Fichier plat (inventaire)", "Onglet", "Statut", "Remplissage du DCP (information)"])
    for r in controle:
        w2.append(r)
    for j, w in enumerate([55, 90, 25, 70, 35], 1):
        w2.column_dimensions[get_column_letter(j)].width = w
    controle_path = cible.with_name("controle_" + cible.name)
    try:
        wb.save(cible)
        wc.save(controle_path)
    except PermissionError:
        sys.exit("[ERREUR] Fichier ouvert dans Excel : le fermer et relancer.")

    ok = sum(1 for l in lignes if l["A_TRAITER"] == 1)
    ecartes = sum(1 for r in controle if r[3].startswith("ÉCARTÉ"))
    print(f"\n{cible}\n  {len(lignes)} ligne(s) : {ok} prête(s), {len(lignes) - ok} à vérifier (jaune)"
          + (f" ; {ecartes} DCP en double écarté(s)" if ecartes else ""))
    print(f"  Contrôle ligne par ligne : {controle_path.name}")
    if not a.date_effet:
        print("  DATE_FIC vide : la remplir, ou relancer avec --date-effet jj/mm/aaaa")
    return 0


if __name__ == "__main__":
    sys.exit(main())
