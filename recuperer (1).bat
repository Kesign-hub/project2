@echo off
setlocal
REM ===========================================================================
REM  RECUPERER : copie les outils du partage d'equipe dans CE dossier local.
REM  Premiere fois : copier ce fichier dans un dossier vide de son poste
REM  (ex. C:\Users\<moi>\outils_migration), puis double-clic.
REM  N'ecrase jamais un fichier local plus recent ; ne supprime rien.
REM  "recuperer.bat test" = simulation.
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

robocopy "%PARTAGE%" "%LOCAL%" /E /XO /FFT /R:1 /W:2 /NP /NDL %SIMU% ^
  /XD venv .venv __pycache__ .git /XF *.pyc publication_*.log /TEE

if %ERRORLEVEL% GEQ 8 (echo [ERREUR] Recuperation en echec. & goto fin)
echo.
if defined SIMU (echo SIMULATION : rien n'a ete copie.) else (echo Recuperation terminee dans %~dp0)
if not exist "%~dp0venv\Scripts\python.exe" (
  echo.
  echo Premiere installation : lancer installer_environnement.bat
)
:fin
endlocal
pause
