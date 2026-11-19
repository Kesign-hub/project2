# -*- coding: utf-8 -*-
"""Crée l'arborescence (vide) des lots de migration SAS -> Python, avec une notice dans chaque dossier.

    python creer_arborescence_lots.py --lots 2 3 4              # SIMULATION : affiche, n'écrit rien
    python creer_arborescence_lots.py --lots 2 3 4 --executer   # création réelle

Les domaines de chaque lot sont déjà connus du script (DOMAINES_PAR_LOT). --domaines 'X' 'Y' ne sert
que pour un domaine qui n'est pas dans cette liste.

Ce que fait le script :
  * crée les dossiers manquants sur M: (parcours usine) et sur O: (parcours Onshore) ;
  * dépose dans chaque dossier un _LISEZ-MOI.txt (à quoi sert le dossier, ce qu'on y met, la suite) ;
  * dépose à la racine de chaque lot 00_COMMENCER_ICI.txt et, sur M:, le fichier de suivi du lot.

Ce qu'il ne fait jamais :
  * toucher au Lot 1 (M:\\DSIN\\Lot 1 reste l'espace de travail en cours) : --lots 1 est refusé ;
  * supprimer, déplacer ou écraser quoi que ce soit : un fichier déjà présent est conservé tel quel,
    même s'il diffère (une notice complétée par l'équipe n'est donc jamais perdue).

Relancer le script est sans risque : il ne crée que ce qui manque. Pour ajouter un domaine plus tard,
relancer avec --lots N --domaines 'NouveauDomaine' --executer.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEST_M_DEFAUT = r"M:\DSIN\Migration_SAS_Python"
DEST_O_DEFAUT = r"O:\DSIN\POLORGA\EP\04-Projets équipe\03-Transverse\33-Mission SAS\2-Rationalisation"
OUTILS = r"O:\DSIN\POLORGA\EP\04-Projets équipe\03-Transverse\33-Mission SAS\9-Outils migration"
LISEZ_MOI = "_LISEZ-MOI.txt"

# Domaines de chaque lot et nombre de programmes prévus (tableau de découpage des lots, 857 programmes).
# « Création UGS/zone » : le / est interdit dans un nom de dossier Windows, il devient « - ».
DOMAINES_PAR_LOT = {
    2: {"Analyse des coûts techniques": 72, "Activité assistants": 105, "Récolte": 76, "Sinistres": 2},
    3: {"Digital": 26, "Bilan": 261, "Motifs contacts": 4, "Partenaires": 9, "Prévision": 16},
    4: {"Création UGS-zone": 1, "Recette MOA": 32, "Reporting": 79, "Routage flux": 5},
}
SIGNATURE = "Notice créée par creer_arborescence_lots.py. Vous pouvez la compléter : elle ne sera jamais écrasée."


# =============================================================================== notices
# Variables disponibles : {lot} (Lot 02), {lot_dir} (Lot_02), {domaine}, {m}, {o}, {outils}

RACINE_M = """\
MIGRATION SAS -> PYTHON : ESPACE DE PRÉPARATION DES PACKAGES (PARCOURS USINE)

Ici, on prépare les programmes SAS qui sont envoyés à l'usine (Accenture) pour être transcrits
en Python sur GCP.

