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

# 백업 폴더는 절대 통째로 지우지 않는다 (지난 빌드가 도중에 실패해 dist\할일정리 쪽
# 원본이 이미 없어진 상태에서 여기까지 비워버리면 유일한 사본을 잃는다).
# 원본이 있을 때만 백업을 "갱신"하고, 원본이 없으면 예전 백업을 그대로 둔다.
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
foreach ($f in $backupFiles) {
    if (Test-Path "$distKorean\$f") {
        Copy-Item "$distKorean\$f" (Join-Path $backupDir $f) -Force
    }
}
foreach ($d in $backupDirs) {
    if (Test-Path "$distKorean\$d") {
        if (Test-Path (Join-Path $backupDir $d)) { Remove-Item (Join-Path $backupDir $d) -Recurse -Force }
        Copy-Item "$distKorean\$d" (Join-Path $backupDir $d) -Recurse -Force
    }
}

if (Test-Path $distBuild) { Remove-Item $distBuild -Recurse -Force }

# 백신 등이 파일을 잠깐 잠그면 Remove-Item이 폴더 일부만 지우고 실패할 수 있다.
# 그 상태로 계속 진행하면 data.json 등 원본이 없어진 반쪽짜리 폴더가 남으므로,
# 여기서 확실히 못 지우면 즉시 중단한다 (백업은 이미 위에서 떠 두었으니 안전).
if (Test-Path $distKorean) {
    $removed = $false
    for ($i = 0; $i -lt 5; $i++) {
        try {
            Remove-Item $distKorean -Recurse -Force -ErrorAction Stop
            $removed = $true
            break
        } catch {
            Start-Sleep -Milliseconds 800
        }
    }
    if (-not $removed) {
        Write-Host "dist\$koreanName 폴더를 지우지 못했습니다 (파일이 잠겨 있을 수 있음). data.json 등은 $backupDir 에 안전하게 백업되어 있습니다." -ForegroundColor Red
        exit 1
    }
}

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
