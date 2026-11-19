# -*- coding: utf-8 -*-
"""Réorganisation de M:\\DSIN\\Lot 1 par COPIE (les originaux ne sont jamais modifiés).

    python reorganiser_lot1.py --inventaire inventaire_M.csv                # SIMULATION (rien n'est écrit sur M: ni O:)
    python reorganiser_lot1.py --inventaire inventaire_M.csv --executer     # copie réelle
    python reorganiser_lot1.py --inventaire inventaire_M.csv --executer --limite 50   # essai sur 50 copies

Deux périmètres :
  * préparation des packages usine  ->  M:\\DSIN\\Migration_SAS_Python
  * travaux Onshore                 ->  O:\\DSIN\\POLORGA\\EP\\04-Projets équipe\\03-Transverse\\33-Mission SAS\\2-Rationalisation
  * ce qui sert aux deux            ->  M:\\DSIN\\Migration_SAS_Python\\00_Commun (une seule copie)
  * classement incertain            ->  <destination>\\99_Archives\\A_verifier\\<arborescence d'origine> + motif

Le classement suit des RÈGLES écrites ci-dessous (fonction classer). Chaque fichier reçoit :
  destination, niveau (Explicite / Déduit / Incertain), règle appliquée, motif.
Le parcours (usine / Onshore) d'un programme vient d'abord des indications explicites de
Desensibilisation\\synthese\\<domaine>\\usine et \\ONSHORE ; le CONTENU des programmes SAS est lu
pour rattacher les fichiers de données qu'ils importent.

Garanties de la copie :
  * aucune suppression, aucun déplacement, aucune modification dans la source ;
  * chaque copie est vérifiée par SHA-256 ; un fichier existant de contenu différent n'est jamais écrasé ;
  * une copie déjà identique est reconnue et ignorée (relance sans risque) ;
  * traçabilité source -> destination conservée dans <M>\\_tracabilite\\correspondances.csv.
"""
from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import json
import os
import re
import shutil
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PureWindowsPath

SOURCE_DEFAUT = r"M:\DSIN\Lot 1"
DEST_M_DEFAUT = r"M:\DSIN\Migration_SAS_Python"
DEST_O_DEFAUT = r"O:\DSIN\POLORGA\EP\04-Projets équipe\03-Transverse\33-Mission SAS\2-Rationalisation"
LOT = "Lot_01"
DOMAINES = ["Climatique", "Demandes", "Pilotage activité", "Reporting", "Satisfaction client"]

# Arborescence par domaine
DOSSIERS_M = ["01_Codes", "02_Carto_DCP", "03_Desensibilisation", "04_Recette", "05_Livraisons"]
SOUS_DOSSIERS_M = {"05_Livraisons": ["En_preparation", "Valides", "ZIP_envoyes", "Retours_usine"]}
DOSSIERS_O = ["01_Codes", "02_Donnees_dependances", "03_Tests", "04_Resultats", "05_Documentation"]
TRANSVERSE = "00_Transverse"            # éléments propres au lot, communs à ses domaines
EXT_DONNEES = {".xlsx", ".xlsm", ".xls", ".csv", ".txt", ".sas7bdat", ".json", ".xml", ".dat", ".bak"}


