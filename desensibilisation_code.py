# -*- coding: utf-8 -*-
"""
desensibilisation_code.py — Assistant de désensibilisation du code et des logs SAS
====================================================================================
Implémente les règles de la "Note de désensibilisation des données dans
le code SAS" (Direction Transformation, projet Modernisation Analytics).

PRINCIPE : ce script automatise ce qui est mécanique et sans ambiguïté
(mots de passe, emails, chaînes de connexion), et SIGNALE pour relecture
humaine ce qui demande un jugement (noms dans les commentaires, valeurs
clients dans les filtres, sorties de données dans les logs). C'est
volontaire : la note impose une "relecture intégrale" finale (étape 4),
et une détection automatique de données réelles par regex produirait de
faux négatifs dangereux (croire que c'est propre alors que ça ne l'est pas).

⚠️ LOGS (.log) : cas particulier et PLUS À RISQUE que le code source. Un log
peut afficher de VRAIES VALEURS DE DONNÉES (proc print, proc freq, put, et
les valeurs de WHERE que SAS réinjecte dans le log). Aucun regex ne peut
distinguer fiablement "vraie donnée client" de "texte quelconque" dans un
dump de sortie. La méthode la plus sûre reste souvent de RE-GÉNÉRER le log
depuis un run sur données déjà anonymisées, plutôt que nettoyer a posteriori
un log qui contient de vraies données.

Usage :
    python desensibilisation_code.py mon_script.sas
    python desensibilisation_code.py mon_run.log
    python desensibilisation_code.py --dossier chemin/vers/scripts/

    Annuaire entreprise (NOM/PRENOM/INITIALE/LOGIN) utilisé automatiquement
    si CHEMIN_ANNUAIRE_DEFAUT ci-dessous est renseigné (à configurer UNE
    SEULE FOIS) — sinon, passer --annuaire chemin.csv à chaque lancement
    pour le surcharger ponctuellement.

Prérequis : aucune dépendance externe (bibliothèque standard uniquement)
"""
from __future__ import annotations
import re
import sys
from pathlib import Path
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# >>> À CONFIGURER UNE SEULE FOIS <<<
# Chemin vers l'annuaire entreprise (colonnes NOM, PRENOM, INITIALE, LOGIN).
# Si renseigné, il est chargé et utilisé AUTOMATIQUEMENT à chaque lancement,
# sans avoir besoin de retaper --annuaire à chaque fois.
# Laisser à None (ou un chemin invalide) pour désactiver -> repli automatique
# sur la détection heuristique seule (spaCy/dictionnaire).
# ---------------------------------------------------------------------------
CHEMIN_ANNUAIRE_DEFAUT: str | None = "EFFECTIFS.csv"

# ---------------------------------------------------------------------------
# Chemin vers la cartographie officielle des COLONNES classées C4 (colonnes
# attendues : Base, Table, Nom de la donnée, Catégorie CNIL, Classification
# CRC). Détection EXACTE des noms de colonnes C4 utilisés comme identifiants
# dans le code SAS (ex. 'where DTINVAL ne .') — bien plus fiable que la
# détection C4 par mots-clés en texte libre, qui reste active en complément
# pour les cas non couverts par cette cartographie.
# ---------------------------------------------------------------------------
CHEMIN_CARTO_C4_DEFAUT: str | None = "cartographie_c4_test.csv"  # <-- ajuste ce chemin


# ---------------------------------------------------------------------------
# Structure de rapport
# ---------------------------------------------------------------------------
@dataclass
class RapportFichier:
    chemin: str
    remplacements_auto: list[str] = field(default_factory=list)
    a_revoir: list[str] = field(default_factory=list)
    # identifiants purs (SIN, n° contrat...) : décision du 29/07/2026 (mail Christophe)
    # -> les identifiants ne sont PLUS à anonymiser (aligné sur OPTIM).
    # Gardés en note informative séparée, pas mélangés aux vraies alertes DCP.
    identifiants: list[str] = field(default_factory=list)
    # Catégories C4 (CNIL) : santé, origine ethnique, religion, syndicat, orientation
    # sexuelle, casier judiciaire, données génétiques/biométriques, NIR — les VRAIS
    # bloquants légaux : non autorisées sur GCP (prod et non-prod) sans désensibilisation.
    alertes_c4: list[str] = field(default_factory=list)
    # table de correspondance nom réel -> trigramme, pour audit/traçabilité
    mapping_noms: dict[str, str] = field(default_factory=dict)
    # table de correspondance chemin réel -> alias logique, pour audit/traçabilité
    mapping_chemins: dict[str, str] = field(default_factory=dict)
    # Faux positifs probables : un mot qui matche un NOM de l'annuaire mais qui
    # est en réalité un mot courant (ex. 'RIEN' -> trigramme 'IEN'). Remplacé
    # quand même (par prudence) mais SIGNALÉ pour vérification manuelle.
    faux_positifs: list[str] = field(default_factory=list)


def _trigramme(prenom: str, nom: str) -> str:
    """'Jean', 'DUPONT' -> 'JDU' (1ère lettre prénom + 2 premières du nom),
    conforme aux exemples de la note officielle (JDU, SBE...)."""
    p = prenom.strip()[:1].upper()
    n = nom.strip()[:2].upper()
    return p + n


# ---------------------------------------------------------------------------
# ANNUAIRE ENTREPRISE (source de vérité officielle) — remplace la plupart des
# heuristiques (spaCy/dictionnaire) dès qu'une personne y figure : le
# trigramme utilisé est l'INITIALE réelle attribuée par les RH, pas un
# trigramme recalculé, et le LOGIN officiel permet une reconnaissance exacte
# du format 'prenom.nom'. spaCy/dictionnaire restent un filet de sécurité
# pour les personnes absentes de l'annuaire (externes, anciens salariés,
# clients, prestataires).
# Colonnes attendues (autres colonnes RH ignorées) : NOM, PRENOM, INITIALE, LOGIN
# ---------------------------------------------------------------------------
class Annuaire:
    def __init__(self):
        self.par_login: dict[str, str] = {}                    # login (min) -> INITIALE
        self.par_nom_prenom: dict[tuple[str, str], str] = {}    # (NOM, PRENOM) -> INITIALE
        self.par_nom_seul: dict[str, str] = {}                  # NOM -> INITIALE (si NOM unique)
        self.prenoms_par_nom: dict[str, set[str]] = {}          # NOM -> {PRENOM, ...}
        # (NOM, 1re lettre du prénom) -> INITIALE, uniquement si UN SEUL
        # employé de ce nom a un prénom commençant par cette lettre.
        # Permet de résoudre 'M SEOSSE' même si plusieurs SEOSSE existent.
        self.par_nom_initiale_prenom: dict[tuple[str, str], str] = {}
        self.noms: set[str] = set()

    def __len__(self):
        return len(self.par_nom_prenom)


def _sans_accents(texte: str) -> str:
    """'Léana' -> 'LEANA', 'Mickaël' -> 'MICKAEL' — pour comparer des noms
    écrits avec ou sans accents (fréquent entre annuaire RH et code SAS)."""
    import unicodedata
    return unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode().upper()


# Colonnes d'identité lues dans l'annuaire. TOUTES les autres colonnes
# (catégorie, service, statut, matricule...) sont ignorées, quelle que soit
# leur position. Les en-têtes sont comparés sans accents / casse / espaces.
COLONNES_ANNUAIRE = {
    "NOM":      ("NOM",),
    "PRENOM":   ("PRENOM",),
    "INITIALE": ("INITIALE", "INITIALES", "TRIGRAMME"),
    "LOGIN":    ("LOGIN",),
}
# Une vraie valeur de nom/prénom : lettres (accents compris), espaces, tirets,
# apostrophes. Pas de chiffres, pas de ponctuation de catégorie ("CAT 2", "B/3").
RE_VALEUR_NOM = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ' \-]{0,59}$")
RE_VALEUR_INITIALE = re.compile(r"^[A-Z]{2,4}$")


def _entete_normalise(c: str) -> str:
    return _sans_accents((c or "").strip().strip('"').strip()).replace(" ", "_")


