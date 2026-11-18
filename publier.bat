@echo off
setlocal
REM ===========================================================================
REM  PUBLIER : copie les outils de CE dossier local vers le partage d'equipe.
REM  Double-clic ou .\publier.bat depuis PowerShell. "publier.bat test" = simulation.
REM  - Seulement ce qui a change ; ne supprime rien sur le partage.
REM  - N'ecrase jamais un fichier plus recent sur le partage.
REM  - N'envoie jamais : venv, referentiels (EFFECTIFS...), donnees, resultats,
REM    rapports de desensibilisation (valeurs reelles).
REM ===========================================================================
set "LOCAL=%~dp0."
REM Chemin du partage. Le dossier "04-Projets equipe" est retrouve par un joker
REM (04-Projets*) : on evite ainsi tout probleme d'accent dans le script.
set "BASE=O:\DSIN\POLORGA\EP"
set "PROJETS="
for /d %%D in ("%BASE%\04-Projets*") do set "PROJETS=%%~fD"
if not defined PROJETS (
  echo [ERREUR] Dossier introuvable : %BASE%\04-Projets...
  echo          Le lecteur O: est-il bien connecte ? Ouvre O:\ dans l'explorateur puis relance.
  goto fin
)
set "PARTAGE=%PROJETS%\03-Transverse\33-Mission SAS\9-Outils migration"
if not exist "%PARTAGE%\" (
  echo [ERREUR] Dossier cible introuvable :
  echo          %PARTAGE%
  goto fin
)
echo Partage : %PARTAGE%

set "SIMU="
if /I "%~1"=="test" set "SIMU=/L"
for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmm"') do set "HORO=%%d"
set "JOURNAL=%~dp0publication_%HORO%.log"

robocopy "%LOCAL%" "%PARTAGE%" /E /XO /FFT /R:1 /W:2 /NP /NDL %SIMU% ^
  /XD venv .venv __pycache__ .git .vscode .pytest_cache _referentiels _donnees_locales _a_classer ^
      _mises_a_jour _correctifs agent_sessions references_sas SORTIES_PY _echange sauvegarde_tri ^
  /XF *.pyc *.log journal_migration.tsv migrer_organisation.py EFFECTIFS*.csv cartographie_c4*.csv ^
      ref_anonym*.csv *_rapport_desensibilisation.txt *.sas7bdat *.parquet *.pkl *.sqlite ~$* ^
  /LOG:"%JOURNAL%" /TEE

if %ERRORLEVEL% GEQ 8 (echo [ERREUR] Publication en echec : voir %JOURNAL% & goto fin)
echo.
if defined SIMU (
  echo SIMULATION : rien n'a ete copie. Pour publier vraiment : publier.bat sans "test".
) else (
  echo Publication terminee. Le dossier du partage va s'ouvrir.
  start "" explorer "%PARTAGE%"
)
echo Journal : %JOURNAL%
:fin
endlocal
pause