# =========================================================================== outils
def norm(s: str) -> str:
    """minuscules, sans accents, ponctuation -> espace."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def cle_programme(nom: str) -> str:
    """Nom de fichier ou de dossier -> clé du programme SAS auquel il se rattache.
    'Journal de X_desensibilise_sas.log', 'X_rapport_desensibilisation-1.txt', 'X_desensibilise' -> 'x'."""
    s = re.sub(r"(\.(sas|log|txt|xlsx|xls|csv|bak|egp|bat|zip))+$", "", nom, flags=re.I)
    s = re.sub(r"^journal de ", "", s, flags=re.I)
    for _ in range(3):
        s = re.sub(r"[ _-]*(desensibilise|rapport_desensibilisation|rapport_rapport_ig|rapport_ig|"
                   r"rapport|sas)([ _-]?\d)?$", "", s, flags=re.I)
    return norm(s)


def code_demande(cle: str) -> str | None:
    m = re.match(r"^(d\d{4}[a-z0-9]*)\b", cle)
    return m.group(1)[:5] if m else None


def domaine_dans(texte: str) -> str | None:
    t = norm(texte)
    for d in DOMAINES:
        if norm(d) in t:
            return d
    return None


def joindre(racine: str, rel: str) -> str:
    """racine + chemin relatif Windows (\\), en chemin du système courant."""
    return os.path.join(racine, *PureWindowsPath(rel).parts)


def lp(p: str | Path) -> str:
    """Chemin long Windows (> 260 caractères) : préfixe \\\\?\\ pour les appels système."""
    s = str(p)
    if os.name != "nt" or s.startswith("\\\\?\\") or len(s) < 240:
        return s
    s = os.path.abspath(s)
    return "\\\\?\\UNC\\" + s[2:] if s.startswith("\\\\") else "\\\\?\\" + s


def sha256(p: str | Path) -> str:
    h = hashlib.sha256()
    with open(lp(p), "rb") as f:
        for bloc in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(bloc)
    return h.hexdigest()


def mtime_iso(st) -> str:
    return datetime.fromtimestamp(st.st_mtime).replace(microsecond=0).isoformat()


# =========================================================================== décision
@dataclass
class Decision:
    perimetre: str          # M | O | COMMUN | ARCH_M | ARCH_O | IGNORE
    dest_rel: str           # relatif à la racine du périmètre
    niveau: str             # Explicite | Déduit | Incertain
    regle: str
    motif: str = ""
    domaine: str = ""
    programme: str = ""


@dataclass
class Fichier:
    rel: str                       # relatif à la source, séparateur \
    taille_inv: str = ""
    mtime_inv: str = ""
    dans_inventaire: bool = True
    present: bool | None = None    # constaté sur disque
    decision: Decision | None = None
    statut: str = ""
    sha: str = ""
    dest_abs: str = ""
    notes: list = field(default_factory=list)

    @property
    def parts(self):
        return PureWindowsPath(self.rel).parts

    @property
    def nom(self):
        return self.parts[-1]

    @property
    def ext(self):
        n = self.nom.lower()
        return ".xlsx.bak" if n.endswith(".xlsx.bak") else Path(n).suffix


# =========================================================================== parcours des programmes
class Parcours:
    """Parcours de chaque programme, par domaine, à partir des indications EXPLICITES."""

    def __init__(self, fichiers: list[Fichier], dossiers: list[str]):
        self.usine: dict[str, set] = {d: set() for d in DOMAINES}
        self.onshore: dict[str, set] = {d: set() for d in DOMAINES}
        self.desens: dict[str, set] = {d: set() for d in DOMAINES}
        self.exclu: set = set()
        chemins = [f.parts for f in fichiers] + [PureWindowsPath(d).parts for d in dossiers]
        for p in chemins:
            if len(p) >= 3 and p[0] == "Desensibilisation" and p[1] == "a exclure":
                self.exclu.add(cle_programme(p[2]))
            if len(p) < 4 or p[0] != "Desensibilisation" or p[1] != "synthese" or p[2] not in DOMAINES:
                continue
            dom, sous = p[2], p[3]
            if sous.lower() == "usine" and len(p) >= 5:
                self.usine[dom].add(cle_programme(p[4]))
                if len(p) >= 6 and p[-1].lower().endswith(".sas"):
                    self.usine[dom].add(cle_programme(p[-1]))
            elif sous.upper() == "ONSHORE" and len(p) >= 5:
                self.onshore[dom].add(cle_programme(p[4]))
            elif not norm(sous).startswith("a revoir"):
                self.desens[dom].add(cle_programme(sous))

    @staticmethod
    def _trouve(cle: str, ensemble: set) -> str | None:
        if cle in ensemble:
            return cle
        code = code_demande(cle)
        if code:
            for c in ensemble:
                if code_demande(c) == code:
                    return c
        proches = difflib.get_close_matches(cle, list(ensemble), n=1, cutoff=0.88)
        return proches[0] if proches else None

    def de(self, dom: str, nom: str) -> tuple[str, str, str]:
        """-> (parcours USINE|ONSHORE|COMMUN|INDETERMINE, niveau, justification)"""
        cle = cle_programme(nom)
        u = self._trouve(cle, self.usine.get(dom, set()))
        o = self._trouve(cle, self.onshore.get(dom, set()))
        x = self._trouve(cle, self.exclu)
        d = self._trouve(cle, self.desens.get(dom, set()))
        if u and o:
            return "COMMUN", "Explicite", "présent dans synthese\\usine ET synthese\\ONSHORE"
        if o:
            return "ONSHORE", "Explicite", "présent dans synthese\\ONSHORE"
        if u:
            return "USINE", "Explicite", "package dans synthese\\usine" + (" (aussi dans « a exclure »)" if x else "")
        if x:
            return "INDETERMINE", "Incertain", "dans « a exclure » (exclu du package usine) sans indication Onshore"
        if d:
            return "USINE", "Déduit", "désensibilisé dans synthese (préparation de package usine)"
        return "INDETERMINE", "Incertain", "aucune indication de parcours (ni usine, ni ONSHORE, ni désensibilisé)"


# =========================================================================== dépendances (lecture des programmes)
RE_FICHIER = re.compile(r"[\"']([^\"'\n]{1,300}?\.(?:xlsx|xlsm|xls|csv|txt|dat|xml|json))[\"']", re.I)


def references_sas(source: Path, fichiers: list[Fichier]) -> dict[str, set]:
    """nom de fichier de données (minuscules) -> {(domaine, programme)} qui le lisent."""
    refs: dict[str, set] = {}
    for f in fichiers:
        if f.ext != ".sas" or f.parts[0] not in DOMAINES or len(f.parts) != 2:
            continue
        try:
            brut = Path(lp(joindre(str(source), f.rel))).read_bytes()
        except OSError:
            continue
        code = brut.decode("cp1252", errors="replace")
        for m in RE_FICHIER.finditer(code):
            nom = PureWindowsPath(m.group(1)).name.lower()
            refs.setdefault(nom, set()).add((f.parts[0], f.nom))
    return refs


# =========================================================================== règles de classement
def nom_prog(nom: str) -> str:
    """Nom lisible du dossier programme (sans _desensibilise, rapport, extension)."""
    s = re.sub(r"(\.(sas|log|txt|xlsx|xls|csv|bak|egp|bat|zip))+$", "", nom, flags=re.I)
    s = re.sub(r"^Journal de ", "", s, flags=re.I)
    for _ in range(3):
        s = re.sub(r"[ _-]*(desensibilise|rapport_desensibilisation|rapport_rapport_IG|rapport_IG|RAPPORT_IG|"
                   r"rapport|sas)([ _-]?\d)?$", "", s, flags=re.I)
    return s.strip(" _-") or nom


def m_dom(dom, *chemin):
    return "\\".join(["Lots", LOT, dom, *chemin])


def archive(f: Fichier, motif: str, perim="ARCH_M", dom="") -> Decision:
    return Decision(perim, "\\".join(["99_Archives", "A_verifier", *f.parts]), "Incertain",
                    "archive", motif, dom)


def selon_parcours(f: Fichier, dom: str, nom: str, P: Parcours, chemin_m: list, chemin_o: list,
                   chemin_commun: list | None, regle: str) -> Decision:
    parc, niv, why = P.de(dom, nom)
    prog = nom_prog(nom)
    if parc == "USINE":
        return Decision("M", m_dom(dom, *chemin_m), niv, regle, why, dom, prog)
    if parc == "ONSHORE":
        return Decision("O", m_dom(dom, *chemin_o), niv, regle, why, dom, prog)
    if parc == "COMMUN" and chemin_commun is not None:
        return Decision("COMMUN", "\\".join(["Lots", LOT, dom, *chemin_commun]), niv, regle, why, dom, prog)
    if parc == "COMMUN":                     # artefact du parcours usine d'un programme commun
        return Decision("M", m_dom(dom, *chemin_m), niv, regle, why, dom, prog)
    d = archive(f, f"Parcours indéterminé : {why}", dom=dom)
    d.programme = prog
    return d


def est_outil(f: Fichier) -> bool:
    p = f.parts
    if p[0] == "Desensibilisation" and len(p) == 3 and p[1] == "synthese":
        return True
    if f.nom.upper() == "CLEAN_WORK.SAS":
        return True
    return p[0] == "migration_sas_python" and len(p) == 2 and f.ext in (".py", ".yml", ".yaml")


def classer(f: Fichier, P: Parcours, refs: dict) -> Decision:
    p, nom, ext = f.parts, f.nom, f.ext
    n = norm(nom)

    if nom.startswith("~$"):
        return Decision("IGNORE", "", "Explicite", "verrou_office", "fichier verrou temporaire d'Office (165 octets)")
    if est_outil(f):
        return Decision("COMMUN", "\\".join(["Outils", nom]), "Explicite", "outil_commun",
                        "outil partagé (une seule copie ; versions différentes signalées)")

    # ---------------------------------------------------------------- racine du lot
    if len(p) == 1:
        if n.startswith("modeoperatoire") or n.startswith("mode operatoire"):
            return Decision("COMMUN", f"Documentation\\{nom}", "Déduit", "documentation", "mode opératoire général")
        if "anonymisation et conformite" in n:
            dom = domaine_dans(nom)
            if dom:
                return Decision("M", m_dom(dom, "02_Carto_DCP", "Conformite", nom), "Explicite",
                                "note_conformite", "note d'anonymisation et de conformité du domaine", dom)
            return archive(f, "Note de conformité : domaine non identifiable dans le nom (« COMPTR » ?)")
        if n.startswith("libnames"):
            dom = domaine_dans(nom)
            if dom:
                return Decision("M", m_dom(dom, "01_Codes", "Lanceurs_macros", nom), "Déduit",
                                "libnames_domaine", "déclarations LIBNAME du domaine", dom)
        return archive(f, "Fichier à la racine du lot sans rattachement certain")

    # ---------------------------------------------------------------- programmes d'origine
    if p[0] in DOMAINES:
        dom = p[0]
        if len(p) > 2:
            return archive(f, "Sous-dossier inattendu dans un dossier de domaine", dom=dom)
        if "desensibilise" in n or "rapport desensibilisation" in n:
            return Decision("M", m_dom(dom, "03_Desensibilisation", nom_prog(nom), nom), "Explicite",
                            "desensibilisation_domaine", "copie désensibilisée / rapport (préparation usine)",
                            dom, nom_prog(nom))
        if n in ("date sas", "datej sas"):
            return Decision("M", m_dom(dom, "01_Codes", "Lanceurs_macros", nom), "Déduit", "macro_date",
                            "programme de paramétrage des dates (DATE.SAS)", dom)
        if ext in (".sas", ".egp", ".bat"):
            return selon_parcours(f, dom, nom, P, ["01_Codes", "SAS_origine", nom],
                                  ["01_Codes", "SAS_origine", nom], ["SAS_origine", nom], "code_origine")
        return archive(f, f"Fichier {ext} dans le dossier des programmes, sans rattachement", dom=dom)

    # ---------------------------------------------------------------- désensibilisation
    if p[0] == "Desensibilisation":
        if len(p) == 2:
            if n.startswith("suivi") or n.startswith("synthese"):
                return Decision("M", "\\".join(["Lots", LOT, TRANSVERSE, "03_Desensibilisation", "Suivi", nom]),
                                "Explicite", "suivi_desensibilisation", "suivi de la désensibilisation du lot")
            if ext == ".sas":
                return Decision("M", "\\".join(["Lots", LOT, TRANSVERSE, "01_Codes", "Lanceurs_macros", nom]),
                                "Déduit", "macro_lot", "programme utilitaire du lot (dates)")
            return archive(f, "Fichier non identifié à la racine de Desensibilisation")
        if p[1] == "a exclure":
            return archive(f, "Dossier « a exclure » : exclu du package usine, parcours Onshore à confirmer")
        if p[1] == "synthese" and len(p) >= 4 and p[2] in DOMAINES:
            dom, sous = p[2], p[3]
            reste = list(p[4:])
            if sous.lower() == "usine":
                pkg = p[4] if len(p) > 5 else nom_prog(nom)
                return Decision("M", m_dom(dom, "05_Livraisons", "En_preparation", *(p[4:] if len(p) > 5
                                                                                     else [pkg, nom])),
                                "Explicite", "package_usine",
                                "package préparé pour l'usine (dossier « usine » : validation non prouvée)",
                                dom, nom_prog(pkg))
            if sous.upper() == "ONSHORE":
                cible = ["03_Tests", "Logs_SAS", *reste] if ext == ".log" else ["01_Codes", "SAS", *reste]
                return Decision("O", m_dom(dom, *cible), "Explicite", "onshore",
                                "dossier synthese\\ONSHORE", dom, nom_prog(nom))
            if norm(sous).startswith("a revoir"):
                return archive(f, "Dossier « A revoir avec équipe »", dom=dom)
            if len(p) == 4:                                 # fichier à la racine du domaine dans synthese
                if ext == ".zip":
                    return selon_parcours(f, dom, nom, P, ["05_Livraisons", "En_preparation", nom_prog(nom), nom],
                                          ["04_Resultats", nom], None, "zip_package")
                if n.startswith("suivi"):
                    return Decision("M", m_dom(dom, "03_Desensibilisation", nom), "Déduit", "suivi_domaine",
                                    "suivi de désensibilisation du domaine", dom)
                if ext == ".log":
                    return selon_parcours(f, dom, nom, P, ["04_Recette", nom_prog(nom), "Logs", nom],
                                          ["03_Tests", "Logs_SAS", nom], None, "log_desensibilise")
                if ext in (".xlsx", ".xlsx.bak", ".csv"):
                    return selon_parcours(f, dom, nom, P, ["03_Desensibilisation", nom_prog(nom), "Donnees", nom],
                                          ["02_Donnees_dependances", nom], None, "donnees_desensibilisees")
                return selon_parcours(f, dom, nom, P, ["03_Desensibilisation", nom_prog(nom), nom],
                                      ["01_Codes", "SAS", nom], None, "code_desensibilise")
            # sous-dossier de programme hors usine/ONSHORE
            contenu = f.notes[0] if f.notes else set()
            if norm(sous).startswith("work"):
                return selon_parcours(f, dom, sous, P, ["04_Recette", "Tables_WORK", sous, *reste[:-1], nom],
                                      ["04_Resultats", "Tables_WORK", sous, *reste[:-1], nom], None, "tables_work")
            if ".sas7bdat" in contenu:
                return selon_parcours(f, dom, sous, P, ["05_Livraisons", "En_preparation", sous, *reste],
                                      ["04_Resultats", sous, *reste], None, "dossier_package")
            return selon_parcours(f, dom, sous, P, ["03_Desensibilisation", sous, *reste],
                                  ["01_Codes", "SAS", sous, *reste], None, "dossier_code_desensibilise")
        return archive(f, "Emplacement non prévu dans Desensibilisation")

    # ---------------------------------------------------------------- cartographie / DCP
    if p[0] == "cartographie":
        trans = ["Lots", LOT, TRANSVERSE]
        if len(p) >= 3 and p[1] == "DCP":
            if len(p) == 3:
                return Decision("M", "\\".join([*trans, "02_Carto_DCP", "DCP", nom]), "Déduit", "dcp_lot",
                                "DCP sans domaine dans le nom de dossier")
            dom = domaine_dans(p[2])
            if not dom:
                return archive(f, "Dossier de DCP sans domaine identifiable")
            queue = re.sub(re.escape(norm(dom)), "", norm(p[2])).strip()
            queue = "_".join(queue.upper().split()).replace("COMPLETION", "completion").replace("TOUT", "tout")
            dossier = "DCP" + (f"_{queue}" if queue else "")
            return Decision("M", m_dom(dom, "02_Carto_DCP", dossier, *p[3:]), "Explicite", "dcp_domaine",
                            "DCP du domaine (un dossier « DPO » ne prouve pas la validation)", dom)
        if len(p) >= 3 and p[1].lower().endswith("_dpo"):
            dom = domaine_dans(p[1])
            if dom:
                return Decision("M", m_dom(dom, "02_Carto_DCP", "Transmis_DPO", *p[2:]), "Explicite",
                                "transmis_dpo", "DCP copiés pour le DPO (validation non prouvée)", dom)
        if len(p) >= 3 and p[1] == "_xlsm_converted":
            return Decision("M", "\\".join([*trans, "02_Carto_DCP", "_xlsm_converted", *p[2:]]), "Explicite",
                            "xlsm_converti", "copies .xlsx des classeurs .xlsm (cartographie)")
        if len(p) >= 3 and p[1] == "sauvegarde_tri":
            dom = domaine_dans(nom)
            if dom:
                return Decision("M", m_dom(dom, "03_Desensibilisation", "Parametrage_anonymisation",
                                           "sauvegardes", nom), "Explicite", "sauvegarde_tri",
                                "sauvegarde d'Anonym_liste_fic avant tri A_TRAITER", dom)
        if len(p) == 2:
            dom = domaine_dans(nom)
            if n.startswith("anonym liste fic"):
                if dom:
                    return Decision("M", m_dom(dom, "03_Desensibilisation", "Parametrage_anonymisation", nom),
                                    "Explicite", "parametrage_anonymisation", "paramétrage du programme d'anonymisation", dom)
                return Decision("M", "\\".join([*trans, "03_Desensibilisation", "Parametrage_anonymisation", nom]),
                                "Déduit", "parametrage_anonymisation_lot", "paramétrage sans domaine dans le nom")
            if n.startswith("recensement dcp prets"):
                return Decision("M", "\\".join([*trans, "03_Desensibilisation", "Parametrage_anonymisation",
                                                "recensements", nom]), "Explicite", "recensement_dcp_prets",
                                "rapport de tri des DCP prêts")
            if n.startswith("recensement ambiguites"):
                return Decision("M", "\\".join([*trans, "03_Desensibilisation", nom]), "Explicite",
                                "ambiguites_ref_anonym", "incohérences ref_anonym remontées au décisionnel")
            if nom.startswith("."):
                return Decision("M", "\\".join([*trans, "02_Carto_DCP", "_cache", nom]), "Explicite",
                                "cache_cartographie", "cache technique de generate_cartography")
            if dom:
                return Decision("M", m_dom(dom, "02_Carto_DCP", nom), "Explicite", "cartographie_domaine",
                                "livrable de cartographie du domaine", dom)
            return Decision("M", "\\".join([*trans, "02_Carto_DCP", nom]), "Déduit", "cartographie_lot",
                            "livrable de cartographie sans domaine")
        return archive(f, "Emplacement non prévu dans cartographie")

    # ---------------------------------------------------------------- analyses du lot (migration_sas_python)
    if p[0] == "migration_sas_python" and len(p) == 2:
        trans = ["Lots", LOT, TRANSVERSE]
        if re.match(r"^\d\d_", nom):
            return Decision("M", "\\".join([*trans, "02_Carto_DCP", "Inventaire_lot", nom]), "Explicite",
                            "inventaire_lot", "inventaire des tables et bibliothèques du lot")
        if n.startswith("anonym liste fic"):
            dom = domaine_dans(nom)
            if dom and re.fullmatch(r"anonym liste fic " + re.escape(norm(dom)) + r" xlsx", n):
                return Decision("M", m_dom(dom, "03_Desensibilisation", "Parametrage_anonymisation", nom),
                                "Explicite", "parametrage_anonymisation", "paramétrage du programme d'anonymisation", dom)
            return archive(f, "Version de test ou intermédiaire d'Anonym_liste_fic (issue, test)")
        # fichier de données : rattaché au programme qui le lit (lecture des .sas)
        lecteurs = refs.get(nom.lower(), set())
        if lecteurs:
            doms = {d for d, _ in lecteurs}
            progs = ", ".join(sorted(pg for _, pg in lecteurs))
            if len(doms) == 1:
                dom = doms.pop()
                prog = sorted(lecteurs)[0][1]
                d = selon_parcours(f, dom, prog, P, ["01_Codes", "Dependances", nom],
                                   ["02_Donnees_dependances", nom], ["Dependances", nom], "dependance_lue")
                d.motif = f"lu par {progs} ; {d.motif}"
                return d
            return Decision("COMMUN", "\\".join(["Lots", LOT, "Dependances", nom]), "Déduit", "dependance_partagee",
                            f"lu par plusieurs domaines : {progs}")
        return archive(f, "Fichier de données non référencé par les programmes du lot")

    return archive(f, "Emplacement non prévu par les règles de classement")


# =========================================================================== inventaire et disque
def lire_inventaire(chemin: str, source: str) -> tuple[list[Fichier], list[str]]:
    fichiers, dossiers = [], []
    racine = source.rstrip("\\")
    with open(chemin, encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh, delimiter=";"):
            rel = r["Chemin_relatif"]
            if r["Chemin_complet"].rstrip("\\") == racine:
                continue
            if r["Type"] == "Dossier":
                dossiers.append(rel)
            else:
                fichiers.append(Fichier(rel, r.get("Taille_octets", ""), r.get("Derniere_modification", "")))
    return fichiers, dossiers


def constater(source: Path, fichiers: list[Fichier]) -> tuple[list[Fichier], list[str]]:
    """Compare l'inventaire au disque : fichiers nouveaux, disparus, modifiés, inaccessibles."""
    connus = {f.rel.lower(): f for f in fichiers}
    nouveaux, erreurs = [], []
    if not Path(lp(source)).is_dir():
        erreurs.append(f"Source inaccessible : {source}")
        return nouveaux, erreurs
    for racine, ds, fs in os.walk(lp(source), onerror=lambda e: erreurs.append(f"Dossier illisible : {e.filename}")):
        for nom in fs:
            absolu = Path(racine) / nom
            rel = str(PureWindowsPath(os.path.relpath(absolu, lp(source))))
            f = connus.get(rel.lower())
            if f is None:
                f = Fichier(rel, dans_inventaire=False)
                nouveaux.append(f)
            try:
                st = os.stat(absolu)
                f.present = True
                if f.dans_inventaire and (str(st.st_size) != f.taille_inv or mtime_iso(st) != f.mtime_inv[:19]):
                    f.notes.append("MODIFIE_DEPUIS_INVENTAIRE")
            except OSError as e:
                f.present = False
                f.notes.append(f"INACCESSIBLE ({e.strerror})")
    for f in fichiers:
        if f.present is None:
            f.present = False
            f.notes.append("INTROUVABLE (supprimé, renommé ou inaccessible)")
    return nouveaux, erreurs