def charger_annuaire(chemin_csv: str, sep: str = ";", encoding: str | None = None) -> Annuaire:
    """Charge l'annuaire entreprise (CSV) et construit les index de recherche.

    Ne lit QUE les colonnes NOM, PRENOM, INITIALE (ou TRIGRAMME), LOGIN, repérées
    par leur POSITION dans l'en-tête. Les autres colonnes (catégorisation de
    l'employé, service...) ne sont jamais lues.
    Protections :
      - en-tête en double (ex. 2 colonnes 'NOM') : la 1re est prise, alerte affichée ;
      - ligne au nombre de colonnes incorrect (un ';' non protégé dans une valeur
        décale toutes les colonnes suivantes) : ligne REJETÉE, jamais indexée ;
      - valeur qui ne ressemble pas à un nom (chiffres, '/', etc.) ou trigramme
        invalide : ligne rejetée.
    Un diagnostic (colonnes utilisées, lignes rejetées + exemples) est affiché."""
    import csv
    if encoding is None:
        encoding = _detecter_encodage(chemin_csv)
    ann = Annuaire()
    with open(chemin_csv, encoding=encoding, newline="") as f:
        premiere_ligne = f.readline()
        f.seek(0)
        vrai_sep = sep if sep in premiere_ligne else (
            "\t" if "\t" in premiere_ligne else "," if "," in premiere_ligne else ";")
        lecteur = csv.reader(f, delimiter=vrai_sep)
        entetes = next(lecteur, [])
        norm = [_entete_normalise(c) for c in entetes]

        # Position de chaque colonne d'identité (1re occurrence seulement)
        idx: dict[str, int] = {}
        for cle, variantes in COLONNES_ANNUAIRE.items():
            positions = [i for i, h in enumerate(norm) if h in variantes]
            if positions:
                idx[cle] = positions[0]
                if len(positions) > 1:
                    print(f"[annuaire] ATTENTION : {len(positions)} colonnes '{cle}' dans "
                          f"l'en-tête (positions {[p + 1 for p in positions]}) -> seule la "
                          f"colonne {positions[0] + 1} est utilisée.")
        manquantes = [c for c in ("NOM", "INITIALE") if c not in idx]
        if manquantes:
            print(f"[annuaire] ERREUR : colonne(s) {manquantes} introuvable(s). "
                  f"En-têtes lus : {entetes}")
            return ann
        print(f"[annuaire] Colonnes utilisées : "
              + ", ".join(f"{c}=col.{i + 1} ('{entetes[i].strip()}')" for c, i in idx.items())
              + f" | {len(entetes) - len(idx)} autre(s) colonne(s) ignorée(s)")

        nb_lues, rejets = 0, {}
        def _rejeter(motif: str, num: int, apercu: str):
            ex = rejets.setdefault(motif, [])
            ex.append(f"ligne {num}: {apercu[:80]}")

        for num, valeurs in enumerate(lecteur, start=2):
            if not any(v.strip() for v in valeurs):
                continue
            nb_lues += 1
            if len(valeurs) != len(entetes):
                _rejeter("colonnes décalées (séparateur dans une valeur ?)", num,
                         vrai_sep.join(valeurs))
                continue
            get = lambda c: valeurs[idx[c]].strip() if c in idx else ""
            nom = get("NOM").upper()
            prenom = get("PRENOM")
            initiale = get("INITIALE").upper()
            login = get("LOGIN").lower()
            if not nom or not initiale:
                continue
            if not RE_VALEUR_NOM.match(nom) or (prenom and not RE_VALEUR_NOM.match(prenom)):
                _rejeter("valeur qui ne ressemble pas à un nom/prénom", num,
                         f"NOM='{nom}' PRENOM='{prenom}'")
                continue
            if not RE_VALEUR_INITIALE.match(initiale):
                _rejeter("trigramme invalide (attendu 2 à 4 lettres)", num,
                         f"NOM='{nom}' INITIALE='{initiale}'")
                continue
            ann.noms.add(nom)
            # --- Normalisation des accents : 'Léana' doit matcher 'Leana',
            #     'Mickaël' doit matcher 'Mickael' -> on indexe les DEUX formes
            nom_norm = _sans_accents(nom)
            prenom_norm = _sans_accents(prenom.upper())
            ann.prenoms_par_nom.setdefault(nom, set()).add(prenom.upper())
            if prenom:
                ann.par_nom_prenom[(nom, prenom.upper())] = initiale
                ann.par_nom_prenom[(nom_norm, prenom_norm)] = initiale
                # --- Noms COMPOSÉS : 'MADJOU FOTSING' -> indexe aussi chaque
                #     composant ('MADJOU', 'FOTSING') pour matcher un code qui
                #     n'écrit qu'une partie du nom de famille.
                composants = [c for c in nom.replace("-", " ").split() if len(c) >= 3]
                if len(composants) > 1:
                    for comp in composants:
                        ann.par_nom_prenom.setdefault((comp, prenom.upper()), initiale)
                        ann.par_nom_prenom.setdefault((_sans_accents(comp), prenom_norm), initiale)
                        ann.noms.add(comp)
                        # indispensable pour que le prénom soit absorbé aussi
                        # sur un composant (ex. 'Léana MADJOU' -> LMA en entier)
                        ann.prenoms_par_nom.setdefault(comp, set()).add(prenom.upper())
            if login:
                ann.par_login[login] = initiale
                # robuste au format : indexe AUSSI la partie avant '@' si le
                # LOGIN est stocké en email complet (prenom.nom@domaine.fr)
                if "@" in login:
                    ann.par_login[login.split("@")[0]] = initiale

        nb_rejets = sum(len(v) for v in rejets.values())
        print(f"[annuaire] {nb_lues} ligne(s) lue(s), {nb_rejets} rejetée(s).")
        for motif, exemples in rejets.items():
            print(f"[annuaire]   - {len(exemples)} x {motif}, ex. : {exemples[:3]}")
    initiales_par_nom: dict[str, set[str]] = {}
    for (nom, _prenom), init in ann.par_nom_prenom.items():
        initiales_par_nom.setdefault(nom, set()).add(init)
    for nom, inits in initiales_par_nom.items():
        if len(inits) == 1:
            ann.par_nom_seul[nom] = next(iter(inits))

    # Index de désambiguïsation : (NOM, 1re lettre du prénom) -> INITIALE.
    # Ne conserve que les combinaisons qui désignent UNE SEULE personne :
    # s'il y a 3 SEOSSE mais un seul dont le prénom commence par M, alors
    # 'M SEOSSE' est résolvable sans ambiguïté. Sinon, on ne devine pas.
    candidats: dict[tuple[str, str], set[str]] = {}
    for (nom, prenom), init in ann.par_nom_prenom.items():
        if not prenom:
            continue
        cle = (nom, _sans_accents(prenom)[:1])
        candidats.setdefault(cle, set()).add(init)
    for cle, inits in candidats.items():
        if len(inits) == 1:
            ann.par_nom_initiale_prenom[cle] = next(iter(inits))
    return ann


_ANNUAIRE_CACHE: Annuaire | None = None


def get_annuaire(chemin_csv: str | None) -> Annuaire | None:
    global _ANNUAIRE_CACHE
    if chemin_csv is None:
        return None
    if _ANNUAIRE_CACHE is None:
        try:
            _ANNUAIRE_CACHE = charger_annuaire(chemin_csv)
            print(f"[info] Annuaire chargé : {len(_ANNUAIRE_CACHE)} personne(s) "
                  f"({len(_ANNUAIRE_CACHE.par_login)} login(s), "
                  f"{len(_ANNUAIRE_CACHE.par_nom_seul)} NOM(s) non-ambigu(s)).")
        except Exception as e:
            print(f"[avertissement] Annuaire introuvable/illisible ({e}) : "
                  f"repli sur la détection heuristique seule (spaCy/dictionnaire).")
            _ANNUAIRE_CACHE = Annuaire()
    return _ANNUAIRE_CACHE


# ---------------------------------------------------------------------------
# CARTOGRAPHIE OFFICIELLE DES COLONNES C4 — colonnes attendues :
# Base, Table, Nom de la donnée, Catégorie CNIL, Classification CRC
# ---------------------------------------------------------------------------
def _detecter_encodage(chemin: str) -> str:
    """Essaie UTF-8 (avec ou sans BOM) puis se replie sur Latin-1 si le
    fichier n'est pas un UTF-8 valide — évite de supposer un encodage fixe,
    car les fichiers CSV RH/métier proviennent de sources variées (Excel,
    exports SAS...) avec des encodages différents."""
    raw = Path(chemin).read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "latin-1"


def charger_cartographie_c4(chemin_csv: str, sep: str = ";",
                            encoding: str | None = None) -> dict[str, dict]:
    """Charge la cartographie officielle des colonnes C4. Renvoie
    {NOM_COLONNE (maj) -> {'categorie': ..., 'classification': ...,
    'tables': [(base, table), ...]}} — une même colonne peut apparaître
    dans plusieurs tables, toutes ses occurrences sont conservées pour info."""
    import csv
    if encoding is None:
        encoding = _detecter_encodage(chemin_csv)
    carto: dict[str, dict] = {}
    with open(chemin_csv, encoding=encoding, newline="") as f:
        premiere_ligne = f.readline()
        f.seek(0)
        vrai_sep = sep if sep in premiere_ligne else (
            "\t" if "\t" in premiere_ligne else "," if "," in premiere_ligne else ";")
        lecteur = csv.DictReader(f, delimiter=vrai_sep)
        lecteur.fieldnames = [c.strip() for c in (lecteur.fieldnames or [])]
        for ligne in lecteur:
            col = (ligne.get("Nom de la donnée") or "").strip().upper()
            base = (ligne.get("Base") or "").strip()
            table = (ligne.get("Table") or "").strip()
            categorie = (ligne.get("Catégorie CNIL") or "").strip()
            classification = (ligne.get("Classification CRC") or "").strip()
            if not col:
                continue
            if col not in carto:
                carto[col] = {"categorie": categorie, "classification": classification, "tables": []}
            carto[col]["tables"].append((base, table))
    return carto


_CARTO_C4_CACHE: dict[str, dict] | None = None


def get_cartographie_c4(chemin_csv: str | None) -> dict[str, dict]:
    global _CARTO_C4_CACHE
    if chemin_csv is None:
        return {}
    if _CARTO_C4_CACHE is None:
        try:
            _CARTO_C4_CACHE = charger_cartographie_c4(chemin_csv)
            print(f"[info] Cartographie C4 chargée : {len(_CARTO_C4_CACHE)} colonne(s) "
                  f"officiellement classée(s) C4.")
        except Exception as e:
            print(f"[avertissement] Cartographie C4 introuvable/illisible ({e}) : "
                  f"repli sur la détection C4 par mots-clés seule.")
            _CARTO_C4_CACHE = {}
    return _CARTO_C4_CACHE


# ---------------------------------------------------------------------------
# RÈGLE N°1 — Mots de passe et identifiants de connexion (automatique, sûr)
# ---------------------------------------------------------------------------
RE_PASSWORD = re.compile(r'(?i)\b(password|pwd|pass)\s*=\s*\S+')
RE_USER_CONN = re.compile(r'(?i)\buser\s*=\s*\S+')

# ---------------------------------------------------------------------------
# RÈGLE N°2 — Adresses email (automatique, sûr, avec cohérence par mapping)
# ---------------------------------------------------------------------------
RE_EMAIL = re.compile(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}')

_compteur_email = {"n": 0}
_mapping_email: dict[str, str] = {}


def _remplace_email(m: re.Match) -> str:
    original = m.group(0)
    if original not in _mapping_email:
        _compteur_email["n"] += 1
        _mapping_email[original] = f"user{_compteur_email['n']}@example.com"
    return _mapping_email[original]


# ---------------------------------------------------------------------------
# Chemins réseau/serveurs (DFE) — REMPLACÉS automatiquement par un alias
# logique cohérent (CHEMIN_SOURCE_01, CHEMIN_SOURCE_02...), pas juste
# signalés. Principe : comme tout ceci migre vers Python/BigQuery, la vraie
# valeur du chemin n'a aucune importance pour l'usine offshore — seule la
# STRUCTURE compte (même alias partout où le même chemin réel apparaît,
# alias différent pour un chemin différent). C'est le même principe
# d'indirection que la couche saskit/config.yml de la migration Python :
# nom logique stable, emplacement réel découplé. Le code reste syntaxiquement
# valide (le remplacement se fait entre les guillemets existants).
# ---------------------------------------------------------------------------
# ⚠️ Regex corrigée (bug critique) : l'ancienne version s'arrêtait au premier
# espace (or les chemins réseau contiennent des espaces : "03-Pilotage UGS-EVT
# CLIM") ET avalait le '*/' de fin de commentaire, ce qui CASSAIT la syntaxe
# SAS (tout le reste du fichier devenait un commentaire).
#   - segments de chemin : autorise les espaces à l'intérieur d'un segment
#   - s'arrête net devant un '*' (protège '*/') ou un guillemet
RE_CHEMIN_RESEAU = re.compile(
    r'\\\\[A-Za-z0-9_\-\.]+(?:\\[^\\\n"\';*]+)+')
RE_HOST_ORACLE = re.compile(r'(?i)\bpath\s*=\s*\S+')

_compteur_chemin = {"n": 0}
_mapping_chemin: dict[str, str] = {}