Organisation
  Lots\\Lot_02, Lots\\Lot_03, ...   un dossier par lot, puis un dossier par domaine métier
  Lot 1                           reste dans M:\\DSIN\\Lot 1 (encore utilisé, on n'y touche pas)

Les programmes transcrits par l'équipe interne (parcours Onshore) sont rangés ailleurs :
  {o}\\Lots

Pour commencer : ouvrir Lots\\Lot_0X\\00_COMMENCER_ICI.txt du lot sur lequel vous travaillez.
"""

RACINE_O = """\
MIGRATION SAS -> PYTHON : TRAVAUX ONSHORE

Ici, on range les programmes SAS transcrits en Python par l'équipe interne (parcours Onshore).
Un dossier par lot, puis un dossier par domaine métier.

Les programmes envoyés à l'usine (parcours usine) et le fichier de suivi de chaque lot sont sur :
  {m}\\Lots

Pour commencer : ouvrir Lot_0X\\00_COMMENCER_ICI.txt du lot sur lequel vous travaillez.
"""

COMMENCER_M = """\
{lot} : COMMENCER ICI
==================================================================================

1. CE QU'EST UN LOT
Un lot, c'est un ensemble de programmes SAS qu'on migre ensemble. Ils sont rangés par domaine
métier : un dossier par domaine, et dans chaque domaine les mêmes dossiers numérotés.

2. LES DEUX PARCOURS
Chaque programme suit l'un des deux parcours :
  - USINE   : l'usine (Accenture) transcrit le programme. Nous préparons et envoyons un package
              (code désensibilisé + tables nécessaires). Tout se passe ici, sur M:.
  - ONSHORE : l'équipe interne transcrit le programme. Tout se passe sur
              {o}\\Lots\\{lot_dir}

Le parcours de chaque programme est écrit dans le fichier de suivi (00_Pilotage). S'il est marqué
« A déterminer », demander au pilote du lot avant de commencer.
Attention : le nom ou l'emplacement d'un dossier ne prouve rien. Un programme est validé, envoyé
ou reçu seulement quand c'est noté dans le fichier de suivi.

3. LE CHEMIN D'UN PROGRAMME (PARCOURS USINE)
Les dossiers de chaque domaine sont numérotés dans l'ordre où on s'en sert :

  01_Codes              on dépose la copie d'origine du programme SAS. On n'y touche plus.
  02_Carto_DCP          on repère les fichiers lus et écrits, et les données personnelles (DCP).
  03_Desensibilisation  on produit la version du code sans information sensible.
  04_Recette            on fait tourner cette version en recette et on vérifie le résultat.
  05_Livraisons         on prépare le package, on l'envoie en ZIP, on range la réponse de l'usine.

Chaque dossier contient un _LISEZ-MOI.txt qui dit quoi y mettre et ce qui vient après.

{liste_domaines}

4. OÙ TROUVER QUOI
  00_Pilotage      fichier de suivi du lot, comptes rendus, échanges avec l'usine
  00_Transverse    ce qui sert à plusieurs domaines (macros, lanceurs, référentiels du lot)
  <Domaine>        le travail programme par programme
  99_Archives      anciennes versions et fichiers à vérifier

5. LES RÈGLES À RETENIR
  - Un sous-dossier par programme, nommé exactement comme le programme SAS (sans le .sas).
    Le même nom dans tous les dossiers : on retrouve tout un programme en cherchant son nom.
  - La copie d'origine (01_Codes) ne se modifie jamais. On travaille sur une copie.
  - Ce qui sert à plusieurs domaines va une seule fois dans 00_Transverse, pas dans chaque domaine.
  - Les rapports de désensibilisation contiennent des valeurs réelles : ils ne partent jamais
    dans un package.
  - Les tables WORK prennent beaucoup de place : une fois le ZIP envoyé et la réception confirmée
    par l'usine, on supprime le dossier de travail du package (le ZIP reste).
  - Avant de supprimer ou de déplacer quelque chose qu'on n'a pas créé soi-même, on demande.
  - Dans le doute sur un fichier : 99_Archives\\A_verifier, avec une ligne d'explication.

6. QUI CONTACTER (à compléter)
  Pilote du lot              :
  Référent DCP / DPO         :
  Contact usine (Accenture)  :
  Référent outils            :

7. OUTILS ET MODE OPÉRATOIRE
Les outils de l'équipe (cartographie, désensibilisation, transcription) et leur mode opératoire :
  {outils}

8. PLAN DU LOT
{plan}
"""

COMMENCER_O = """\
{lot} : COMMENCER ICI (PARCOURS ONSHORE)
==================================================================================

Ici, on range les programmes du {lot} transcrits en Python par l'équipe interne.
Les programmes envoyés à l'usine, le fichier de suivi du lot et les éléments communs sont sur :
  {m}\\Lots\\{lot_dir}
Commencer par lire le 00_COMMENCER_ICI.txt de ce dossier-là : il explique les deux parcours.

LE CHEMIN D'UN PROGRAMME (PARCOURS ONSHORE)
Dans chaque domaine :

  01_Codes                 le programme SAS d'origine et sa version Python
  02_Donnees_dependances   ce dont le programme a besoin pour tourner : tables, fichiers, macros
  03_Tests                 les exécutions SAS et Python qu'on compare, avec leurs logs
  04_Resultats             les résultats validés et les rapports de comparaison
  05_Documentation         la fiche du programme, les règles de gestion, les décisions prises

LES RÈGLES À RETENIR
  - Un sous-dossier par programme, nommé exactement comme le programme SAS (sans le .sas).
  - Le programme SAS d'origine ne se modifie jamais.
  - L'avancement se note dans le fichier de suivi du lot (sur M:, dans 00_Pilotage).

PLAN DU LOT
{plan}
"""

PILOTAGE = """\
00_PILOTAGE ({lot})

À quoi sert ce dossier
  Suivre l'avancement du lot et garder la trace des décisions.

Ce qu'on y met
  - Suivi_programmes_{lot_dir}.xlsx : la liste des programmes du lot, leur parcours (usine ou
    Onshore), qui s'en occupe et où ils en sont. C'est la référence : si ce n'est pas noté ici,
    ce n'est pas fait.
  - Comptes_rendus : les comptes rendus de réunion.
  - Echanges_usine : périmètre, planning, demandes et réponses échangés avec l'usine.

À faire en arrivant
  Ouvrir le fichier de suivi et repérer les programmes dont vous êtes responsable.
"""

TRANSVERSE = """\
00_TRANSVERSE ({lot})

À quoi sert ce dossier
  Ranger une seule fois ce qui sert à plusieurs domaines du lot.

Ce qu'on y met
  - Macros_lanceurs : macros SAS, %include, programmes de dates ou de paramètres utilisés par
    plusieurs programmes.
  - Referentiels : tables de correspondance, formats, listes de référence propres au lot.

Ce qu'on n'y met pas
  - Ce qui ne sert qu'à un seul domaine : le laisser dans ce domaine.
  - Les outils de l'équipe : ils sont dans {outils}
"""

ARCHIVES = """\
99_ARCHIVES ({lot})

À quoi sert ce dossier
  Garder ce qu'on ne veut pas perdre mais qui ne sert plus, ou dont on n'est pas sûr.

Ce qu'on y met
  - Versions_anterieures : une ancienne version remplacée par une nouvelle.
  - A_verifier : un fichier dont on ne sait pas où il va. Le déposer en gardant son dossier
    d'origine, et ajouter une ligne dans A_verifier\\motifs.txt : nom du fichier, pourquoi il est
    là, qui l'a déposé, date.

À faire régulièrement
  Passer en revue A_verifier avec le pilote du lot pour reclasser ou supprimer.
"""

DOMAINE_M = """\
{domaine} ({lot}) : PARCOURS USINE

Ce dossier contient le travail sur les programmes du domaine {domaine} envoyés à l'usine.
Les dossiers sont numérotés dans l'ordre où on s'en sert : 01, puis 02, jusqu'à 05.
Dans chacun : un sous-dossier par programme, nommé exactement comme le programme SAS.

  01_Codes              copie d'origine du programme
  02_Carto_DCP          fichiers utilisés et données personnelles repérées
  03_Desensibilisation  code sans information sensible
  04_Recette            exécution en recette et vérifications
  05_Livraisons         package, ZIP envoyé, retour de l'usine

Où en est chaque programme : voir le fichier de suivi dans 00_Pilotage.
"""

CODES_M = """\
01_CODES ({lot} / {domaine})

À quoi sert ce dossier
  Garder la copie d'origine de chaque programme SAS, tel qu'il a été récupéré.

Ce qu'on y met
  Un sous-dossier par programme : 01_Codes\\NOM_DU_PROGRAMME\\
  On y dépose le .sas et ce qu'il appelle directement (%include, macros propres au programme).
  Les macros utilisées par plusieurs programmes vont dans 00_Transverse\\Macros_lanceurs.

La règle
  On ne modifie jamais ces fichiers. C'est la référence pour comparer.

Étape suivante
  02_Carto_DCP
"""

CARTO = """\
02_CARTO_DCP ({lot} / {domaine})

À quoi sert ce dossier
  Savoir, pour chaque programme, quels fichiers il lit et écrit, et quelles données personnelles
  il manipule. DCP = données à caractère personnel : nom, prénom, adresse, téléphone, e-mail,
  numéro de contrat ou de client, IBAN, etc.

Ce qu'on y met
  - Cartographie : le résultat de l'outil de cartographie (fichiers plats, tables, chemins).
  - DCP : les fichiers DCP du domaine, complétés avec la méthode d'anonymisation de chaque colonne.

La règle
  Une colonne DCP non traitée bloque l'envoi du package. La validation par le DPO se note dans
  le fichier de suivi (00_Pilotage), pas en déplaçant un fichier dans un dossier.

Étape suivante
  03_Desensibilisation
"""

DESENS = """\
03_DESENSIBILISATION ({lot} / {domaine})

À quoi sert ce dossier
  Produire, pour chaque programme, une version du code sans information sensible : noms de
  personnes remplacés, chemins réseau remplacés, IBAN masqués.

Ce qu'on y met
  - Un sous-dossier par programme : le code désensibilisé produit par l'outil
    (desensibilisation_code.py).
  - Rapports_NE_PAS_ENVOYER : les rapports de l'outil. Ils contiennent les valeurs réelles qui
    ont été remplacées : ils restent ici et ne partent jamais dans un package.

À faire
  Relire le code désensibilisé : rien de personnel ne doit rester dans les commentaires ou les
  noms de fichiers.

Étape suivante
  04_Recette
"""

RECETTE = """\
04_RECETTE ({lot} / {domaine})

À quoi sert ce dossier
  Vérifier que le code désensibilisé tourne en recette et donne le résultat attendu.

Ce qu'on y met
  Un sous-dossier par programme : logs d'exécution, contrôles, captures ou notes sur les écarts.

Les règles
  - En recette, on n'écrit jamais dans les fichiers métier : les sorties vont dans ce dossier.
  - Une table absente de la recette se demande au décisionnel ; la demande et la date se notent
    dans le fichier de suivi.
  - Les tables WORK de test prennent de la place : les supprimer quand la recette est terminée.

Étape suivante
  05_Livraisons (si la recette est bonne et notée dans le suivi)
"""

LIVRAISONS = """\
05_LIVRAISONS ({lot} / {domaine})

À quoi sert ce dossier
  Préparer le package envoyé à l'usine, garder le ZIP envoyé et la réponse de l'usine.

Les trois étapes
  1_En_preparation   un sous-dossier par programme avec tout ce qui part : code désensibilisé,
                     tables WORK exportées, fichiers d'entrée anonymisés.
  2_ZIP_envoyes      le ZIP tel qu'il est parti : NOM_DU_PROGRAMME_AAAAMMJJ.zip.
                     On ne le modifie plus. Un renvoi = un nouveau ZIP avec la nouvelle date.
  3_Retours_usine    accusés de réception, questions et anomalies remontées par l'usine.

Libérer de la place
  Les tables WORK d'un package pèsent souvent plusieurs gigaoctets. Quand l'usine a confirmé la
  bonne réception du ZIP (noté dans le suivi), le responsable du package supprime son dossier
  dans 1_En_preparation. Le ZIP dans 2_ZIP_envoyes garde la trace exacte de ce qui a été envoyé.

Avant d'envoyer, vérifier
  - les DCP sont validées (suivi) ;
  - la recette est bonne (suivi) ;
  - aucun rapport de désensibilisation ni donnée réelle non anonymisée dans le ZIP.
"""

DOMAINE_O = """\
{domaine} ({lot}) : PARCOURS ONSHORE

Ce dossier contient les programmes du domaine {domaine} transcrits en Python par l'équipe interne.
Dans chaque dossier : un sous-dossier par programme, nommé exactement comme le programme SAS.

  01_Codes                 programme SAS d'origine et version Python
  02_Donnees_dependances   tables, fichiers et macros nécessaires
  03_Tests                 exécutions SAS et Python comparées
  04_Resultats             résultats validés, rapports de comparaison
  05_Documentation         fiche programme, règles de gestion, décisions

Où en est chaque programme : fichier de suivi du lot, sur {m}\\Lots\\{lot_dir}\\00_Pilotage
"""

CODES_O = """\
01_CODES ({lot} / {domaine})

Ce qu'on y met
  - SAS\\NOM_DU_PROGRAMME\\    : le programme d'origine. On ne le modifie jamais.
  - Python\\NOM_DU_PROGRAMME\\ : la transcription Python (module et lanceur du framework).

Étape suivante
  Réunir ce dont le programme a besoin : 02_Donnees_dependances
"""

DONNEES_O = """\
02_DONNEES_DEPENDANCES ({lot} / {domaine})

Ce qu'on y met
  Un sous-dossier par programme : la liste des tables et fichiers lus, les fichiers d'entrée,
  les macros et formats dont il a besoin. Ce qui sert à plusieurs domaines va dans
  {m}\\Lots\\{lot_dir}\\00_Transverse.

Étape suivante
  03_Tests
"""

TESTS_O = """\
03_TESTS ({lot} / {domaine})

Ce qu'on y met
  Un sous-dossier par programme : exécutions SAS (référence) et Python, logs, écarts constatés.

La règle
  On n'écrit jamais dans les fichiers métier pendant les tests : les sorties vont ici.

Étape suivante
  Quand SAS et Python donnent le même résultat : 04_Resultats
"""

RESULTATS_O = """\
04_RESULTATS ({lot} / {domaine})

Ce qu'on y met
  Un sous-dossier par programme : le rapport de comparaison final et les sorties validées.
  La validation se note aussi dans le fichier de suivi du lot.

Étape suivante
  05_Documentation
"""

DOC_O = """\
05_DOCUMENTATION ({lot} / {domaine})

Ce qu'on y met
  Un sous-dossier par programme : ce qu'il fait en quelques lignes, ses règles de gestion,
  les choix faits pendant la transcription et pourquoi, les points restés ouverts.
  C'est ce que lira la personne qui reprendra le programme.
"""


# =============================================================================== structure
# (chemin relatif au lot ou au domaine, notice ou None, description pour le plan)
LOT_M = [
    ("00_Pilotage", PILOTAGE, "suivi du lot, comptes rendus, échanges avec l'usine"),
    ("00_Pilotage\\Comptes_rendus", None, ""),
    ("00_Pilotage\\Echanges_usine", None, ""),
    ("00_Transverse", TRANSVERSE, "ce qui sert à plusieurs domaines"),
    ("00_Transverse\\Macros_lanceurs", None, ""),
    ("00_Transverse\\Referentiels", None, ""),
    ("99_Archives", ARCHIVES, "anciennes versions, fichiers à vérifier"),
    ("99_Archives\\Versions_anterieures", None, ""),
    ("99_Archives\\A_verifier", None, ""),
]
DOM_M = [
    ("", DOMAINE_M, ""),
    ("01_Codes", CODES_M, "copie d'origine des programmes"),
    ("02_Carto_DCP", CARTO, "fichiers utilisés, données personnelles"),
    ("02_Carto_DCP\\Cartographie", None, ""),
    ("02_Carto_DCP\\DCP", None, ""),
    ("03_Desensibilisation", DESENS, "code sans information sensible"),
    ("03_Desensibilisation\\Rapports_NE_PAS_ENVOYER", None, ""),
    ("04_Recette", RECETTE, "exécution en recette, vérifications"),
    ("05_Livraisons", LIVRAISONS, "package, ZIP envoyé, retour usine"),
    ("05_Livraisons\\1_En_preparation", None, ""),
    ("05_Livraisons\\2_ZIP_envoyes", None, ""),
    ("05_Livraisons\\3_Retours_usine", None, ""),
]
LOT_O: list = []
DOM_O = [
    ("", DOMAINE_O, ""),
    ("01_Codes", CODES_O, "programme SAS d'origine et version Python"),
    ("01_Codes\\SAS", None, ""),
    ("01_Codes\\Python", None, ""),
    ("02_Donnees_dependances", DONNEES_O, "tables, fichiers, macros nécessaires"),
    ("03_Tests", TESTS_O, "exécutions SAS et Python comparées"),
    ("04_Resultats", RESULTATS_O, "résultats validés"),
    ("05_Documentation", DOC_O, "fiche programme, règles, décisions"),
]

COLONNES_SUIVI = [
    ("Programme", 32), ("Domaine", 18), ("Parcours", 14), ("Responsable", 18),
    ("Code récupéré (01)", 14), ("Carto DCP faite (02)", 14), ("DCP validées DPO", 14),
    ("Désensibilisé (03)", 14), ("Recette OK (04)", 14), ("Package prêt", 14),
    ("ZIP envoyé le", 14), ("Réception confirmée le", 14), ("WORK supprimé", 12),
    ("Transcription Python", 14), ("Comparaison SAS = Python", 14), ("Statut", 16), ("Commentaire", 50),
]
STATUTS = ["A démarrer", "En cours", "Bloqué", "Envoyé usine", "Terminé", "Abandonné"]
PARCOURS = ["Usine", "Onshore", "A déterminer"]
MODE_EMPLOI_SUIVI = [
    "Une ligne par programme du lot. C'est la référence : si ce n'est pas noté ici, ce n'est pas fait.",
    "Programme : le nom exact du programme SAS, sans .sas (le même nom que les sous-dossiers).",
    "Parcours : Usine (envoyé à Accenture, travail sur M:) ou Onshore (transcrit en interne, travail sur O:).",
    "  Si le parcours n'est pas décidé : A déterminer, et voir avec le pilote du lot.",
    "Colonnes d'étape : mettre la date du jour quand l'étape est faite. Vide = pas fait.",
    "  Parcours usine : colonnes 01 à « WORK supprimé ».",
    "  Parcours Onshore : 01, Transcription Python, Comparaison SAS = Python.",
    "Bloqué : écrire dans Commentaire ce qui bloque et à qui la demande a été faite, avec la date.",
    "WORK supprimé : dossier de 05_Livraisons\\1_En_preparation supprimé après confirmation de l'usine.",
]


# =============================================================================== moteur
def nom_valide(nom: str) -> str:
    nom = nom.strip()
    if not nom or re.search(r'[<>:"/\\|?*]', nom) or nom.endswith("."):
        sys.exit(f"Nom de domaine invalide pour Windows : « {nom} »")
    return nom


def plan_texte(lot_dir: str, dom_struct: list, lot_struct: list) -> str:
    lignes = [f"  {lot_dir}\\", "    00_COMMENCER_ICI.txt"]
    if lot_struct:
        lignes.append(f"    Suivi : 00_Pilotage\\Suivi_programmes_{lot_dir}.xlsx")
    for rel, _, desc in lot_struct:
        if "\\" not in rel:
            lignes.append(f"    {rel + chr(92):<28}{desc}")
    lignes.append("    <Domaine>\\                  un dossier par domaine métier, tous sur ce modèle :")
    for rel, _, desc in dom_struct:
        if rel and "\\" not in rel:
            lignes.append(f"      {rel + chr(92):<26}{desc}")
    return "\n".join(lignes)


class Createur:
    def __init__(self, executer: bool):
        self.executer = executer
        self.dossiers_crees = self.dossiers_existants = 0
        self.fichiers_crees = self.fichiers_identiques = 0
        self.conserves: list[str] = []
        self.journal: list[str] = []

    def dossier(self, p: Path):
        if p.is_dir():
            self.dossiers_existants += 1
            return
        if p.exists():
            sys.exit(f"[ARRÊT] {p} existe mais n'est pas un dossier.")
        self.dossiers_crees += 1
        self.journal.append(f"  + {p}")
        if self.executer:
            p.mkdir(parents=True, exist_ok=True)

    def fichier(self, p: Path, texte: str):
        contenu = (texte.rstrip() + "\n\n" + SIGNATURE + "\n").replace("\r\n", "\n").replace("\n", "\r\n")
        if p.exists():
            try:
                identique = p.read_bytes() == contenu.encode("utf-8-sig")
            except OSError:
                identique = False
            if identique:
                self.fichiers_identiques += 1
            elif str(p) not in self.conserves:
                self.conserves.append(str(p))
            return
        self.fichiers_crees += 1
        self.journal.append(f"  + {p}")
        if self.executer:
            with open(p, "w", encoding="utf-8-sig", newline="") as f:
                f.write(contenu)

    def suivi(self, p: Path, lot: str, domaines: list[str]):
        if p.exists() or p.with_suffix(".csv").exists():   # suivi déjà rempli par l'équipe : on n'y touche pas
            self.fichiers_identiques += 1
            return
        self.fichiers_crees += 1
        self.journal.append(f"  + {p}")
        if not self.executer:
            return
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Alignment, Font, PatternFill
            from openpyxl.worksheet.datavalidation import DataValidation
        except ImportError:
            csv = p.with_suffix(".csv")
            csv.write_text(";".join(c for c, _ in COLONNES_SUIVI) + "\r\n", encoding="utf-8-sig")
            self.journal.append(f"    (openpyxl absent : suivi créé en CSV {csv.name})")
            return
        wb = Workbook()
        ws = wb.active
        ws.title = "Programmes"
        for i, (col, larg) in enumerate(COLONNES_SUIVI, 1):
            c = ws.cell(row=1, column=i, value=col)
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="1F4E78")
            c.alignment = Alignment(wrap_text=True, vertical="center")
            ws.column_dimensions[c.column_letter].width = larg
        ws.row_dimensions[1].height = 45
        ws.freeze_panes = "B2"
        ws.auto_filter.ref = f"A1:{ws.cell(row=1, column=len(COLONNES_SUIVI)).column_letter}1"
        noms = [c for c, _ in COLONNES_SUIVI]

        def liste(col, valeurs):
            lettre = ws.cell(row=1, column=noms.index(col) + 1).column_letter
            dv = DataValidation(type="list", formula1='"' + ",".join(valeurs) + '"', allow_blank=True)
            ws.add_data_validation(dv)
            dv.add(f"{lettre}2:{lettre}1000")

        liste("Parcours", PARCOURS)
        liste("Statut", STATUTS)
        if domaines:
            liste("Domaine", domaines)
        m = wb.create_sheet("Mode d'emploi")
        m.column_dimensions["A"].width = 110
        m["A1"] = f"Suivi des programmes : {lot}"
        m["A1"].font = Font(bold=True, size=14)
        for i, l in enumerate(MODE_EMPLOI_SUIVI, 3):
            m.cell(row=i, column=1, value=l)
        wb.save(p)


def construire(c: Createur, racine: Path, lot_n: int, domaines: list[str], cote: str, m: str, o: str):
    lot_dir = f"Lot_{lot_n:02d}"
    v = {"lot": f"Lot {lot_n:02d}", "lot_dir": lot_dir, "m": m, "o": o, "outils": OUTILS}
    prevus = DOMAINES_PAR_LOT.get(lot_n, {})
    if prevus:
        v["liste_domaines"] = (f"Domaines du {v['lot']} ({sum(prevus.values())} programmes prévus) :\n"
                               + "\n".join(f"  - {d:<32}{n:>4} programme(s)" for d, n in prevus.items()))
    else:
        v["liste_domaines"] = "Domaines du lot : voir les dossiers de domaine."
    domaines = domaines or list(prevus)
    lot_struct, dom_struct = (LOT_M, DOM_M) if cote == "M" else (LOT_O, DOM_O)
    lots = racine / "Lots"
    c.dossier(lots)
    if cote == "O":
        c.fichier(lots / LISEZ_MOI, RACINE_O.format(**v))
    base = lots / lot_dir
    c.dossier(base)
    plan = plan_texte(lot_dir, dom_struct, lot_struct)
    c.fichier(base / "00_COMMENCER_ICI.txt", (COMMENCER_M if cote == "M" else COMMENCER_O).format(plan=plan, **v))
    for rel, notice, _ in lot_struct:
        c.dossier(base / rel)
        if notice:
            c.fichier(base / rel / LISEZ_MOI, notice.format(**v))
    if cote == "M":
        c.suivi(base / "00_Pilotage" / f"Suivi_programmes_{lot_dir}.xlsx", v["lot"], domaines)
    for d in domaines:
        for rel, notice, _ in dom_struct:
            p = base / d / rel if rel else base / d
            c.dossier(p)
            if notice:
                c.fichier(p / LISEZ_MOI, notice.format(domaine=d, **v))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lots", type=int, nargs="+", required=True, help="numéros de lot, ex. 2 3")
    ap.add_argument("--domaines", nargs="*", default=[], help="domaines métier du ou des lots")
    ap.add_argument("--dest-m", default=DEST_M_DEFAUT, help="espace usine (défaut : %(default)s)")
    ap.add_argument("--dest-o", default=DEST_O_DEFAUT, help="espace Onshore (défaut : %(default)s)")
    ap.add_argument("--sans-onshore", action="store_true", help="ne rien créer sur O:")
    ap.add_argument("--executer", action="store_true", help="créer réellement (sinon simulation)")
    a = ap.parse_args(argv)

    if not a.domaines and any(n not in DOMAINES_PAR_LOT for n in a.lots if n != 1):
        print("[i] Domaines inconnus pour le(s) lot(s) "
              + ", ".join(str(n) for n in a.lots if n not in DOMAINES_PAR_LOT) + " : préciser --domaines.")
    if 1 in a.lots:
        print("[ARRÊT] Le Lot 1 n'est pas concerné : il reste dans M:\\DSIN\\Lot 1, encore utilisé.")
        return 2
    domaines = list(dict.fromkeys(nom_valide(d) for d in a.domaines))
    cibles = [("M", Path(a.dest_m))] + ([] if a.sans_onshore else [("O", Path(a.dest_o))])
    for _, r in cibles:
        parent = r if r.exists() else r.parent
        if not parent.exists():
            print(f"[ARRÊT] {parent} est introuvable (lecteur non connecté ?). Rien n'a été créé.")
            return 2

    c = Createur(a.executer)
    for cote, r in cibles:
        c.dossier(r)
        if cote == "M":
            c.fichier(r / LISEZ_MOI, RACINE_M.format(m=a.dest_m, o=a.dest_o))
        for n in a.lots:
            construire(c, r, n, domaines, cote, a.dest_m, a.dest_o)

    print(("CRÉATION" if a.executer else "SIMULATION (rien n'est écrit, ajouter --executer)")
          + f" : lots {', '.join(f'{n:02d}' for n in a.lots)}"
          + (f", domaines {', '.join(domaines)}" if domaines else ", domaines prévus pour chaque lot"))
    print("\n".join(c.journal) or "  (rien à créer : tout existe déjà)")
    print(f"\nDossiers : {c.dossiers_crees} à créer, {c.dossiers_existants} déjà présents"
          f"\nNotices et suivi : {c.fichiers_crees} à créer, {c.fichiers_identiques} déjà présents")
    if c.conserves:
        print(f"Fichiers déjà présents et différents, CONSERVÉS sans modification ({len(c.conserves)}) :")
        print("\n".join("  = " + x for x in c.conserves))
    return 0


if __name__ == "__main__":
    sys.exit(main())