# =========================================================================== plan
def contenu_dossiers(fichiers: list[Fichier]) -> dict[tuple, set]:
    """extensions présentes dans chaque dossier de programme de synthese\\<domaine>\\<dossier>."""
    c: dict[tuple, set] = {}
    for f in fichiers:
        p = f.parts
        if len(p) > 4 and p[0] == "Desensibilisation" and p[1] == "synthese":
            c.setdefault(p[:4], set()).add(f.ext)
    return c


def racines(dest_m: str, dest_o: str) -> dict:
    return {"M": dest_m, "COMMUN": os.path.join(dest_m, "00_Commun"), "ARCH_M": dest_m,
            "O": dest_o, "ARCH_O": dest_o}


def planifier(fichiers, dossiers, source, dest_m, dest_o, cache):
    P = Parcours(fichiers, dossiers)
    refs = references_sas(Path(source), [f for f in fichiers if f.present])
    contenu = contenu_dossiers(fichiers)
    R = racines(dest_m, dest_o)
    for f in fichiers:
        p = f.parts
        if len(p) > 4 and p[0] == "Desensibilisation" and p[1] == "synthese":
            f.notes.insert(0, contenu.get(p[:4], set()))
        else:
            f.notes.insert(0, set())
        f.decision = classer(f, P, refs)
        f.notes.pop(0)
        if not f.dans_inventaire and f.decision.niveau == "Incertain":
            f.decision.motif = "Nouveau fichier (absent de l'inventaire) ; " + f.decision.motif
        if f.decision.perimetre != "IGNORE":
            f.dest_abs = joindre(R[f.decision.perimetre], f.decision.dest_rel)
    departager_outils(fichiers, source, dest_m, cache)
    return P, refs