def _alias_chemin(chemin_reel: str) -> str:
    if chemin_reel not in _mapping_chemin:
        _compteur_chemin["n"] += 1
        _mapping_chemin[chemin_reel] = f"CHEMIN_SOURCE_{_compteur_chemin['n']:02d}"
    return _mapping_chemin[chemin_reel]

# ---------------------------------------------------------------------------
# SIGNALÉ — Noms/prénoms probables dans les commentaires (heuristique faible,
# volontairement non remplacé auto : à relire par un humain)
# ---------------------------------------------------------------------------
# ⚠️ Regex corrigée (bug de désynchronisation) : en SAS, un commentaire de
# type '* ...' se termine au premier ';', PAS à la fin de ligne. L'ancienne
# version ('^\s*\*.*$') avalait toute la ligne, y compris un éventuel '/*'
# situé après le ';' — le '*/' correspondant devenait alors orphelin et TOUT
# le découpage des commentaires suivants était décalé (les blocs plus bas
# dans le fichier n'étaient plus détectés du tout).
RE_LIGNE_COMMENTAIRE = re.compile(r'(/\*.*?\*/|^[ \t]*\*[^;]*;)', re.MULTILINE | re.DOTALL)

# ---------------------------------------------------------------------------
# SORTIES STATISTIQUES COLLÉES EN COMMENTAIRE (proc univariate / freq / means
# / summary...) — à SUPPRIMER : elles n'apportent aucune information métier à
# l'usine offshore, alourdissent le code, et peuvent exposer des volumétries
# ou distributions de données réelles.
#
# Détection par CONTENU (pas par position) : ces blocs sont souvent collés
# loin de la procédure qui les a produits, voire dans un fichier différent.
# On cherche les marqueurs typiques des sorties SAS ; il en faut PLUSIEURS
# pour déclencher la suppression, afin de ne pas supprimer par erreur un
# vrai commentaire métier qui mentionnerait juste "moyenne" ou "N".
# ---------------------------------------------------------------------------
MARQUEURS_SORTIE_STAT = [
    r'\bQuantile\b', r'\b100%\s*Max\b', r'\b0%\s*Min\b', r'\b75%\s*Q3\b',
    r'\b25%\s*Q1\b', r'\b50%\s*Median\b', r'\bStd\s+Deviation\b',
    r'\bSum\s+Weights\b', r'\bSum\s+Observations\b', r'\bSkewness\b',
    r'\bKurtosis\b', r'\bVariance\b', r'\bCumulative\s+Frequency\b',
    r'\bCumulative\s+Percent\b', r'\bThe\s+FREQ\s+Procedure\b',
    r'\bThe\s+UNIVARIATE\s+Procedure\b', r'\bThe\s+MEANS\s+Procedure\b',
    r'\bBasic\s+Statistical\s+Measures\b', r'\bMoments\b',
    r'\bCoeff\s+Variation\b', r'\bInterquartile\s+Range\b',
]
RE_MARQUEURS_STAT = [re.compile(p, re.IGNORECASE) for p in MARQUEURS_SORTIE_STAT]

# NOTE: du log SAS collées en commentaire (volumétries d'observations) :
# un seul marqueur suffit, le motif est sans ambiguïté.
RE_NOTE_LOG = re.compile(r'NOTE\s*:', re.IGNORECASE)

# Seuil : nombre de marqueurs statistiques distincts requis pour supprimer
SEUIL_MARQUEURS_STAT = 2

# ---------------------------------------------------------------------------
# Commentaires ANCIENS : années antérieures à ce seuil -> SIGNALÉS (jamais
# supprimés automatiquement, car certains portent une règle métier toujours
# valide). Le relecteur tranche depuis le rapport.
# ---------------------------------------------------------------------------
ANNEE_SEUIL_OBSOLESCENCE = 2023
RE_ANNEE = re.compile(r'\b(19\d{2}|20\d{2})\b')


def _est_sortie_statistique(texte_commentaire: str) -> tuple[bool, str]:
    """Renvoie (True, motif) si le commentaire est une sortie statistique
    collée (proc univariate/freq/means) ou une NOTE: de log, à supprimer."""
    if RE_NOTE_LOG.search(texte_commentaire):
        return True, "NOTE: de log SAS (volumétrie d'observations)"
    trouves = [p.pattern for p in RE_MARQUEURS_STAT if p.search(texte_commentaire)]
    if len(trouves) >= SEUIL_MARQUEURS_STAT:
        return True, f"sortie statistique ({len(trouves)} marqueurs, ex. {trouves[0]})"
    return False, ""

RE_NOM_PROBABLE = re.compile(r'\b([A-ZÀ-Ý][a-zà-ÿ]+)\s+([A-ZÀ-Ý]{2,})\b')  # "Jean DUPONT"

# ---------------------------------------------------------------------------
# Filtre anti-faux-positifs : ne signaler "Prénom NOM" que si le 1er mot est
# un VRAI prénom français reconnu — élimine "Table WORK", "Pilotage UGS",
# "Passages CATNAT" (aucun n'est un prénom) sans toucher aux vrais noms.
# Liste non exhaustive mais couvre l'immense majorité des prénoms courants
# en France ; à enrichir si des faux négatifs apparaissent dans tes rapports.
# ---------------------------------------------------------------------------
PRENOMS_FR = {
    "aaron","abdel","adam","adrien","agnes","aicha","alain","alan","albert","alex",
    "alexandra","alexandre","alexis","alice","alicia","aline","amandine","amelie",
    "amine","anais","andre","andrea","anna","anne","antoine","arnaud","arthur",
    "audrey","aurelie","aurelien","axel","baptiste","benjamin","benoit","bernard",
    "bertrand","bruno","camille","carine","carole","caroline","catherine","cecile",
    "celine","charles","charlotte","chloe","christelle","christian","christiane",
    "christina","christine","christophe","claire","claude","clement","clemence",
    "corinne","cyril","damien","daniel","david","delphine","denis","didier",
    "dominique","edouard","elise","elodie","emeline","emilie","emilien","emma",
    "emmanuel","emmanuelle","eric","estelle","etienne","eugene","eva","fabien",
    "fabienne","fabrice","fanny","fatima","florence","florent","florian","france",
    "francis","francois","francoise","frederic","frederique","gabriel","gaelle",
    "genevieve","georges","gerald","gerard","germain","ghislaine","gilbert","gilles",
    "guillaume","guy","helene","henri","herve","hugo","ines","isabelle","jacqueline",
    "jacques","jean","jeanne","jerome","joel","johan","joseph","josephine","josiane",
    "julie","julien","juliette","karim","karine","kevin","laetitia","laura","laure",
    "laurence","laurent","lea","lea","lena","leo","leon","leonard","lilian","lionel",
    "loic","louis","louise","luc","lucas","lucie","lucien","ludovic","lydie","magali",
    "manon","marc","marceau","marcel","margaux","marguerite","maria","marianne",
    "marie","marine","marion","martin","martine","mathieu","mathilde","mathis",
    "matthieu","maxime","melanie","melissa","michel","michele","michelle","mickael",
    "morgane","muriel","myriam","nadege","nadia","nadine","nathalie","nicolas",
    "nicole","nina","noemie","olivier","oceane","pascal","pascale","patricia",
    "patrick","paul","paulette","paule","philippe","pierre","priscilla","quentin",
    "rachel","raphael","raymond","regis","remi","renaud","rene","robert","roger",
    "roland","romain","romane","sabine","sabrina","salome","samuel","sandra",
    "sandrine","sarah","sebastien","serge","severine","simon","simone","sophie",
    "stephane","stephanie","suzanne","sylvain","sylvie","theo","therese","thibault",
    "thierry","thomas","tristan","valentin","valentine","valerie","vanessa",
    "veronique","victor","victoire","vincent","virginie","vivien","xavier","yann",
    "yannick","yves","yvette","yvonne","zoe",
}


def _est_prenom_connu(mot: str) -> bool:
    """Normalise (minuscules, sans accent approximatif) et cherche dans PRENOMS_FR."""
    import unicodedata
    n = unicodedata.normalize("NFKD", mot.lower()).encode("ascii", "ignore").decode()
    return n in PRENOMS_FR


# Mots courants français/SAS souvent confondus avec un NOM de l'annuaire
# (ex. 'RIEN' peut matcher un nom RH 'RIEN' et devenir 'IEN'). On ne bloque
# PAS le remplacement (prudence), mais on le SIGNALE dans le rapport comme
# faux positif probable, pour vérification manuelle.
MOTS_COURANTS_SUSPECTS = {
    "rien", "tout", "tous", "toute", "toutes", "avec", "sans", "pour", "dans",
    "date", "code", "type", "nom", "test", "table", "base", "champ", "ligne",
    "colonne", "valeur", "total", "somme", "moyenne", "point", "page", "note",
    "fin", "debut", "vide", "null", "vrai", "faux", "oui", "non", "auto",
    "client", "contrat", "sinistre", "garage", "expert", "montant", "libelle",
    "mission", "reglement", "recherche", "compte", "suivi", "annee", "mois",
}


def _est_faux_positif_nom(mot: str) -> bool:
    """True si le mot ressemble à un mot courant plutôt qu'à un vrai nom."""
    import unicodedata
    n = unicodedata.normalize("NFKD", mot.lower()).encode("ascii", "ignore").decode()
    return n in MOTS_COURANTS_SUSPECTS


# Mots à NE JAMAIS remplacer comme des noms, même s'ils matchent l'annuaire ou
# l'heuristique : mois, jours, et quelques termes calendaires. Un mois ('Janvier',
# 'Décembre') n'est jamais un nom de personne dans ce contexte.
MOTS_JAMAIS_NOM = {
    "janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet", "aout",
    "septembre", "octobre", "novembre", "decembre",
    "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche",
    "trimestre", "semestre", "semaine", "mensuel", "annuel", "hebdo",
}


def _ne_jamais_remplacer(mot: str) -> bool:
    """True si le mot ne doit JAMAIS être traité comme un nom (mois, jour...)."""
    import unicodedata
    n = unicodedata.normalize("NFKD", str(mot).lower()).encode("ascii", "ignore").decode()
    return n in MOTS_JAMAIS_NOM


def _deja_couvert_precoce(pos_abs: int, noms_a_remplacer: list[tuple[str, str, int]]) -> bool:
    """Vérifie si une position est déjà couverte par un remplacement déjà
    enregistré (évite les doublons entre les différentes passes de détection)."""
    return any(p <= pos_abs < p + len(t) for t, _, p in noms_a_remplacer)


