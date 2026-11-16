/* ------------------------------------------------------------------------------ */
/* NOM DU PROGRAMME : LANCEUR_SUVI_IRSI.SAS									      */
/*																				  */
/* AUTEUR			: CM														  */
/*																				  */
/* DATE				: 11/06/2018												  */
/*																				  */
/* ------------------------------------------------------------------------------ */
/* DESCRIPTION:																	  */
/* Afin de suivre l'impact de la convention IRSI qui s'applique sur les sinistres */
/* en habitation (NH, HA,MR),en PRO (MP) et en AGRI (MA) avec la garantie DDE ou  */
/* INC (hors ELEC et INC sinistrées simultanément), nous consruisons un datamart. */
/* Ce dernier contient le périmètre suivant :									  */
/* 			- Sinistres survenus depuis 01/06/2018								  */
/*			- hors les sinistres annulés										  */
/*			- avec la garantie DDE et/ou INC non annulé							  */
/*			- pour les produits NH, HA, MR, MP, MA								  */
/*			- Aucun sinistre DDE ou INC ne sera ouvert en prédéclaration		  */
/* ------------------------------------------------------------------------------ */
/* Evolution :																	  */
/* FEV 2026 |		| - Ajout de la macro-variable annee pour se baser sur la 	  */
/* 						bonne année de vision pour l'enquête.					  */
/*						Exemple en 2026 : on observe jusqu'au 31/12/2025. Donc	  */
/*						%let annee = 2025;										  */
/*						%let fichier = 1225;									  */
/* ------------------------------------------------------------------------------ */


/* -------------------------------------------------------------------------------- */
/* ETAPE 0 : Macro-variables utiles pour la chaine de production				    */
/* -------------------------------------------------------------------------------- */

/******************************* Paramètre à modifier *******************************/

/* Macro-variables pour dater les extractions */
/*%let semaine = %eval(%sysfunc(week(%sysfunc(TODAY())))+1);*/


%let an0	= %sysfunc(substr(&annee.,3));

%let annee1 = %sysevalf(&annee. - 1);
%let an1	= %sysfunc(substr(&annee1.,3));

%let annee2 = %sysevalf(&annee. - 2);
%let an2	= %sysfunc(substr(&annee2.,3));
%let annee3 = %sysevalf(&annee. - 3);
%let an3	= %sysfunc(substr(&annee3.,3));

%let annee4 = %sysevalf(&annee. - 4);
%let an4	= %sysfunc(substr(&annee4.,3));

/* utiliser pour l'ensemble des programmes */ 

%LET annee_vis=25;  
%LET mois_vis=12;

/* Librairie de Stockage des tables intermédiaires pour construire les datamarts */
libname temp "\\bureautique\bureautique\DSIN\POLORGA\EP\05-Demandes-Etudes\02-Cellule Technique\FFA\Enquêtes FFA IRSI\Tables\&annee.\Temporaire";

/* Librairie de Stockage du Datamart IRSI */
libname tables "\\bureautique\bureautique\DSIN\POLORGA\EP\05-Demandes-Etudes\02-Cellule Technique\FFA\Enquêtes FFA IRSI\Tables\&annee.";

/* Librairie de Stockage des réfentiels pour les données DARVA et autres */
libname REF "\\serveurinf\Users$\DSIN\P_Perf\01_SUIVI\02_FREQUENCE_MENSUELLE\19_IRSI\Referentiels";

libname temp2 "\\serveurinf\Users$\DSIN\DSIN_DPART\IRSI";