def departager_outils(fichiers, source, dest_m, cache):
    """Outils communs : une seule copie par nom ; une autre version (contenu différent) -> archives."""
    groupes: dict[str, list] = {}
    for f in fichiers:
        if f.decision and f.decision.regle == "outil_commun" and f.present:
            groupes.setdefault(f.nom.lower(), []).append(f)
    for nom, fs in groupes.items():
        if len(fs) < 2:
            continue
        infos = []
        for f in fs:
            src = joindre(source, f.rel)
            infos.append((os.stat(lp(src)).st_mtime, cache.empreinte(src), f))
        infos.sort(key=lambda x: x[0], reverse=True)
        ref_sha = infos[0][1]
        for _, sha, f in infos[1:]:
            if sha == ref_sha:
                f.decision.motif = f"doublon identique de {infos[0][2].rel}"
            else:
                d = archive(f, f"Autre version de l'outil commun {f.nom} (la plus récente est "
                               f"{infos[0][2].rel})")
                f.decision = d
                f.dest_abs = joindre(dest_m, d.dest_rel)


# =========================================================================== empreintes (cache)
class Cache:
    """Évite de recalculer les SHA-256 à chaque relance : clé = chemin + taille + date."""

    def __init__(self, chemin: Path | None):
        self.chemin, self.d = chemin, {}
        if chemin and Path(lp(chemin)).exists():
            try:
                self.d = json.loads(Path(lp(chemin)).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.d = {}

    def empreinte(self, p: Path) -> str:
        st = os.stat(lp(p))
        k = f"{p}|{st.st_size}|{st.st_mtime_ns}"
        if k not in self.d:
            self.d[k] = sha256(p)
        return self.d[k]

    def oublier(self, p: Path):
        for k in [k for k in self.d if k.startswith(f"{p}|")]:
            del self.d[k]

    def enregistrer(self):
        if self.chemin:
            Path(lp(self.chemin.parent)).mkdir(parents=True, exist_ok=True)
            Path(lp(self.chemin)).write_text(json.dumps(self.d), encoding="utf-8")


# =========================================================================== copie
def copier(fichiers, source, dest_m, dest_o, executer, limite, cache):
    vus: dict[str, tuple[str, str]] = {}        # destination -> (sha, source) copiée/constatée pendant ce passage
    n_copies = 0
    anciennes = copies_precedentes(dest_m)      # (source, destination) -> (sha, date) des passages précédents
    R = racines(dest_m, dest_o)
    def ordre(x):
        # à destination égale, la version la plus récente passe en premier et garde la place
        try:
            m = os.stat(lp(joindre(source, x.rel))).st_mtime if x.present else 0
        except OSError:
            m = 0
        return (x.dest_abs.lower(), -m, x.rel.lower())

    for f in sorted(fichiers, key=ordre):
        d = f.decision
        if d.perimetre == "IGNORE":
            f.statut = "IGNORE"
            continue
        if not f.present:
            f.statut = "NON_COPIE_" + ("INACCESSIBLE" if any("INACCESSIBLE" in n for n in f.notes) else "INTROUVABLE")
            continue
        src = joindre(source, f.rel)
        try:
            f.sha = cache.empreinte(src)
        except OSError as e:
            f.statut = f"NON_COPIE_INACCESSIBLE ({e.strerror})"
            continue
        dest = Path(f.dest_abs)
        # même destination déjà prise pendant ce passage
        if f.dest_abs.lower() in vus:
            sha_vu, src_vu = vus[f.dest_abs.lower()]
            if sha_vu == f.sha:
                f.statut = "DOUBLON_IDENTIQUE"
                f.notes.append(f"même contenu que {src_vu}")
                continue
            f.notes.append(f"COLLISION : {f.dest_abs} reçoit déjà {src_vu} (contenu différent, plus récent)")
            d2 = archive(f, f"Version antérieure : même destination que {src_vu} (plus récent, contenu différent)",
                         "ARCH_O" if d.perimetre == "O" else "ARCH_M", d.domaine)
            f.decision, f.dest_abs = d2, joindre(R[d2.perimetre], d2.dest_rel)
            dest = Path(f.dest_abs)
        if Path(lp(dest)).exists():
            try:
                sha_dest = cache.empreinte(dest)
            except OSError as e:
                f.statut = f"ERREUR_LECTURE_DESTINATION ({e.strerror})"
                continue
            if sha_dest == f.sha:
                f.statut = "DEJA_COPIE"
                vus[f.dest_abs.lower()] = (f.sha, f.rel)
                continue
            if f.decision.perimetre.startswith("ARCH"):
                f.statut = "COLLISION_ARCHIVE_NON_ECRASE"
                f.notes.append("l'archive existe avec un contenu différent")
                continue
            avant = anciennes.get((src.lower(), str(dest).lower()))
            if avant and avant[0] == sha_dest:
                f.notes.append(f"SOURCE_MODIFIEE_DEPUIS_COPIE du {avant[1][:10]} (ancienne copie conservée)")
                motif = (f"Nouvelle version du fichier source depuis sa copie du {avant[1][:10]} : "
                         "l'ancienne copie reste en place, à remplacer à la main si besoin")
            else:
                f.notes.append(f"COLLISION : {dest} existe avec un contenu différent (non écrasé)")
                motif = "Destination occupée par un contenu différent (non écrasé)"
            d2 = archive(f, motif,
                         "ARCH_O" if f.decision.perimetre == "O" else "ARCH_M", f.decision.domaine)
            f.decision, f.dest_abs = d2, joindre(R[d2.perimetre], d2.dest_rel)
            dest = Path(f.dest_abs)
            if Path(lp(dest)).exists():
                f.statut = "DEJA_COPIE" if cache.empreinte(dest) == f.sha else "COLLISION_ARCHIVE_NON_ECRASE"
                continue
        vus[f.dest_abs.lower()] = (f.sha, f.rel)
        if not executer:
            f.statut = "A_COPIER"
            continue
        if limite is not None and n_copies >= limite:
            f.statut = "REPORTE_LIMITE_TEST"      # destination normale conservée
            continue
        try:
            Path(lp(dest.parent)).mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".copie_en_cours")
            shutil.copy2(lp(src), lp(tmp))
            if sha256(tmp) != f.sha:
                os.remove(lp(tmp))
                f.statut = "ERREUR_VERIFICATION_SHA256"
                continue
            os.replace(lp(tmp), lp(dest))
            cache.oublier(dest)
            f.statut = "COPIE_OK"
            n_copies += 1
        except OSError as e:
            f.statut = f"ERREUR_COPIE ({e.strerror})"