# ---------------------------------------------------------------------------
# NUMÉROS DANS LES COMMENTAIRES (sinistre, téléphone, ...)
# Règle actuelle :
#   - on masque UNIQUEMENT dans les COMMENTAIRES (/* ... */ et * ... ;).
#     Le code exécutable et les filtres (where=, sin=...) ne sont PAS modifiés,
#     sinon le programme ne ferait plus la même chose.
#   - on masque UNIQUEMENT les chaînes d'EXACTEMENT 10 chiffres consécutifs
#     (ex. sinistre 0198988904, téléphone 0628402491). Une suite plus courte
#     (2024, 123456) ou plus longue (000085603372) n'est pas touchée.
#   - IBAN : masqué aussi dans les commentaires (demande explicite précédente).
#     Mettre MASQUER_IBAN_COMMENTAIRES = False pour le désactiver.
# ---------------------------------------------------------------------------
NB_CHIFFRES_A_MASQUER = 10
MASQUER_IBAN_COMMENTAIRES = True

# Exactement 10 chiffres : ni chiffre juste avant, ni chiffre juste après
# (donc 'sin="0198988904"' est masqué, '000085603372' (12 chiffres) ne l'est pas).
RE_NUMERO_10 = re.compile(r'(?<!\d)\d{' + str(NB_CHIFFRES_A_MASQUER) + r'}(?!\d)')

# IBAN français complet (avec ou sans espaces), masqué en entier.
RE_IBAN = re.compile(r'\bFR\d{2}(?:[ \t]?[0-9A-Z]{2,5}){3,7}\b', re.IGNORECASE)

MASQUE_NUMERO = "NUM_MASQUE"
MASQUE_IBAN = "IBAN_MASQUE"


def _masquer_donnees_chiffrees(code: str, rapport: "RapportFichier") -> str:
    """Masque, DANS LES COMMENTAIRES SEULEMENT, les chaînes d'exactement 10
    chiffres (et les IBAN si MASQUER_IBAN_COMMENTAIRES). Le reste du code n'est
    jamais modifié. Remplacements faits de la fin vers le début pour ne pas
    décaler les positions. Chaque masquage est journalisé dans le rapport."""
    remplacements: list[tuple[int, int, str, str, str]] = []  # (début, fin, original, masque, catégorie)

    def _chevauche(s: int, e: int) -> bool:
        return any(not (e <= rs or s >= re_) for rs, re_, *_ in remplacements)

    for bloc in RE_LIGNE_COMMENTAIRE.finditer(code):
        texte, base = bloc.group(0), bloc.start()
        regles = []
        if MASQUER_IBAN_COMMENTAIRES:
            regles.append((RE_IBAN, MASQUE_IBAN, "IBAN (commentaire)"))
        regles.append((RE_NUMERO_10, MASQUE_NUMERO, "NUMÉRO 10 CHIFFRES (commentaire)"))
        for regex, masque, categorie in regles:    # IBAN d'abord : il contient des chiffres
            for m in regex.finditer(texte):
                s, e = base + m.start(), base + m.end()
                if _chevauche(s, e):
                    continue
                remplacements.append((s, e, m.group(0), masque, categorie))

    for s, e, orig, masque, categorie in sorted(remplacements, key=lambda x: x[0]):
        ligne = code[:s].count("\n") + 1
        rapport.remplacements_auto.append(f"[{categorie} - ligne {ligne}] '{orig}' -> {masque}")
    for s, e, _orig, masque, _cat in sorted(remplacements, key=lambda x: -x[0]):
        code = code[:s] + masque + code[e:]
    return code


# ---------------------------------------------------------------------------
# Détection NER (spaCy) — optionnelle, plus fiable que le dictionnaire de
# prénoms car elle utilise le CONTEXTE, pas juste la forme du mot. Chargée
# une seule fois (coûteux), avec repli silencieux si spaCy/le modèle est
# indisponible (ex. blocage réseau sur poste d'entreprise) : l'outil continue
# de fonctionner avec la méthode par dictionnaire dans ce cas.
#   pip install spacy
#   python -m spacy download fr_core_news_sm
# ---------------------------------------------------------------------------
# spaCy est DÉSACTIVÉ : décision du projet -> détection de noms basée
# UNIQUEMENT sur l'annuaire entreprise (source de vérité RH). spaCy produisait
# trop de faux positifs (mots français ordinaires pris pour des noms) et
# fabriquait des trigrammes non fiables. Les personnes hors annuaire ne sont
# plus remplacées automatiquement : elles sont SIGNALÉES dans le rapport pour
# relecture manuelle (voir _entite_plausible / a_revoir).
_UTILISER_SPACY = False
_NLP_SPACY = None
_SPACY_TENTE = False


def _charger_spacy():
    """spaCy volontairement désactivé (mode annuaire seul). Retourne toujours
    None pour que tout le pipeline retombe sur l'annuaire + signalement."""
    return None


def _detecter_personnes_spacy(texte_commentaire: str) -> list[str]:
    """Renvoie la liste des entités PERSON détectées par spaCy dans un bloc
    de commentaire, FILTRÉES par un contrôle de plausibilité — spaCy seul
    (surtout fr_core_news_sm, modèle léger) produit trop de faux positifs
    sur du texte hors contexte (commentaires de code, logs anglais) : il
    confond des mots français ordinaires ('Ajout', 'Focus') ou des phrases
    anglaises de log ('were deleted') avec des noms de personnes."""
    nlp = _charger_spacy()
    if nlp is None:
        return []
    doc = nlp(texte_commentaire)
    return [ent.text for ent in doc.ents
            if ent.label_ == "PER" and _entite_plausible(ent.text)]


def _entite_plausible(nom_detecte: str) -> bool:
    """Filtre de bon sens post-spaCy : rejette ce qui ne peut structurellement
    pas être un nom de personne, même si le modèle l'a étiqueté PERSON.
      - rejette toute entité contenant un mot tout en minuscules
        (élimine 'were deleted', qui n'est même pas du français)
      - pour une entité à un seul mot : accepté seulement si c'est un
        prénom français connu (sinon 'Ajout', 'Focus' etc. passeraient)
      - pour une entité à plusieurs mots : la FORME doit être correcte
        ('Majuscule+minuscules' ou 'TOUT_MAJUSCULES' par mot) ET au moins
        UN des mots doit être un vrai prénom français connu — sinon la
        forme seule ne suffit pas (elle acceptait à tort 'COLUMN Statement'
        ou 'Donnees Bilan', qui ont la bonne casse mais ne sont pas des noms)
    """
    mots = [m for m in nom_detecte.split() if m.isalpha()]
    if not mots:
        return False
    if nom_detecte == nom_detecte.lower():  # aucune majuscule du tout
        return False
    if any(m == m.lower() for m in mots):  # au moins un mot tout en minuscules
        return False
    if len(mots) == 1:
        return _est_prenom_connu(mots[0])

    def _forme_valide(m: str) -> bool:
        return (m[0].isupper() and m[1:].islower()) or (m.isupper() and len(m) >= 2)

    forme_ok = all(_forme_valide(m) for m in mots)
    au_moins_un_prenom = any(_est_prenom_connu(m) for m in mots)
    return forme_ok and au_moins_un_prenom

# ---------------------------------------------------------------------------
# SIGNALÉ — Valeurs littérales dans des filtres (where/if ... in (...))
# ---------------------------------------------------------------------------
RE_FILTRE_IN = re.compile(r'(?i)\b(where|if)\b[^;]*\bin\s*\([^)]+\)')
RE_NUMERO_CONTRAT = re.compile(r'\b[A-Z]{2,4}-\d{4}-\d{6,}\b')

# ---------------------------------------------------------------------------
# Identifiants de connexion type 'prenom.nom' (tout en minuscules, séparés
# par un point) — convention standard des logins d'entreprise. Différent de
# la forme 'Jean DUPONT' des commentaires : ici tout est en minuscules, donc
# invisible pour RE_NOM_PROBABLE/spaCy qui exigent une majuscule. Détecté
# spécifiquement À L'INTÉRIEUR des chaînes entre guillemets (valeurs
# littérales de filtre/données), pas dans le code lui-même, pour éviter de
# toucher à d'éventuelles références SAS type bibliotheque.table.
# ---------------------------------------------------------------------------
RE_CHAINE_LITTERALE = re.compile(r"(?<![A-Za-zÀ-ÿ])'([^'\n]*)'|\"([^\"\n]*)\"")
RE_LOGIN_PRENOM_NOM = re.compile(r'\b([a-zà-ÿ]{2,})\.([a-zà-ÿ]{2,})\b')

# ---------------------------------------------------------------------------
# CATÉGORIE C4 (CNIL) — les vrais bloquants légaux, distincts des DCP C3
# classiques. Détection par mots-clés (français, insensible à la casse) :
# santé, origine ethnique/raciale, religion, opinions politiques, syndicat,
# orientation sexuelle, casier judiciaire, données génétiques/biométriques, NIR.
# ⚠️ Heuristique par mots-clés : faux positifs/négatifs possibles — signale
# pour relecture PRIORITAIRE, ne tranche jamais tout seul.
# ---------------------------------------------------------------------------
MOTS_CLES_C4 = {
    "santé":      [r"\bmaladie", r"\bsant[ée]", r"\bpathologie", r"\bhandicap",
                   r"\bhospitalis", r"\bdiagnostic", r"\btraitement.?m[ée]dic"],
    "origine":    [r"\borigine.?(raciale|ethnique)", r"\bethnie"],
    "religion":   [r"\breligio", r"\bconviction.?(religieuse|philosophique)", r"\bculte\b"],
    "politique":  [r"\bopinion.?politique", r"\bappartenance.?politique"],
    "syndicat":   [r"\bsyndica"],
    "orientation":[r"\borientation.?sexuelle", r"\bvie.?sexuelle"],
    "pénal":      [r"\bcasier.?judiciaire", r"\bcondamnation", r"\binfraction.?p[ée]nale",
                   r"\bantécédent.?judiciaire"],
    "génétique":  [r"\bg[ée]n[ée]tique", r"\bADN\b", r"\bbiom[ée]trique"],
    "NIR":        [r"\bNIR\b", r"\bnum[ée]ro.?s[ée]curit[ée].?sociale", r"\bnum[ée]ro.?INSEE"],
}
RE_C4 = {cat: re.compile("|".join(pats), re.IGNORECASE) for cat, pats in MOTS_CLES_C4.items()}

# ---------------------------------------------------------------------------
# SPÉCIFIQUE AUX LOGS — zones à très haut risque : sorties de procédures qui
# affichent de VRAIES VALEURS DE DONNÉES (pas juste de la logique de code).
# ---------------------------------------------------------------------------
RE_PROC_PRINT_START = re.compile(r'(?im)^\s*proc\s+(print|freq|means|report|tabulate)\b')
RE_PUT_STATEMENT = re.compile(r'(?im)^\s*put\s+[^;]+;')
RE_WHERE_ECHO = re.compile(r'(?im)^\s*WHERE\s+.+;')  # SAS réinjecte le WHERE avec valeurs littérales


