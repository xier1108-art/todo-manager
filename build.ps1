# 소스(app.py, web/) 수정 후 exe를 다시 만들 때 build.bat을 더블클릭하세요 (이 파일을 실행합니다).
# 결과물: dist\할일정리\할일정리.exe (+ _internal 폴더)
#
# PyInstaller에 한글 이름을 직접 넘기면 cmd -> PowerShell -> PyInstaller로 이어지는
# 콘솔 코드페이지 변환 과정에서 깨지는 경우가 있어, 영문 이름(TodoManager)으로 빌드한 뒤
# 폴더/파일명을 한글로 바꾸는 방식을 씁니다.
# 또한 PyInstaller는 dist\할일정리 폴더를 통째로 지웠다가 새로 만들기 때문에,
# 안에 있던 data.json(그룹/상태/메모 등), token.json(구글 캘린더 로그인),
# webview_data(글꼴 설정 등 localStorage가 담긴 브라우저 프로필)를
# 빌드 전후로 자동 백업·복원합니다.

Set-Location $PSScriptRoot

$koreanName = "할일정리"
$buildName = "TodoManager"
$distKorean = "dist\$koreanName"
$distBuild = "dist\$buildName"
$backupFiles = @("data.json", "token.json")
$backupDirs = @("webview_data")
$backupDir = Join-Path $env:TEMP "할일정리_backup"

if (Test-Path $backupDir) { Remove-Item $backupDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
foreach ($f in $backupFiles) {
    if (Test-Path "$distKorean\$f") {
        Copy-Item "$distKorean\$f" (Join-Path $backupDir $f) -Force
    }
}
foreach ($d in $backupDirs) {
    if (Test-Path "$distKorean\$d") {
        Copy-Item "$distKorean\$d" (Join-Path $backupDir $d) -Recurse -Force
    }
}

if (Test-Path $distBuild) { Remove-Item $distBuild -Recurse -Force }
if (Test-Path $distKorean) { Remove-Item $distKorean -Recurse -Force }

pyinstaller --name $buildName --windowed --noconfirm --add-data "web;web" --add-data "icon.ico;." --icon "icon.ico" app.py

if (-not (Test-Path "$distBuild\$buildName.exe")) {
    Write-Host "빌드에 실패했습니다. 위 로그를 확인하세요." -ForegroundColor Red
    exit 1
}

Rename-Item "$distBuild\$buildName.exe" "$koreanName.exe"

# 방금 만든 파일을 백신 등이 잠깐 잠글 수 있어 폴더 이름 변경은 몇 번 재시도한다.
$renamed = $false
for ($i = 0; $i -lt 5; $i++) {
    try {
        Rename-Item $distBuild $koreanName -ErrorAction Stop
        $renamed = $true
        break
    } catch {
        Start-Sleep -Milliseconds 800
    }
}

if (-not $renamed) {
    Write-Host "폴더 이름 변경(dist\$buildName -> dist\$koreanName)에 실패했습니다. dist\$buildName 폴더를 확인하세요." -ForegroundColor Red
    exit 1
}

foreach ($f in $backupFiles) {
    $src = Join-Path $backupDir $f
    if (Test-Path $src) {
        Copy-Item $src "$distKorean\$f" -Force
        Write-Host "기존 $f 를 복원했습니다."
    }
}
foreach ($d in $backupDirs) {
    $src = Join-Path $backupDir $d
    if (Test-Path $src) {
        Copy-Item $src "$distKorean\$d" -Recurse -Force
        Write-Host "기존 $d 를 복원했습니다."
    }
}

Write-Host "빌드 완료: $distKorean\$koreanName.exe"