def copies_precedentes(dest_m) -> dict:
    trace = Path(dest_m) / "_tracabilite" / "correspondances.csv"
    res = {}
    try:
        with open(lp(trace), encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh, delimiter=";"):
                res[(r["Source"].lower(), r["Destination"].lower())] = (r["SHA256"], r["Date"])
    except (OSError, KeyError):
        pass
    return res


def creer_structure(dest_m, dest_o, executer) -> list[str]:
    dossiers = [joindre(dest_m, x) for x in ("00_Commun\\Outils", "00_Commun\\Documentation",
                                             "99_Archives\\A_verifier", "_tracabilite")]
    dossiers += [joindre(dest_o, "99_Archives\\A_verifier")]
    for dom in [TRANSVERSE, *DOMAINES]:
        for d in DOSSIERS_M:
            base = joindre(dest_m, f"Lots\\{LOT}\\{dom}\\{d}")
            dossiers.append(base)
            dossiers += [os.path.join(base, s) for s in SOUS_DOSSIERS_M.get(d, [])]
    for dom in DOMAINES:
        dossiers += [joindre(dest_o, f"Lots\\{LOT}\\{dom}\\{d}") for d in DOSSIERS_O]
    if executer:
        for d in dossiers:
            Path(lp(d)).mkdir(parents=True, exist_ok=True)
    return dossiers