def _detecter_zones_a_haut_risque_log(code: str, rapport: RapportFichier):
    """Spécifique aux .log : signale les zones qui affichent probablement
    de vraies données (non remplaçables automatiquement de façon fiable)."""
    for m in RE_PROC_PRINT_START.finditer(code):
        ligne = code[:m.start()].count("\n") + 1
        rapport.a_revoir.append(
            f"[DONNÉES AFFICHÉES - ligne {ligne}] {m.group(0).strip()} "
            f"-> cette procédure affiche probablement de vraies valeurs. "
            f"Vérifier la sortie qui suit, ou supprimer ce bloc avant envoi.")
    for m in RE_WHERE_ECHO.finditer(code):
        rapport.a_revoir.append(
            f"[FILTRE AVEC VALEURS RÉELLES] {m.group(0).strip()[:100]} "
            f"-> SAS réinjecte les valeurs littérales du filtre dans le log")
    for m in RE_PUT_STATEMENT.finditer(code):
        rapport.a_revoir.append(
            f"[PUT - possible affichage de donnée] {m.group(0).strip()[:80]} "
            f"-> vérifier si la variable affichée contient une donnée réelle")


def desensibiliser(code: str, nom_fichier: str, annuaire: "Annuaire | None" = None,
                   cartographie_c4: dict[str, dict] | None = None) -> tuple[str, RapportFichier]:
    rapport = RapportFichier(chemin=nom_fichier)
    _mapping_email.clear()
    _compteur_email["n"] = 0

    # --- ÉTAPE 0 : suppression des sorties statistiques collées en commentaire
    # (proc univariate/freq/means) et des NOTE: de log. Fait EN PREMIER pour
    # ne pas analyser inutilement du contenu voué à disparaître.
    blocs_a_supprimer = []
    for bloc in RE_LIGNE_COMMENTAIRE.finditer(code):
        texte = bloc.group(0)
        a_supprimer, motif = _est_sortie_statistique(texte)
        if a_supprimer:
            ligne = code[:bloc.start()].count("\n") + 1
            blocs_a_supprimer.append((bloc.start(), bloc.end(), ligne, motif,
                                      len(texte.splitlines())))
    # suppression de la fin vers le début pour ne pas décaler les positions
    for debut, fin, ligne, motif, nb_lignes in reversed(blocs_a_supprimer):
        code = code[:debut] + code[fin:]
        rapport.remplacements_auto.append(
            f"commentaire supprimé (ligne {ligne}, {nb_lignes} ligne(s)) : {motif}")
    if blocs_a_supprimer:
        # évite de laisser des trous de lignes vides là où les blocs étaient
        code = re.sub(r'\n[ \t]*\n[ \t]*\n+', '\n\n', code)

    # --- ÉTAPE 0bis : signalement (SANS suppression) des commentaires
    # contenant une année ANTÉRIEURE À 2023 — historique d'anciennes
    # modifications, souvent obsolète. On ne supprime PAS automatiquement :
    # certains de ces commentaires portent une règle métier toujours en
    # vigueur. Le relecteur décide au cas par cas depuis le rapport.
    for bloc in RE_LIGNE_COMMENTAIRE.finditer(code):
        texte = bloc.group(0)
        annees = {int(a) for a in RE_ANNEE.findall(texte)}
        anciennes = sorted(a for a in annees if a < ANNEE_SEUIL_OBSOLESCENCE)
        if not anciennes:
            continue
        ligne = code[:bloc.start()].count("\n") + 1
        apercu = " ".join(texte.split())[:110]
        rapport.a_revoir.append(
            f"[COMMENTAIRE ANCIEN - ligne {ligne}] année(s) détectée(s) : "
            f"{', '.join(str(a) for a in anciennes)} (< {ANNEE_SEUIL_OBSOLESCENCE}) "
            f"| contenu : \"{apercu}\" "
            f"-> commentaire potentiellement obsolète : À RELIRE et supprimer "
            f"manuellement si l'information n'est plus pertinente.")

    # --- Règle 1 : mots de passe -> vidés ---
    def _vide_password(m):
        rapport.remplacements_auto.append(f"password vidé : {m.group(0)[:40]}")
        cle = re.match(r'(?i)(password|pwd|pass)\s*=', m.group(0)).group(1)
        return f"{cle}="
    code = RE_PASSWORD.sub(_vide_password, code)

    # --- Règle 1 : user= dans connexions -> USERNAME générique ---
    def _generique_user(m):
        rapport.remplacements_auto.append(f"user généralisé : {m.group(0)[:40]}")
        return "user=USERNAME"
    code = RE_USER_CONN.sub(_generique_user, code)

    # --- Règle 2 : emails -> génériques (mapping cohérent par email) ---
    def _email_wrapper(m):
        avant = m.group(0)
        apres = _remplace_email(m)
        rapport.remplacements_auto.append(f"email : {avant} -> {apres}")
        return apres
    code = RE_EMAIL.sub(_email_wrapper, code)

    # --- Chemins réseau \\serveur\... : DÉCISION ACTUELLE = on les GARDE TELS
    #     QUELS (pas de remplacement par CHEMIN_SOURCE_xx pour le moment).
    #     Le bloc de remplacement est volontairement désactivé. Pour le
    #     réactiver plus tard, remettre l'appel à RE_CHEMIN_RESEAU.sub ci-dessous.
    # def _remplace_chemin(m):
    #     avant = m.group(0)
    #     apres = _alias_chemin(avant)
    #     rapport.mapping_chemins[avant] = apres
    #     return apres
    # code = RE_CHEMIN_RESEAU.sub(_remplace_chemin, code)

    # --- Signalement : path= dans connexions Oracle/SQL (hors chemin \\...,
    #     déjà traité ci-dessus ; ex. path=SERVEUR_PROD.caa.local sans \\) ---
    for m in RE_HOST_ORACLE.finditer(code):
        rapport.a_revoir.append(f"[SERVEUR/PATH] {m.group(0)[:60]} "
                                 f"-> vérifier s'il s'agit d'un nom de serveur réel")

    # --- Noms probables dans les commentaires -> trigramme (remplacé) ---
    # Priorité à spaCy (NER, comprend le contexte) si disponible ; sinon
    # repli sur le dictionnaire de prénoms. Mapping cohérent par fichier :
    # le même nom complet donne toujours le même trigramme. Formes partielles
    # gérées SI ET SEULEMENT SI croisables avec un nom complet déjà confirmé
    # ailleurs dans le même fichier (jamais de devinette à partir de rien) :
    #   - NOM DE FAMILLE seul (sans prénom)         : "DUPONT" -> JDU
    #   - Prénom + initiale du nom ("Jean D.")       : -> JDU (si Jean+D matche)
    #   - Initiale + nom de famille ("J Dupont")     : -> JDU (si J+Dupont matche)
    # Les initiales isolées ('JD') restent NON remplacées automatiquement :
    # trop de collisions avec de vrais codes métier SAS à 2 lettres
    # (ex. 'AC', 'MC', 'A2'...) -> resterait un signalement manuel si besoin.
    spacy_disponible = _charger_spacy() is not None
    noms_a_remplacer: list[tuple[str, str, int]] = []  # (texte_trouvé, trigramme, position)
    surnames_connus: dict[str, str] = {}   # NOM (maj) -> trigramme (officiel si annuaire, sinon calculé)
    prenoms_connus: dict[str, tuple[str, str]] = {}  # Prénom -> (initiale_nom, trigramme)

    # =========================================================================
    # PASSE PRIORITAIRE : ANNUAIRE ENTREPRISE (source de vérité officielle).
    # Utilise l'INITIALE RH réelle, pas un trigramme recalculé. Couvre :
    #   - LOGIN exact ('laurent.desanglois') dans les chaînes littérales
    #   - NOM+PRENOM (dans les deux ordres) dans les commentaires
    #   - NOM seul, SI ce NOM est unique dans l'annuaire (pas d'homonymes)
    # Pré-remplit surnames_connus/prenoms_connus : les passes suivantes
    # (formes abrégées 'Jean D.', 'J Dupont') en bénéficient automatiquement,
    # avec le trigramme OFFICIEL plutôt qu'un trigramme recalculé.
    # =========================================================================
    if annuaire and len(annuaire) > 0:
        # 1) LOGIN exact, dans les chaînes entre guillemets
        for m_chaine in RE_CHAINE_LITTERALE.finditer(code):
            contenu = m_chaine.group(1) if m_chaine.group(1) is not None else m_chaine.group(2)
            offset = m_chaine.start() + 1
            login_lower = contenu.strip().lower()
            login_sans_domaine = login_lower.split("@")[0] if "@" in login_lower else login_lower
            trig_login = annuaire.par_login.get(login_lower) or annuaire.par_login.get(login_sans_domaine)
            if trig_login:
                trig = trig_login
                pos_abs = offset
                noms_a_remplacer.append((contenu, trig, pos_abs))
                rapport.mapping_noms[f"{contenu} (login, annuaire)"] = trig
                if "." in login_lower:
                    p, n = login_lower.split(".", 1)
                    surnames_connus[n.upper()] = trig
                    prenoms_connus[p.capitalize()] = (n[0].upper(), trig)

        # 2) NOM+PRENOM (ou PRENOM+NOM), dans les commentaires, recherche
        #    directe contre l'annuaire (pas besoin de spaCy pour ces cas :
        #    on sait déjà que c'est un vrai employé)
        RE_MOT_MAJ = re.compile(r'\b[A-ZÀ-Ý][a-zà-ÿ]+\b|\b[A-ZÀ-Ý]{2,}\b')
        for bloc in RE_LIGNE_COMMENTAIRE.finditer(code):
            texte = bloc.group(0)
            mots_trouves = list(RE_MOT_MAJ.finditer(texte))
            for i in range(len(mots_trouves) - 1):
                m1, m2 = mots_trouves[i], mots_trouves[i + 1]
                if texte[m1.end():m2.start()].strip():  # doivent être adjacents (juste un espace)
                    continue
                w1, w2 = m1.group(0).upper(), m2.group(0).upper()
                trig = (annuaire.par_nom_prenom.get((w1, w2)) or
                       annuaire.par_nom_prenom.get((w2, w1)) or
                       annuaire.par_nom_prenom.get((_sans_accents(w1), _sans_accents(w2))) or
                       annuaire.par_nom_prenom.get((_sans_accents(w2), _sans_accents(w1))))
                if trig:
                    pos_abs = bloc.start() + m1.start()
                    texte_complet = texte[m1.start():m2.end()]
                    if _deja_couvert_precoce(pos_abs, noms_a_remplacer):
                        continue
                    noms_a_remplacer.append((texte_complet, trig, pos_abs))
                    rapport.mapping_noms[f"{texte_complet} (annuaire)"] = trig
                    surnames_connus[w1] = trig
                    surnames_connus[w2] = trig
                    # mémorise le prénom réel (pas le nom) pour les formes abrégées
                    for w in (w1, w2):
                        if annuaire.par_nom_seul.get(w) == trig and w != (w1 if w1 in annuaire.noms else w2):
                            pass
                    nom_reel = w1 if w1 in annuaire.noms else (w2 if w2 in annuaire.noms else None)
                    prenom_reel = w2 if nom_reel == w1 else w1
                    if nom_reel:
                        prenoms_connus[prenom_reel.capitalize()] = (nom_reel[0], trig)

        # 3) NOM seul (sans prénom), UNIQUEMENT si non-ambigu dans l'annuaire.
        #    Absorbe aussi un PRÉNOM ou une INITIALE juste avant, pour que
        #    'Léana MADJOU' ou 'M SEOSSE' disparaisse ENTIÈREMENT (sinon on
        #    laisserait 'Léana LMA' / 'M MSE', le prénom restant en clair).
        if annuaire.par_nom_seul:
            RE_NOM_ANNUAIRE = re.compile(
                r'(?:\b([A-ZÀ-Ýa-zà-ÿ][A-Za-zÀ-ÿ\-]*)\.?\s+)?'   # prénom/initiale optionnel
                r'\b(' + '|'.join(re.escape(n) for n in annuaire.par_nom_seul) + r')\b')
            for bloc in RE_LIGNE_COMMENTAIRE.finditer(code):
                texte = bloc.group(0)
                for m in RE_NOM_ANNUAIRE.finditer(texte):
                    pos_abs = bloc.start() + m.start()
                    if _deja_couvert_precoce(pos_abs, noms_a_remplacer):
                        continue
                    nom_trouve = m.group(2)
                    # Jamais un mois/jour, même s'il figure dans l'annuaire
                    if _ne_jamais_remplacer(nom_trouve):
                        continue
                    trig = annuaire.par_nom_seul[nom_trouve]
                    mot_avant = m.group(1)
                    # Ne pas absorber un mot précédent qui est un mois/jour
                    if mot_avant and _ne_jamais_remplacer(mot_avant):
                        mot_avant = None
                    # n'absorbe le mot précédent QUE s'il est plausible comme
                    # prénom/initiale de cette personne (évite d'avaler un mot
                    # quelconque : 'par MADJOU' ne doit pas manger 'par')
                    prenoms_de_ce_nom = {_sans_accents(p) for p in
                                         annuaire.prenoms_par_nom.get(nom_trouve, set())}
                    absorbe = False
                    if mot_avant:
                        ma_norm = _sans_accents(mot_avant)
                        absorbe = (ma_norm in prenoms_de_ce_nom or
                                   (len(ma_norm) == 1 and
                                    any(p.startswith(ma_norm) for p in prenoms_de_ce_nom)))
                    texte_remplace = m.group(0) if absorbe else nom_trouve
                    pos_reelle = pos_abs if absorbe else bloc.start() + m.start(2)
                    noms_a_remplacer.append((texte_remplace, trig, pos_reelle))
                    rapport.mapping_noms[f"{texte_remplace} (annuaire, NOM non-ambigu)"] = trig
                    surnames_connus[nom_trouve] = trig
                    # Signalement faux positif probable : le "nom" est un mot courant
                    # (ex. 'RIEN' -> 'IEN'). Remplacé par prudence, mais à vérifier.
                    if _est_faux_positif_nom(nom_trouve) and not absorbe:
                        ligne_fp = code[:pos_reelle].count("\n") + 1
                        rapport.faux_positifs.append(
                            f"nom '{nom_trouve}' (valeur littérale, annuaire) -> "
                            f"trigramme '{trig}' -> ligne {ligne_fp} "
                            f"-> À VÉRIFIER : mot courant probablement PAS un vrai nom.")

        # 4) PRÉNOM/INITIALE + NOM, y compris quand le NOM SEUL est AMBIGU
        #    (plusieurs personnes le portent) : on désambiguïse par la 1re
        #    lettre du prénom. Ex. 3 SEOSSE dans l'annuaire mais un seul dont
        #    le prénom commence par M -> 'M SEOSSE' est résolvable.
        #    C'est ce qui manquait pour 'M SEOSSE', 'J MARTEIL', 'Léana MADJOU'.
        if annuaire.par_nom_initiale_prenom:
            noms_index = sorted({n for (n, _i) in annuaire.par_nom_initiale_prenom},
                                key=len, reverse=True)
            if noms_index:
                RE_PRENOM_NOM_AMBIGU = re.compile(
                    r'\b([A-ZÀ-Ýa-zà-ÿ][A-Za-zÀ-ÿ\-]*)\.?[ \t]+'
                    r'(' + '|'.join(re.escape(n) for n in noms_index) + r')\b')
                for bloc in RE_LIGNE_COMMENTAIRE.finditer(code):
                    texte = bloc.group(0)
                    for m in RE_PRENOM_NOM_AMBIGU.finditer(texte):
                        pos_abs = bloc.start() + m.start()
                        if _deja_couvert_precoce(pos_abs, noms_a_remplacer):
                            continue
                        mot_avant, nom_trouve = m.group(1), m.group(2)
                        initiale_prenom = _sans_accents(mot_avant)[:1]
                        trig = annuaire.par_nom_initiale_prenom.get(
                            (nom_trouve, initiale_prenom))
                        if not trig:
                            continue  # combinaison ambiguë ou inconnue -> on ne devine pas
                        noms_a_remplacer.append((m.group(0), trig, pos_abs))
                        rapport.mapping_noms[
                            f"{m.group(0)} (annuaire, désambiguïsé par initiale)"] = trig
                        surnames_connus[nom_trouve] = trig

    for bloc in RE_LIGNE_COMMENTAIRE.finditer(code):
        texte = bloc.group(0)
        if spacy_disponible:
            personnes = _detecter_personnes_spacy(texte)
            for nom_detecte in personnes:
                mots = nom_detecte.split()
                prenom = mots[0]
                nom_famille = mots[-1] if len(mots) > 1 else mots[0]
                pos_abs = bloc.start() + texte.find(nom_detecte)
                if pos_abs < 0 or _deja_couvert_precoce(pos_abs, noms_a_remplacer):
                    continue  # déjà traité par l'annuaire -> pas de doublon
                trig = _trigramme(prenom, nom_famille)
                noms_a_remplacer.append((nom_detecte, trig, pos_abs))
                surnames_connus[nom_famille.upper()] = trig
                prenoms_connus[prenom] = (nom_famille[0].upper(), trig)
                rapport.mapping_noms[nom_detecte] = trig
        else:
            for m in RE_NOM_PROBABLE.finditer(texte):
                premier_mot, deuxieme_mot = m.group(1), m.group(2)
                if not _est_prenom_connu(premier_mot):
                    continue
                # Jamais un mois/jour comme "nom" ni comme "prénom"
                if _ne_jamais_remplacer(premier_mot) or _ne_jamais_remplacer(deuxieme_mot):
                    continue
                pos_abs = bloc.start() + m.start()
                if _deja_couvert_precoce(pos_abs, noms_a_remplacer):
                    continue  # déjà traité par l'annuaire -> pas de doublon
                trig = _trigramme(premier_mot, deuxieme_mot)
                noms_a_remplacer.append((m.group(0), trig, pos_abs))
                surnames_connus[deuxieme_mot.upper()] = trig
                prenoms_connus[premier_mot] = (deuxieme_mot[0].upper(), trig)
                rapport.mapping_noms[m.group(0)] = trig

    def _deja_couvert(pos_abs: int) -> bool:
        return any(p <= pos_abs < p + len(t) for t, _, p in noms_a_remplacer)

    # 2e passe : NOM DE FAMILLE seul (sans prénom), déjà connu par la 1ère
    # passe, réapparaissant ailleurs dans un commentaire -> même trigramme.
    if surnames_connus:
        RE_SURNAME = re.compile(r'\b(' + '|'.join(re.escape(s) for s in surnames_connus) + r')\b')
        for bloc in RE_LIGNE_COMMENTAIRE.finditer(code):
            texte = bloc.group(0)
            for m in RE_SURNAME.finditer(texte):
                pos_abs = bloc.start() + m.start()
                if _deja_couvert(pos_abs):
                    continue
                trig = surnames_connus[m.group(1)]
                noms_a_remplacer.append((m.group(0), trig, pos_abs))
                rapport.mapping_noms[m.group(0)] = trig

    # 3e passe : formes abrégées croisées avec un nom COMPLET déjà confirmé
    # dans ce même fichier (jamais de nouvelle identité inventée à partir
    # d'une simple initiale) :
    #   "Jean D."  -> prénom connu + initiale correspondant au nom connu
    #   "J Dupont" -> initiale correspondant au prénom connu + nom connu
    if prenoms_connus:
        alt_prenoms = '|'.join(re.escape(p) for p in prenoms_connus)
        RE_PRENOM_INITIALE = re.compile(rf'\b({alt_prenoms})\s+([A-ZÀ-Ý])\.?(?!\w)')
        for bloc in RE_LIGNE_COMMENTAIRE.finditer(code):
            texte = bloc.group(0)
            for m in RE_PRENOM_INITIALE.finditer(texte):
                prenom_trouve, initiale = m.group(1), m.group(2)
                initiale_attendue, trig = prenoms_connus[prenom_trouve]
                if initiale != initiale_attendue:
                    continue  # initiale incohérente avec le nom connu -> pas de devinette
                pos_abs = bloc.start() + m.start()
                if _deja_couvert(pos_abs):
                    continue
                noms_a_remplacer.append((m.group(0), trig, pos_abs))
                rapport.mapping_noms[m.group(0)] = trig

    if surnames_connus:
        # initiale + NOM connu ("J Dupont") : reconstruit le prénom via
        # prenoms_connus (recherche inverse initiale -> prénom(s) compatibles)
        initiale_vers_prenoms: dict[str, list[str]] = {}
        for p, (init_nom, trig) in prenoms_connus.items():
            initiale_vers_prenoms.setdefault(p[0].upper(), []).append(p)
        alt_noms = '|'.join(re.escape(s) for s in surnames_connus)
        RE_INITIALE_NOM = re.compile(rf'\b([A-ZÀ-Ý])\.?\s+({alt_noms})\b', re.IGNORECASE)
        for bloc in RE_LIGNE_COMMENTAIRE.finditer(code):
            texte = bloc.group(0)
            for m in RE_INITIALE_NOM.finditer(texte):
                initiale, nom_trouve = m.group(1).upper(), m.group(2).upper()
                trig = surnames_connus[nom_trouve]
                # vérifie que l'initiale correspond bien à un prénom déjà
                # associé à CE nom précis (pas juste n'importe quel prénom
                # commençant par cette lettre, pour rester rigoureux)
                candidats = initiale_vers_prenoms.get(initiale, [])
                if not any(prenoms_connus[c][1] == trig for c in candidats):
                    continue
                pos_abs = bloc.start() + m.start()
                if _deja_couvert(pos_abs):
                    continue
                noms_a_remplacer.append((m.group(0), trig, pos_abs))
                rapport.mapping_noms[m.group(0)] = trig

    # --- Signalement (pas de remplacement) : formes abrégées GÉNÉRIQUES qui
    # n'ont pas pu être cross-référencées avec un nom complet connu du fichier
    # (ex. 'L. Desanglaois' si 'Lucie DESANGLAOIS' n'apparaît nulle part
    # ailleurs) — sans cette passe, ces cas restaient invisibles. On ne peut
    # pas construire un trigramme fiable sans le nom complet, donc signalement
    # manuel plutôt que remplacement automatique risqué.
    # (Exécuté AVANT les substitutions ci-dessous, sur le code encore intact,
    # pour que les positions restent alignées avec celles de noms_a_remplacer.)
    RE_INITIALE_NOM_GENERIQUE = re.compile(r'\b([A-ZÀ-Ý])\.\s*([A-ZÀ-Ý][a-zà-ÿ]{2,}|[A-ZÀ-Ý]{2,})\b')
    positions_deja_traitees = {p for _, _, p in noms_a_remplacer}
    for bloc in RE_LIGNE_COMMENTAIRE.finditer(code):
        texte = bloc.group(0)
        ligne_debut = code[:bloc.start()].count("\n") + 1
        for m in RE_INITIALE_NOM_GENERIQUE.finditer(texte):
            pos_abs = bloc.start() + m.start()
            if any(p <= pos_abs < p + 30 for p in positions_deja_traitees):
                continue  # zone déjà couverte par un remplacement auto
            ligne_du_match = ligne_debut + texte[:m.start()].count("\n")
            debut_ligne = texte.rfind("\n", 0, m.start()) + 1
            fin_ligne = texte.find("\n", m.end())
            if fin_ligne == -1:
                fin_ligne = len(texte)
            contexte = texte[debut_ligne:fin_ligne].strip()
            rapport.a_revoir.append(
                f"[NOM ABRÉGÉ non cross-référencé - ligne {ligne_du_match}] "
                f"'{m.group(0)}' | contexte : \"{contexte}\" "
                f"-> forme 'Initiale. Nom' possible, mais aucun nom complet "
                f"correspondant trouvé ailleurs dans ce fichier pour confirmer "
                f"le trigramme -> À VÉRIFIER ET REMPLACER MANUELLEMENT si "
                f"c'est bien un nom de personne.")

    # --- Identifiants de connexion 'prenom.nom' (minuscules, dans les
    # chaînes littérales) -> établit une identité complète comme une vraie
    # détection de nom (le login révèle prénom ET nom), et se connecte au
    # même mapping que les noms détectés en commentaires (cohérence
    # croisée : 'laurent.desanglois' et 'Laurent DESANGLOIS' donnent le
    # même trigramme s'ils désignent la même personne).
    for m_chaine in RE_CHAINE_LITTERALE.finditer(code):
        contenu = m_chaine.group(1) if m_chaine.group(1) is not None else m_chaine.group(2)
        offset_contenu = m_chaine.start() + 1  # +1 pour sauter le guillemet ouvrant
        for m in RE_LOGIN_PRENOM_NOM.finditer(contenu):
            prenom_login, nom_login = m.group(1), m.group(2)
            if not _est_prenom_connu(prenom_login):
                continue  # pas de prénom reconnu -> pourrait être autre chose
                          # (ex. un nom de champ technique) -> pas de devinette
            trig = _trigramme(prenom_login, nom_login)
            pos_abs = offset_contenu + m.start()
            if _deja_couvert(pos_abs):
                continue
            noms_a_remplacer.append((m.group(0), trig, pos_abs))
            surnames_connus[nom_login.upper()] = trig
            prenoms_connus[prenom_login] = (nom_login[0].upper(), trig)
            rapport.mapping_noms[m.group(0)] = trig

    # Application des remplacements de noms, du plus loin au plus proche dans
    # le texte (positions décroissantes) pour ne pas décaler les index déjà
    # calculés au fur et à mesure des remplacements successifs.
    for texte_trouve, trig, pos in sorted(noms_a_remplacer, key=lambda x: -x[2]):
        code = code[:pos] + trig + code[pos + len(texte_trouve):]
        rapport.remplacements_auto.append(f"nom '{texte_trouve}' -> trigramme '{trig}'")

    # --- Noms de famille CONNUS DE L'ANNUAIRE apparaissant comme valeur
    # littérale dans le code (ex. filtre nom_client in ('DUPONT')). Ciblé
    # sur l'annuaire (source de vérité), PAS un signalement générique de
    # tout "in (...)" -> élimine le bruit des filtres techniques/métier
    # sans rapport avec des personnes (dates, codes produit, libellés...).
    # --- Noms de famille CONNUS DE L'ANNUAIRE apparaissant comme valeur
    # littérale dans le code (ex. filtre nom_client in ('DUPONT')). Ciblé
    # sur l'annuaire (source de vérité), PAS un signalement générique de
    # tout "in (...)" -> élimine le bruit des filtres techniques/métier
    # sans rapport avec des personnes (dates, codes produit, libellés...).
    # Condition len>3 : évite de re-matcher un trigramme (3 lettres) déjà
    # posé par une substitution précédente comme s'il était un nouveau nom.
    if annuaire and annuaire.par_nom_seul:
        for m_chaine in RE_CHAINE_LITTERALE.finditer(code):
            contenu = m_chaine.group(1) if m_chaine.group(1) is not None else m_chaine.group(2)
            valeur_maj = contenu.strip().upper()
            if _ne_jamais_remplacer(contenu.strip()):
                continue  # jamais un mois/jour
            if valeur_maj in annuaire.par_nom_seul and len(contenu.strip()) > 3:
                offset = m_chaine.start() + 1
                trig = annuaire.par_nom_seul[valeur_maj]
                code = code[:offset] + trig + code[offset + len(contenu):]
                rapport.remplacements_auto.append(
                    f"nom '{contenu}' (valeur littérale, annuaire) -> trigramme '{trig}'")
                rapport.mapping_noms[f"{contenu} (valeur littérale, annuaire)"] = trig

    for m in RE_NUMERO_CONTRAT.finditer(code):
        rapport.identifiants.append(
            f"[IDENTIFIANT - règle en évolution] {m.group(0)} "
            f"-> décision MA du 29/07/2026 (Christophe) : les identifiants (SIN, "
            f"n° contrat...) ne sont PAS à anonymiser par défaut (base OPTIM). "
            f"À confirmer si ce cas précis est bien un identifiant pur, ou une "
            f"donnée DCP à traiter quand même.")

    # --- Signalement PRIORITAIRE : colonnes C4 officielles (cartographie
    # exacte) — détecte les noms de colonnes utilisés comme identifiants
    # dans le code SAS (ex. 'where DTINVAL ne .', 'keep=... SEQUELLES').
    # Confiance HAUTE : ce sont les noms de colonnes réels, pas une
    # déduction depuis du texte libre.
    if cartographie_c4:
        RE_IDENTIFIANT_SAS = re.compile(r'\b[A-Za-zÀ-ÿ_][A-Za-zÀ-ÿ0-9_]*\b')
        for m in RE_IDENTIFIANT_SAS.finditer(code):
            nom_col = m.group(0).upper()
            info = cartographie_c4.get(nom_col)
            if not info:
                continue
            ligne = code[:m.start()].count("\n") + 1
            debut_ligne = code.rfind("\n", 0, m.start()) + 1
            fin_ligne = code.find("\n", m.end())
            if fin_ligne == -1:
                fin_ligne = len(code)
            contexte = code[debut_ligne:fin_ligne].strip()
            tables_str = ", ".join(f"{b}.{t}" for b, t in info["tables"][:3])
            rapport.alertes_c4.append(
                f"[C4 - CARTOGRAPHIE OFFICIELLE - ligne {ligne}] colonne '{m.group(0)}' "
                f"| catégorie : {info['categorie']} | classification : "
                f"{info['classification']} | connue dans : {tables_str} "
                f"| contexte : \"{contexte[:120]}\" "
                f"-> chargement sur GCP INTERDIT sans désensibilisation "
                f"(prod ET non-prod). Colonne officiellement classée C4.")

    # --- Signalement PRIORITAIRE : catégories C4 (vrais bloquants légaux GCP) ---
    # Détection par MOTS-CLÉS en texte libre — complémentaire à la cartographie
    # officielle ci-dessus (couvre les mentions en commentaires/descriptions
    # qui n'utilisent pas forcément le nom de colonne exact).
    for categorie, pattern in RE_C4.items():
        for m in pattern.finditer(code):
            ligne = code[:m.start()].count("\n") + 1
            debut_ligne = code.rfind("\n", 0, m.start()) + 1
            fin_ligne = code.find("\n", m.end())
            if fin_ligne == -1:
                fin_ligne = len(code)
            contexte = code[debut_ligne:fin_ligne].strip()
            rapport.alertes_c4.append(
                f"[C4 - {categorie.upper()} - ligne {ligne}] motif : '{m.group(0)}' "
                f"| contexte : \"{contexte[:120]}\" "
                f"-> catégorie CNIL C4 potentielle : chargement sur GCP INTERDIT sans "
                f"désensibilisation (prod ET non-prod). Vérification prioritaire.")

    # --- Masquage des DONNÉES CHIFFRÉES sensibles (IBAN, téléphones, séries
    # de chiffres : sinistre/contrat/NOCLI/code C.E), dans le code, les
    # commentaires ET les filtres. Fait EN DERNIER pour que les positions des
    # remplacements de noms ci-dessus ne soient pas décalées. ---
    code = _masquer_donnees_chiffrees(code, rapport)

    # --- Spécifique aux logs : zones à très haut risque (vraies données) ---
    if nom_fichier.lower().endswith(".log"):
        _detecter_zones_a_haut_risque_log(code, rapport)

    return code, rapport


