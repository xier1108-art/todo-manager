# 소스(app.py, web/) 수정 후 exe를 다시 만들 때 build.bat을 더블클릭하세요 (이 파일을 실행합니다).
# 결과물: dist\할일정리\할일정리.exe (+ _internal 폴더)
#
# PyInstaller에 한글 이름을 직접 넘기면 cmd -> PowerShell -> PyInstaller로 이어지는
# 콘솔 코드페이지 변환 과정에서 깨지는 경우가 있어, 영문 이름(TodoManager)으로 빌드한 뒤
# exe 이름을 한글로 바꾸는 방식을 씁니다.
#
# dist\할일정리 폴더는 통째로 지우지 않고 프로그램 파일(할일정리.exe, _internal)만 바꿔 끼웁니다.
# data.json·token.json, 자동화가 만든 백업 파일 등 사용자 파일은 전혀 건드리지 않습니다.
# (예전에는 폴더를 지웠다 되살렸는데, 다른 프로그램이 data.json을 열고 있으면 삭제가
#  반쯤 걸려 데이터가 사라질 뻔했고, 목록에 없는 백업 파일은 그냥 지워졌음)

Set-Location $PSScriptRoot

$koreanName = "할일정리"
$buildName = "TodoManager"
$distKorean = "dist\$koreanName"
$distBuild = "dist\$buildName"

if (Test-Path $distBuild) { Remove-Item $distBuild -Recurse -Force }

pyinstaller --name $buildName --windowed --noconfirm --add-data "web;web" --add-data "icon.ico;." --icon "icon.ico" app.py

if (-not (Test-Path "$distBuild\$buildName.exe")) {
    Write-Host "빌드에 실패했습니다. 위 로그를 확인하세요." -ForegroundColor Red
    exit 1
}

New-Item -ItemType Directory -Force -Path $distKorean | Out-Null

# 실행 중인 앱이 _internal 파일을 잡고 있으면 지워지지 않는다. 백신 등의 잠깐 잠금도 있어 몇 번 재시도한다.
if (Test-Path "$distKorean\_internal") {
    $removed = $false
    for ($i = 0; $i -lt 5; $i++) {
        try {
            Remove-Item "$distKorean\_internal" -Recurse -Force -ErrorAction Stop
            $removed = $true
            break
        } catch {
            Start-Sleep -Milliseconds 800
        }
    }
    if (-not $removed) {
        Write-Host "$distKorean\_internal 을 바꾸지 못했습니다. 할일정리가 켜져 있으면 트레이에서 '완전 종료' 후 다시 실행하세요. (data.json 등 데이터는 건드리지 않았습니다)" -ForegroundColor Red
        exit 1
    }
}

Move-Item "$distBuild\_internal" "$distKorean\_internal"
Copy-Item "$distBuild\$buildName.exe" "$distKorean\$koreanName.exe" -Force
Remove-Item $distBuild -Recurse -Force

Write-Host "빌드 완료: $distKorean\$koreanName.exe"
