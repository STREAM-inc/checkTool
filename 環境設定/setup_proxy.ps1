<#
  社内プロキシの設定を自動で行う（Backlog・GitHub・pip に繋がるようにする）

  やること
    1. プロキシのID・パスワードを聞く（パスワードは画面に出ない）
    2. そのID・パスワードで Backlog と GitHub に繋がるか試す（間違っていたら聞き直す）
    3. 繋がったら、自分のユーザーの環境変数に保存する
         HTTP_PROXY / HTTPS_PROXY / http_proxy / https_proxy = http://ID:パスワード@proxy:8080
         NO_PROXY / no_proxy = localhost,127.0.0.1,::1,.local
    4. git のプロキシ（git config --global http.proxy / https.proxy）も同じにする
    5. Windows のプロキシ設定（Edge/Chrome 用）は確認だけ（変えない）

  使い方
    プロキシ設定.bat をダブルクリック
    （確認だけしたいとき: powershell -ExecutionPolicy Bypass -File setup_proxy.ps1 -CheckOnly）

  ※ パスワードは環境変数に平文で入ります（今までの手動設定と同じ）。パスワードを変えたら、もう一度実行してください。
  ※ 管理者権限は不要です（自分のユーザーの設定だけを変えます）。
#>
param(
    [string]$ProxyHost = "proxy",
    [int]$ProxyPort = 8080,
    [string]$NoProxy = "localhost,127.0.0.1,::1,.local",
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$TestUrls = @(
    @{ Name = "Backlog"; Url = "https://streeeeeam.backlog.com/" },
    @{ Name = "GitHub";  Url = "https://github.com/" }
)
$ProxyVars = @("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")

function Write-Step($msg) { Write-Host ""; Write-Host "== $msg ==" -ForegroundColor Cyan }
function Hide-Password([string]$url) { if ($url) { $url -replace '://([^:/@]+):[^@]*@', '://$1:****@' } else { "(未設定)" } }

# 指定のプロキシ(ID・パスワード付き)で URL に繋がるか。繋がれば $null、だめなら理由を返す
function Test-ProxyAccess([string]$Url, [string]$User, [string]$Password) {
    $req = [Net.HttpWebRequest]::Create($Url)
    $req.Method = "HEAD"; $req.Timeout = 15000; $req.AllowAutoRedirect = $false
    $proxy = New-Object Net.WebProxy("http://${ProxyHost}:${ProxyPort}")
    if ($User) { $proxy.Credentials = New-Object Net.NetworkCredential($User, $Password) }
    $req.Proxy = $proxy
    try { $res = $req.GetResponse(); $res.Close(); return $null }
    catch [Net.WebException] {
        $r = $_.Exception.Response
        if ($r) {
            $code = [int]$r.StatusCode; $r.Close()
            if ($code -eq 407) { return "プロキシのID・パスワードが違います（407）" }
            return $null  # 401/403/404 などはサイトまで届いている＝プロキシはOK
        }
        return $_.Exception.Message
    }
}

# 今の設定の表示
Write-Step "今の設定"
foreach ($n in $ProxyVars + @("NO_PROXY")) {
    "{0,-12} {1}" -f $n, (Hide-Password ([Environment]::GetEnvironmentVariable($n, "User")))
}
$git = Get-Command git -ErrorAction SilentlyContinue
if ($git) {
    "git http.proxy  " + (Hide-Password (git config --global --get http.proxy))
    "git https.proxy " + (Hide-Password (git config --global --get https.proxy))
} else { "git は入っていません（GitHub を使わないなら問題ありません）" }
$ie = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'
if ($ie.ProxyEnable -eq 1) { "Windows のプロキシ: 有効（$($ie.ProxyServer)）" }
else { Write-Host "Windows のプロキシ: 無効 … Edge/Chrome が社外に繋がらない場合は情報システム担当に確認してください" -ForegroundColor Yellow }

if ($CheckOnly) {
    Write-Step "接続の確認（今の環境変数の設定で）"
    $cur = [Environment]::GetEnvironmentVariable("HTTPS_PROXY", "User")
    $u = $null; $p = $null
    if ($cur -match '://([^:/@]+):([^@]*)@') { $u = [Uri]::UnescapeDataString($Matches[1]); $p = [Uri]::UnescapeDataString($Matches[2]) }
    foreach ($t in $TestUrls) {
        $err = Test-ProxyAccess $t.Url $u $p
        if ($err) { Write-Host ("NG  {0}: {1}" -f $t.Name, $err) -ForegroundColor Red } else { Write-Host ("OK  {0}" -f $t.Name) -ForegroundColor Green }
    }
    exit 0
}

# ID・パスワードを聞いて、繋がるまで(3回まで)試す
Write-Step "プロキシのID・パスワード"
$defaultUser = $env:USERNAME
$user = Read-Host "プロキシのID（そのまま Enter で $defaultUser）"
if (-not $user) { $user = $defaultUser }
$password = $null
for ($i = 1; $i -le 3; $i++) {
    $sec = Read-Host "パスワード（画面には出ません）" -AsSecureString
    $password = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
    if (-not $password) { Write-Host "パスワードが空です" -ForegroundColor Yellow; continue }
    Write-Host "Backlog / GitHub に繋がるか確認しています…"
    $errs = @()
    foreach ($t in $TestUrls) {
        $err = Test-ProxyAccess $t.Url $user $password
        if ($err) { $errs += ("{0}: {1}" -f $t.Name, $err) } else { Write-Host ("  OK  {0}" -f $t.Name) -ForegroundColor Green }
    }
    if (-not $errs) { break }
    $errs | ForEach-Object { Write-Host "  NG  $_" -ForegroundColor Red }
    if ($errs -match "407") {
        if ($i -lt 3) { Write-Host "もう一度パスワードを入れてください（$i/3）" -ForegroundColor Yellow; $password = $null; continue }
        Write-Host "3回失敗したので、何も変えずに終わります。" -ForegroundColor Red; exit 1
    }
    # 407 以外(社内ネットワークに繋がっていない等)は、設定だけ保存するか聞く
    $ans = Read-Host "繋がりませんでしたが、この設定で保存しますか？ (y/N)"
    if ($ans -ne "y") { Write-Host "何も変えずに終わります。"; exit 1 }
    break
}
if (-not $password) { Write-Host "何も変えずに終わります。"; exit 1 }

# パスワードに記号があってもURLとして正しくなるようにエンコードする
$proxyUrl = "http://{0}:{1}@{2}:{3}" -f [Uri]::EscapeDataString($user), [Uri]::EscapeDataString($password), $ProxyHost, $ProxyPort

Write-Step "保存"
foreach ($n in $ProxyVars) { [Environment]::SetEnvironmentVariable($n, $proxyUrl, "User") }
foreach ($n in @("NO_PROXY", "no_proxy")) { [Environment]::SetEnvironmentVariable($n, $NoProxy, "User") }
Write-Host ("環境変数: HTTP_PROXY / HTTPS_PROXY / http_proxy / https_proxy = {0}" -f (Hide-Password $proxyUrl))
Write-Host ("環境変数: NO_PROXY / no_proxy = {0}" -f $NoProxy)
if ($git) {
    git config --global http.proxy $proxyUrl
    git config --global https.proxy $proxyUrl
    Write-Host ("git: http.proxy / https.proxy = {0}" -f (Hide-Password $proxyUrl))
}

# Python(目検ツールが Backlog に繋ぐのと同じ方法)でも確認
$py = Get-Command py -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command python -ErrorAction SilentlyContinue }
if ($py) {
    Write-Step "Python からの確認（目検ツールと同じ繋ぎ方）"
    $env:HTTPS_PROXY = $proxyUrl; $env:HTTP_PROXY = $proxyUrl; $env:NO_PROXY = $NoProxy
    $code = @'
import urllib.request, urllib.error
for name, url in [("Backlog", "https://streeeeeam.backlog.com/"), ("GitHub", "https://github.com/")]:
    try:
        urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=15)
        print("  OK ", name)
    except urllib.error.HTTPError as e:
        print("  NG " if e.code == 407 else "  OK ", name, "(HTTP %d)" % e.code)
    except Exception as e:
        print("  NG ", name, e)
'@
    # PowerShell 5.1 は外のプログラムに渡す文字列の " を消してしまうので、-c ではなく一時ファイルにして実行する
    $tmp = Join-Path $env:TEMP ("proxy_check_{0}.py" -f [Guid]::NewGuid().ToString("N"))
    [IO.File]::WriteAllText($tmp, $code, (New-Object Text.UTF8Encoding($false)))
    try { & $py.Source $tmp } finally { Remove-Item $tmp -ErrorAction SilentlyContinue }
}

Write-Host ""
Write-Host "完了しました。開いている 黒い画面・目検ツール・エディタ などは一度閉じてから開き直してください（新しい設定はそこから効きます）。" -ForegroundColor Green