def traiter_fichier(chemin: Path, annuaire: "Annuaire | None" = None,
                    cartographie_c4: dict[str, dict] | None = None,
                    dossier_sortie: Path | None = None):
    """Désensibilise un fichier. Si dossier_sortie est fourni, le fichier
    désensibilisé et son rapport y sont écrits (arborescence miroir) ; sinon,
    ils sont écrits à côté du fichier source (comportement historique)."""
    code = chemin.read_text(encoding="latin-1", errors="replace")
    code_propre, rapport = desensibiliser(code, chemin.name, annuaire=annuaire,
                                          cartographie_c4=cartographie_c4)

    if dossier_sortie is not None:
        dossier_sortie.mkdir(parents=True, exist_ok=True)
        sortie = dossier_sortie / (chemin.stem + "_desensibilise" + chemin.suffix)
        rapport_path = dossier_sortie / (chemin.stem + "_rapport_desensibilisation.txt")
    else:
        sortie = chemin.with_name(chemin.stem + "_desensibilise" + chemin.suffix)
        rapport_path = chemin.with_name(chemin.stem + "_rapport_desensibilisation.txt")
    sortie.write_text(code_propre, encoding="latin-1")
    with open(rapport_path, "w", encoding="utf-8") as f:
        f.write(f"RAPPORT DE DÉSENSIBILISATION — {rapport.chemin}\n")
        f.write("=" * 70 + "\n")
        f.write("⚠️  CE RAPPORT CONTIENT DES VALEURS RÉELLES (tables de correspondance) —\n")
        f.write("   USAGE INTERNE UNIQUEMENT. Ne JAMAIS transmettre ce fichier à l'usine\n")
        f.write("   offshore : seul le fichier *_desensibilise.sas doit être envoyé.\n")
        f.write("=" * 70 + "\n\n")

        if rapport.alertes_c4:
            f.write(f"🔴 ALERTE C4 — BLOQUANT LÉGAL POTENTIEL ({len(rapport.alertes_c4)} élément(s)) :\n")
            f.write("   Catégories santé/origine/religion/syndicat/orientation sexuelle/\n")
            f.write("   pénal/génétique/NIR : chargement GCP interdit sans désensibilisation.\n")
            f.write("   À VÉRIFIER EN PRIORITÉ ABSOLUE avant tout envoi.\n")
            for r in rapport.alertes_c4:
                f.write(f"  - {r}\n")
            f.write("\n")

        f.write(f"Remplacements automatiques effectués ({len(rapport.remplacements_auto)}) :\n")
        for r in rapport.remplacements_auto:
            f.write(f"  - {r}\n")

        if rapport.mapping_noms:
            f.write(f"\n📋 Table de correspondance NOMS (audit/traçabilité — "
                    f"{len(rapport.mapping_noms)} entrée(s)) :\n")
            for original, trig in rapport.mapping_noms.items():
                f.write(f"  - \"{original}\" -> {trig}\n")

        if rapport.mapping_chemins:
            f.write(f"\n📋 Table de correspondance CHEMINS (audit/traçabilité — "
                    f"{len(rapport.mapping_chemins)} entrée(s)) :\n")
            for original, alias in rapport.mapping_chemins.items():
                f.write(f"  - \"{original}\" -> {alias}\n")

        if rapport.faux_positifs:
            f.write(f"\n⚠️  FAUX POSITIFS PROBABLES — noms remplacés qui semblent être des "
                    f"mots courants ({len(rapport.faux_positifs)}) :\n")
            f.write("   Ces remplacements ont été appliqués par prudence, mais le \"nom\"\n")
            f.write("   ressemble à un mot ordinaire. À vérifier et éventuellement annuler.\n")
            for r in rapport.faux_positifs:
                f.write(f"  - {r}\n")

        f.write(f"\n⚠️  À VÉRIFIER MANUELLEMENT — DCP nominatives ({len(rapport.a_revoir)} élément(s)) :\n")
        if not rapport.a_revoir:
            f.write("  (aucun signalement — relecture manuelle reste obligatoire)\n")
        for r in rapport.a_revoir:
            f.write(f"  - {r}\n")
        f.write(f"\nℹ️  IDENTIFIANTS repérés ({len(rapport.identifiants)}) — "
                f"règle en évolution, PAS d'action requise par défaut :\n")
        for r in rapport.identifiants:
            f.write(f"  - {r}\n")
        f.write("\n" + "=" * 70 + "\n")
        f.write("RAPPEL : ce rapport ne remplace pas la relecture intégrale du code\n")
        f.write("exigée par la note de désensibilisation (étape 4). En cas de doute,\n")
        f.write("contacter le référent RGPD de votre BU/SU.\n")

    print(f"{chemin.name} -> {sortie.name}")
    print(f"  {len(rapport.remplacements_auto)} remplacement(s) automatique(s)")
    print(f"  {len(rapport.a_revoir)} élément(s) à vérifier manuellement (voir {rapport_path.name})")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage : python desensibilisation_code.py fichier.sas [--annuaire chemin.csv]")
        print("        python desensibilisation_code.py fichier.log [--annuaire chemin.csv]")
        print("        python desensibilisation_code.py --dossier chemin/ [--annuaire chemin.csv]")
        print("        python desensibilisation_code.py --lot \"M:\\DSIN\\Lot 1\" [--output DEST] [--annuaire chemin.csv]")
        print("        python desensibilisation_code.py --lot \"M:\\DSIN\\Lot 1\" --categorie Climatique \"Satisfaction client\"")
        print("            (seulement les répertoires choisis, même arborescence de sortie)")
        print("            (parcourt tout le Lot 1 par catégorie -> arborescence miroir")
        print("             sous <Lot 1>/desensibilisation/<categorie>/...)")
        sys.exit(1)

    chemin_annuaire = None
    if "--annuaire" in sys.argv:
        chemin_annuaire = sys.argv[sys.argv.index("--annuaire") + 1]  # surcharge ponctuelle
    elif CHEMIN_ANNUAIRE_DEFAUT and Path(CHEMIN_ANNUAIRE_DEFAUT).exists():
        chemin_annuaire = CHEMIN_ANNUAIRE_DEFAUT  # utilisé automatiquement
    ann = get_annuaire(chemin_annuaire)

    chemin_carto_c4 = None
    if "--carto-c4" in sys.argv:
        chemin_carto_c4 = sys.argv[sys.argv.index("--carto-c4") + 1]
    elif CHEMIN_CARTO_C4_DEFAUT and Path(CHEMIN_CARTO_C4_DEFAUT).exists():
        chemin_carto_c4 = CHEMIN_CARTO_C4_DEFAUT
    carto_c4 = get_cartographie_c4(chemin_carto_c4)

    if sys.argv[1] == "--lot":
        # Parcourt TOUT le Lot 1 (racine passée en argument), dossier de premier
        # niveau par dossier de premier niveau (Pilotage activité, Satisfaction
        # client, Climatique...), et écrit les résultats dans une arborescence
        # MIROIR sous <racine>/desensibilisation/<categorie>/... (ou --output).
        # Chaque catégorie garde sa structure interne d'origine.
        racine = Path(sys.argv[2])
        if not racine.is_dir():
            print(f"[erreur] Racine introuvable : {racine}")
            sys.exit(1)
        # Destination racine des sorties
        if "--output" in sys.argv:
            base_sortie = Path(sys.argv[sys.argv.index("--output") + 1])
        else:
            base_sortie = racine / "desensibilisation"

        # Catégories = sous-dossiers de premier niveau (hors 'desensibilisation'
        # et 'cartographie' pour ne pas se traiter soi-même / re-traiter).
        exclus = {"desensibilisation", "cartographie"}
        categories = [d for d in sorted(racine.iterdir())
                      if d.is_dir() and d.name.lower() not in exclus]
        if not categories:
            print(f"[erreur] Aucun sous-dossier de catégorie sous {racine}")
            sys.exit(1)

        # --categorie A "B C" ... : ne traiter QUE ces répertoires de premier
        # niveau (comparaison sans casse ni accents). Sans l'option : tous.
        if "--categorie" in sys.argv:
            i = sys.argv.index("--categorie") + 1
            demandees = []
            while i < len(sys.argv) and not sys.argv[i].startswith("--"):
                demandees.append(sys.argv[i])
                i += 1
            if not demandees:
                print("[erreur] --categorie attend au moins un nom de répertoire.")
                sys.exit(1)
            par_nom = {_sans_accents(c.name).strip(): c for c in categories}
            inconnues = [d for d in demandees if _sans_accents(d).strip() not in par_nom]
            if inconnues:
                print(f"[erreur] Répertoire(s) introuvable(s) sous {racine} : {inconnues}")
                print("         Répertoires disponibles : "
                      + ", ".join(f'"{c.name}"' for c in categories))
                sys.exit(1)
            categories = [par_nom[_sans_accents(d).strip()] for d in demandees]
            print(f"Répertoire(s) traité(s) : {', '.join(c.name for c in categories)}")

        total = 0
        for cat in categories:
            fichiers = sorted(list(cat.rglob("*.sas")) + list(cat.rglob("*.log")))
            # Ne pas retraiter des fichiers déjà désensibilisés
            fichiers = [f for f in fichiers
                        if "_desensibilise" not in f.stem
                        and "desensibilisation" not in {p.lower() for p in f.parts}]
            if not fichiers:
                continue
            print(f"\n=== Catégorie : {cat.name} ({len(fichiers)} fichier(s)) ===")
            for f in fichiers:
                # Reproduire la sous-arborescence interne de la catégorie
                sous_chemin = f.parent.relative_to(cat)
                dest = base_sortie / cat.name / sous_chemin
                traiter_fichier(f, annuaire=ann, cartographie_c4=carto_c4,
                                dossier_sortie=dest)
                total += 1
        print(f"\n==================================================")
        print(f"TOTAL : {total} fichier(s) désensibilisé(s) dans {base_sortie}")
        print(f"Arborescence miroir par catégorie : {base_sortie}\\<categorie>\\...")
    elif sys.argv[1] == "--dossier":
        dossier = Path(sys.argv[2])
        fichiers = sorted(list(dossier.rglob("*.sas")) + list(dossier.rglob("*.log")))
        if not fichiers:
            print(f"Aucun .sas/.log trouvé sous {dossier}")
        for f in fichiers:
            traiter_fichier(f, annuaire=ann, cartographie_c4=carto_c4)
    else:
        chemin = Path(sys.argv[1])
        traiter_fichier(chemin, annuaire=ann, cartographie_c4=carto_c4)
        if chemin.suffix.lower() == ".log":
            print("\n⚠️  RAPPEL LOG : les regex ne peuvent PAS détecter de façon fiable")
            print("   les vraies valeurs de données affichées par PROC PRINT/FREQ/PUT.")
            print("   Le plus sûr reste souvent de RE-GÉNÉRER le log à partir d'un run")
            print("   sur des données déjà anonymisées, plutôt que de nettoyer a posteriori")
            print("   un log qui contient de vraies données clients.")