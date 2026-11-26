/* ============================================================================================
   LANCEUR D'ANONYMISATION - MISSION SAS (DCT)
   Fait tourner le programme d'anonymisation du décisionnel (F2_100_TRT_FICHIER) DEPUIS NOTRE ESPACE,
   sans rien écrire ni modifier dans le leur (\\S00SAS017\config$, \\serveurbidev\...).

   Principe
   - Leurs macros (F99_*, F0_*, MG_*, F1_*) restent chez eux : leur programme les %include depuis leur
     dossier à chaque lancement -> on utilise TOUJOURS leur dernière version.
   - Leur programme principal est RELU chez eux à chaque lancement et adapté EN MÉMOIRE (copie temporaire
     dans la WORK), avec 3 changements seulement :
       1. LIBNAME MADREF : nos écritures (REF_ANONYM, LISTE_TAB_ANONYM) vont dans NOTRE dossier ; les
          lectures trouvent d'abord nos tables, puis les leurs (bibliothèque concaténée, la 1re reçoit
          les écritures).
       2. Juste avant l'appel %F2_100_TRT_FICHIER : %include de nos surcharges (surcharges_decisionnel.sas),
          qui envoient les fichiers anonymisés dans NOTRE dossier de sortie.
       3. Le 1er « proc printto log="nul" » est neutralisé pour garder le log (VOIR_LOG=1).
   - Si leur programme a changé au point que ces repères n'existent plus, RIEN n'est lancé.
   ============================================================================================ */

/* ---------- 1. À RENSEIGNER ---------------------------------------------------------------- */

/* Programme principal du décisionnel, à l'endroit où ILS le maintiennent (lecture seule) */
/* -> vérifier le NOM EXACT du fichier dans 02_Pgm_Pilotes */
%let PGM_DECISIONNEL = \\S00SAS017\config$\Projets\DSDMAD\inf\01_Alim\02_Pgm_Pilotes\F2_100_TRT_FICHIER.sas;

/* Notre fichier de paramétrage (produit par generer_anonym_liste_fic.py) */
/* Chemins réseau (UNC) plutôt que M: : valables aussi si SAS tourne sur le serveur (M: = \\s00sas017\users$) */
%let chemANO_REF = \\s00sas017\users$\DSIN\Lot 1\cartographie;
%let nomREF      = Anonym_liste_fic_Satisfaction client.xlsx;

/* Nos dossiers (à créer avant le 1er lancement) */
%let REF_LOCAL  = \\s00sas017\users$\DSIN\Lot 1\Anonymisation\Referentiels;   /* REF_ANONYM, LISTE_TAB_ANONYM */
%let SORTIE_ANO = \\s00sas017\users$\DSIN\Lot 1\Anonymisation\Sorties;        /* fichiers et tables anonymisés */

/* Nos surcharges (fichier livré avec ce lanceur) */
%let SURCHARGES = \\s00sas017\users$\DSIN\Lot 1\Anonymisation\surcharges_decisionnel.sas;

%let VOIR_LOG = 1;    /* 1 = log visible (leur programme le masque), 0 = comportement d'origine */


/* ---------- 2. Contrôles avant lancement -------------------------------------------------- */
%global N_MADREF N_APPEL N_PRINTTO;

%macro _controles;
    %local ok;
    %let ok = 1;
    %if %sysfunc(fileexist(%superq(PGM_DECISIONNEL))) = 0 %then %do;
        %put ERROR: [DCT] Programme du décisionnel introuvable : %superq(PGM_DECISIONNEL);
        %let ok = 0;
    %end;
    %if %sysfunc(fileexist(%superq(chemANO_REF)\%superq(nomREF))) = 0 %then %do;
        %put ERROR: [DCT] Fichier de paramétrage introuvable : %superq(chemANO_REF)\%superq(nomREF);
        %let ok = 0;
    %end;
    %if %sysfunc(fileexist(%superq(REF_LOCAL))) = 0 %then %do;
        %put ERROR: [DCT] Dossier REF_LOCAL à créer : %superq(REF_LOCAL);
        %let ok = 0;
    %end;
    %if %sysfunc(fileexist(%superq(SORTIE_ANO))) = 0 %then %do;
        %put ERROR: [DCT] Dossier SORTIE_ANO à créer : %superq(SORTIE_ANO);
        %let ok = 0;
    %end;
    %if %sysfunc(fileexist(%superq(SURCHARGES))) = 0 %then %do;
        %put ERROR: [DCT] Fichier de surcharges introuvable : %superq(SURCHARGES);
        %let ok = 0;
    %end;
    %global CONTROLES_OK;
    %let CONTROLES_OK = &ok.;
