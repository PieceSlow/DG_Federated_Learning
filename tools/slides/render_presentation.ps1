$ErrorActionPreference = 'Stop'
$source = (Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.pptx' | Select-Object -First 1).FullName
$target = Join-Path $PSScriptRoot 'ppt_preview'
New-Item -ItemType Directory -Force -Path $target | Out-Null
$app = New-Object -ComObject PowerPoint.Application
$app.Visible = -1
try {
    $presentation = $app.Presentations.Open($source, $true, $false, $false)
    try {
        for ($i = 1; $i -le $presentation.Slides.Count; $i++) {
            $file = Join-Path $target ('slide_{0:D2}.png' -f $i)
            $presentation.Slides.Item($i).Export($file, 'PNG', 1600, 900)
            Write-Output $file
        }
    } finally {
        $presentation.Close()
    }
} finally {
    $app.Quit()
}
