<#
.SYNOPSIS
  Prépare ou vérifie la machine de démonstration Rappel (Windows).

.DESCRIPTION
  Par défaut : VÉRIFICATION seulement (aucune installation, aucune modification) :
  RAM >= 16 Go, disque >= 15 Go libres, Docker (démon actif), Python 3.11 ou 3.12, git.
  Puis affiche les étapes Zetaris et Postgres à faire à la main.

  -Preparer : crée .venv, installe requirements-demo.txt, copie .env.example vers .env
  s'il n'existe pas, et démarre Postgres (docker compose up -d postgres).
  Aucune clé n'est écrite ni affichée : les renseigner dans .env à la main.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\preparer_machine_demo.ps1
  powershell -ExecutionPolicy Bypass -File scripts\preparer_machine_demo.ps1 -Preparer
  powershell -ExecutionPolicy Bypass -File scripts\preparer_machine_demo.ps1 -Python C:\chemin\python.exe
#>
param([switch]$Preparer, [string]$Python = "")

$ErrorActionPreference = "Stop"
$Racine = Split-Path -Parent $PSScriptRoot
$Echecs = @()

function Resultat($Case, $Ok, $Detail) {
    $Statut = if ($Ok) { "REUSSI" } else { "ECHOUE" }
    if (-not $Ok) { $script:Echecs += $Case }
    Write-Output ("{0,-8} | {1,-7} | {2}" -f $Case, $Statut, $Detail)
}

Write-Output "Vérification de la machine de démonstration ($Racine)"
Write-Output "Case     | Statut  | Détail"

$RamGo = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
# Tolérance de 0,5 Go : une machine de 16 Go annonce souvent 15,7 Go utilisables.
Resultat "RAM" ($RamGo -ge 15.5) "$RamGo Go (minimum 16 Go, tolérance 0,5 Go)"

$Lecteur = (Get-Item $Racine).PSDrive
$LibreGo = [math]::Round($Lecteur.Free / 1GB, 1)
Resultat "Disque" ($LibreGo -ge 15) "$LibreGo Go libres sur $($Lecteur.Name): (minimum 15 Go)"

$Docker = Get-Command docker -ErrorAction SilentlyContinue
if ($Docker) {
    $Version = & docker info --format "{{.ServerVersion}}" 2>$null
    Resultat "Docker" ($LASTEXITCODE -eq 0) $(if ($LASTEXITCODE -eq 0) { "démon $Version" } else { "installé mais démon arrêté (lancer Docker Desktop)" })
} else {
    Resultat "Docker" $false "introuvable (installer Docker Desktop)"
}

# Candidats : -Python, .venv du dépôt, tous les python du PATH, lanceur py (3.12 puis 3.11).
$Candidats = @()
if ($Python) { $Candidats += $Python }
$Candidats += Join-Path $Racine ".venv\Scripts\python.exe"
$Candidats += @(Get-Command python -All -ErrorAction SilentlyContinue | ForEach-Object { $_.Source })
if (Get-Command py -ErrorAction SilentlyContinue) {
    foreach ($Version in @("-3.12", "-3.11")) {
        $Chemin = & py $Version -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $Chemin) { $Candidats += $Chemin }
    }
}
$PythonRetenu = $null
foreach ($Candidat in $Candidats) {
    if (-not $Candidat -or -not (Test-Path $Candidat)) { continue }
    $Sortie = & $Candidat -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    if ($LASTEXITCODE -eq 0 -and $Sortie -in @("3.11", "3.12")) { $PythonRetenu = $Candidat; break }
}
Resultat "Python" ($null -ne $PythonRetenu) $(if ($PythonRetenu) { "$PythonRetenu (3.11 ou 3.12)" } else { "Python 3.11 ou 3.12 introuvable (option -Python)" })

$Git = Get-Command git -ErrorAction SilentlyContinue
Resultat "git" ($null -ne $Git) $(if ($Git) { "trouvé" } else { "introuvable (ajouter C:\Program Files\Git\cmd au PATH)" })

if ($Preparer) {
    if ($Echecs.Count -gt 0) {
        Write-Output "Préparation annulée : corriger d'abord $($Echecs -join ', ')."
        exit 1
    }
    Push-Location $Racine
    try {
        if (-not (Test-Path ".venv")) { & $PythonRetenu -m venv .venv }
        & .\.venv\Scripts\python.exe -m pip install -r requirements-demo.txt
        if (-not (Test-Path ".env")) {
            Copy-Item .env.example .env
            Write-Output ".env créé depuis .env.example : renseigner les clés à la main (jamais dans git)."
        }
        & docker compose up -d postgres
    } finally {
        Pop-Location
    }
}

Write-Output ""
Write-Output "Étapes manuelles (aucune clé affichée ici) :"
Write-Output "  Postgres : docker compose up -d postgres ; dans .env : MEMOIRE_PROVIDER=postgres"
Write-Output "             puis : python scripts\check_postgres.py"
Write-Output "  Zetaris  : démarrer l'instance fournie par le coéquipier ; dans .env : SOURCE_PROVIDER=zetaris,"
Write-Output "             ZETARIS_BASE_URL, ZETARIS_API_KEY, ZETARIS_ORG_ID ; aligner les tables (docs\run_sql.md)"
Write-Output "  Meterless: dans .env : LLM_PROVIDER=meterless, METERLESS_RUNTIME_TOKEN,"
Write-Output "             METERLESS_MODEL_RAPIDE, METERLESS_MODEL_PROFOND (valeurs de la console)"
Write-Output "  Contrôle : python scripts\verifier_machine_demo.py --confirmer"

if ($Echecs.Count -gt 0) {
    Write-Output "Bilan : ECHOUE sur $($Echecs -join ', ')"
    exit 1
}
Write-Output "Bilan : aucun ECHOUE"
exit 0