%mend _controles;
%_controles;


/* ---------- 3. Copie adaptée de leur programme (en mémoire, WORK) ------------------------ */
filename _orig  "&PGM_DECISIONNEL.";
filename _adapte temp;

data _null_;
    length ligne $32767 chemin $1000;
    retain n_madref n_appel n_printto 0;
    infile _orig lrecl=32767 truncover end=fin;
    file _adapte lrecl=32767;
    input ligne $char32767.;

    /* 1. MADREF : nos écritures chez nous, lectures chez nous puis chez eux */
    if prxmatch('/^\s*libname\s+MADREF\s+"/i', ligne) then do;
        chemin = scan(ligne, 2, '"');
        ligne  = cats('libname MADREF ("', symget('REF_LOCAL'), '" "', chemin, '");',
                      '  /* [DCT] adapté : écritures dans REF_LOCAL */');
        n_madref + 1;
    end;

    /* 2. nos surcharges, juste avant l'appel du traitement */
    if prxmatch('/^\s*%F2_100_TRT_FICHIER\s*;/i', ligne) then do;
        put '%include "' "&SURCHARGES." '"; /* [DCT] surcharges */';
        n_appel + 1;
    end;

    /* 3. log visible : seulement le tout premier printto "nul" (en tête de programme) */
    if symget('VOIR_LOG') = '1' and n_printto = 0
       and prxmatch('/^\s*proc\s+printto\s+log\s*=\s*"nul"\s*;\s*run\s*;/i', ligne) then do;
        ligne = '/* [DCT] désactivé pour garder le log : proc printto log="nul"; run; */';
        n_printto + 1;
    end;

    /* ... et le « options nonotes; » qui le suit */
    if symget('VOIR_LOG') = '1' and n_printto = 1 and prxmatch('/^\s*options\s+nonotes\s*;\s*$/i', ligne) then do;
        ligne = '/* [DCT] désactivé pour garder le log : options nonotes; */';
        n_printto + 1;
    end;

    lg = lengthn(ligne);
    if lg = 0 then put;
    else put ligne $varying32767. lg;

    if fin then do;
        call symputx('N_MADREF', n_madref, 'G');
        call symputx('N_APPEL', n_appel, 'G');
        call symputx('N_PRINTTO', n_printto, 'G');
    end;
run;


/* ---------- 4. Lancement ----------------------------------------------------------------- */
%macro _lancer;
    %if &CONTROLES_OK. ne 1 %then %do;
        %put ERROR: [DCT] Contrôles en échec (voir ci-dessus) : rien n%str(%')est lancé.;
        %return;
    %end;
    %if &N_MADREF. ne 1 or &N_APPEL. ne 1 %then %do;
        %put ERROR: [DCT] Le programme du décisionnel a changé : repères attendus introuvables;
        %put ERROR: [DCT]   libname MADREF trouvé &N_MADREF. fois (attendu 1), appel F2_100_TRT_FICHIER &N_APPEL. fois (attendu 1);
        %put ERROR: [DCT] Rien n%str(%')est lancé : relire leur programme et adapter ce lanceur.;
        %return;
    %end;
    %put NOTE: [DCT] Programme du décisionnel relu : %superq(PGM_DECISIONNEL);
    %put NOTE: [DCT] Paramétrage : %superq(chemANO_REF)\%superq(nomREF);
    %put NOTE: [DCT] Référentiels écrits dans : %superq(REF_LOCAL);
    %put NOTE: [DCT] Sorties anonymisées dans : %superq(SORTIE_ANO);
    %include _adapte;
%mend _lancer;
%_lancer;

filename _orig clear;

/* Bilan : WORK.SUIVI (statut par fichier) et WORK.SUIVI_DCP_REVIEW (colonnes à revoir) */