# =========================================================================== rapports
def ecrire_csv(chemin: Path, entetes: list, lignes: list):
    chemin.parent.mkdir(parents=True, exist_ok=True)
    with open(chemin, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(entetes)
        w.writerows(lignes)


def rapports(fichiers, P, refs, dossier: Path, source, executer, erreurs, dest_m):
    horo = datetime.now().isoformat(timespec="seconds")
    plan = []
    for f in sorted(fichiers, key=lambda x: x.rel.lower()):
        d = f.decision
        plan.append([joindre(source, f.rel), f.dest_abs, d.perimetre, d.domaine, d.programme,
                     d.niveau, d.regle, d.motif, f.statut, f.sha,
                     "oui" if f.dans_inventaire else "NOUVEAU", " | ".join(n for n in f.notes if isinstance(n, str))])
    entetes = ["Source", "Destination", "Perimetre", "Domaine", "Programme", "Niveau", "Regle", "Motif",
               "Statut", "SHA256", "Dans_inventaire", "Anomalies"]
    ecrire_csv(dossier / "plan_classement.csv", entetes, plan)
    ecrire_csv(dossier / "archives_a_verifier.csv", entetes, [l for l in plan if l[2].startswith("ARCH")])
    anomalies = [l for l in plan if l[11] or l[8].startswith(("NON_COPIE", "ERREUR", "COLLISION"))
                 or l[10] == "NOUVEAU"]
    ecrire_csv(dossier / "anomalies.csv", entetes, anomalies)
    deps = [[dom, prog, fichier, ", ".join(sorted({f.rel for f in fichiers if f.nom.lower() == fichier})) or "absent du lot"]
            for fichier, lecteurs in sorted(refs.items()) for dom, prog in sorted(lecteurs)]
    ecrire_csv(dossier / "dependances_programmes.csv", ["Domaine", "Programme", "Fichier_lu", "Present_dans_lot"], deps)
    if executer:
        trace = Path(dest_m) / "_tracabilite" / "correspondances.csv"
        neuf = not Path(lp(trace)).exists()
        Path(lp(trace.parent)).mkdir(parents=True, exist_ok=True)
        with open(lp(trace), "a", encoding="utf-8-sig" if neuf else "utf-8", newline="") as fh:
            w = csv.writer(fh, delimiter=";")
            if neuf:
                w.writerow(["Date", "Source", "Destination", "SHA256", "Statut", "Regle", "Motif"])
            for l in plan:
                if l[8] in ("COPIE_OK", "DEJA_COPIE", "DOUBLON_IDENTIQUE"):
                    w.writerow([horo, l[0], l[1], l[9], l[8], l[6], l[7]])
    # synthèse
    from collections import Counter
    st, pe, ni = Counter(l[8] for l in plan), Counter(l[2] for l in plan), Counter(l[5] for l in plan)
    lignes = [f"Réorganisation {LOT} — {horo} — {'EXÉCUTION' if executer else 'SIMULATION (rien écrit)'}",
              f"Source : {source}", f"Fichiers traités : {len(plan)}", "",
              "Par périmètre : " + ", ".join(f"{k}={v}" for k, v in sorted(pe.items())),
              "Par niveau    : " + ", ".join(f"{k}={v}" for k, v in sorted(ni.items())),
              "Par statut    : " + ", ".join(f"{k}={v}" for k, v in sorted(st.items())), "",
              f"Anomalies : {len(anomalies)} (anomalies.csv) — à vérifier : {pe.get('ARCH_M', 0) + pe.get('ARCH_O', 0)}"
              " (archives_a_verifier.csv)", *erreurs]
    (dossier / "synthese.txt").write_text("\n".join(lignes), encoding="utf-8")
    return "\n".join(lignes)


# =========================================================================== main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inventaire", required=True, help="inventaire CSV (Type;Nom;Chemin_complet;Chemin_relatif;...)")
    ap.add_argument("--source", default=SOURCE_DEFAUT)
    ap.add_argument("--dest-m", default=DEST_M_DEFAUT)
    ap.add_argument("--dest-o", default=DEST_O_DEFAUT)
    ap.add_argument("--executer", action="store_true", help="copier réellement (sinon simulation)")
    ap.add_argument("--limite", type=int, help="nombre maximal de copies (essai)")
    ap.add_argument("--rapports", help="dossier des rapports (défaut : ./rapports_reorganisation/<date>)")
    a = ap.parse_args(argv)

    horo = datetime.now().strftime("%Y%m%d_%H%M%S")
    dossier = Path(a.rapports or Path.cwd() / "rapports_reorganisation" / horo)
    fichiers, dossiers = lire_inventaire(a.inventaire, a.source)
    print(f"Inventaire : {len(fichiers)} fichier(s), {len(dossiers)} dossier(s)")
    nouveaux, erreurs = constater(Path(a.source), fichiers)
    fichiers += nouveaux
    print(f"Disque : {len(nouveaux)} nouveau(x) fichier(s), "
          f"{sum(1 for f in fichiers if not f.present)} absent(s) ou inaccessible(s)")
    for e in erreurs:
        print("  [!]", e)
    cache = Cache(Path(a.dest_m) / "_tracabilite" / "cache_empreintes.json" if a.executer
                  else dossier / "cache_empreintes.json")
    P, refs = planifier(fichiers, dossiers, a.source, a.dest_m, a.dest_o, cache)
    creer_structure(a.dest_m, a.dest_o, a.executer)
    copier(fichiers, a.source, a.dest_m, a.dest_o, a.executer, a.limite, cache)
    cache.enregistrer()
    texte = rapports(fichiers, P, refs, dossier, a.source, a.executer, erreurs, a.dest_m)
    print("\n" + texte + f"\n\nRapports : {dossier}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
