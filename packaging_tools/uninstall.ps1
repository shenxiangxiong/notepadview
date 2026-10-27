param([switch]$Silent, [switch]$NoRegister)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)

function Get-AssociationValue([Microsoft.Win32.RegistryKey]$Classes, [string]$Path, [string]$Name) {
    $key = $Classes.OpenSubKey($Path)
    if ($null -eq $key) { return $null }
    try {
        return $key.GetValue($Name, $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
    } finally { $key.Dispose() }
}

function Remove-AssociationValue([Microsoft.Win32.RegistryKey]$Classes, [string]$Path,
                                 [string]$Name, [string]$Expected) {
    $key = $Classes.OpenSubKey($Path, $true)
    if ($null -eq $key) { return $false }
    try {
        $actual = $key.GetValue($Name, $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
        if ($actual -is [string] -and $key.GetValueKind($Name) -eq [Microsoft.Win32.RegistryValueKind]::String -and
            [string]::Equals($actual, $Expected, [StringComparison]::OrdinalIgnoreCase)) {
            $key.DeleteValue($Name)
            return $true
        }
        return $false
    } finally { $key.Dispose() }
}

function Remove-EmptyAssociationKey([Microsoft.Win32.RegistryKey]$Classes, [string]$Path) {
    $key = $Classes.OpenSubKey($Path)
    if ($null -eq $key) { return }
    try { $empty = $key.ValueCount -eq 0 -and $key.SubKeyCount -eq 0 }
    finally { $key.Dispose() }
    if ($empty) { $Classes.DeleteSubKey($Path, $false) }
}

function Remove-FileAssociations([string]$InstallRoot, [Microsoft.Win32.RegistryKey]$Classes) {
    $command = '"' + (Join-Path $InstallRoot 'NotepadView.exe') + '" "%1"'
    $extensions = @('.md', '.markdown', '.txt', '.json', '.xml', '.sql')
    $application = 'Applications\NotepadView.exe'
    $progId = 'NotepadView.Document'
    $changed = $false
    foreach ($path in @($application, $progId)) {
        $actual = Get-AssociationValue $Classes ($path + '\shell\open\command') ''
        if ($actual -isnot [string] -or
            -not [string]::Equals($actual, $command, [StringComparison]::OrdinalIgnoreCase)) { continue }
        if ($path -eq $application) {
            foreach ($extension in $extensions) {
                if (Remove-AssociationValue $Classes ($path + '\SupportedTypes') $extension '') { $changed = $true }
            }
            if (Remove-AssociationValue $Classes $path 'FriendlyAppName' 'Notepad View') { $changed = $true }
            Remove-EmptyAssociationKey $Classes ($path + '\SupportedTypes')
        } else {
            foreach ($extension in $extensions) {
                $openWith = $extension + '\OpenWithProgids'
                if (Remove-AssociationValue $Classes $openWith $progId '') { $changed = $true }
                Remove-EmptyAssociationKey $Classes $openWith
            }
            if (Remove-AssociationValue $Classes $path '' 'Notepad View 文档') { $changed = $true }
        }
        if (Remove-AssociationValue $Classes ($path + '\shell\open\command') '' $command) { $changed = $true }
        foreach ($suffix in @('\shell\open\command', '\shell\open', '\shell', '')) {
            Remove-EmptyAssociationKey $Classes ($path + $suffix)
        }
    }
    $contextMenu = '*\shell\NotepadView.Open'
    $contextCommand = '"' + (Join-Path $InstallRoot 'NotepadView.exe') + '" "%1"'
    $actualContextCommand = Get-AssociationValue $Classes ($contextMenu + '\command') ''
    if ($actualContextCommand -is [string] -and
        [string]::Equals($actualContextCommand, $contextCommand, [StringComparison]::OrdinalIgnoreCase)) {
        if (Remove-AssociationValue $Classes ($contextMenu + '\command') '' $contextCommand) {
            $changed = $true
        }
        if (Remove-AssociationValue $Classes $contextMenu 'Icon' ((Join-Path $InstallRoot 'NotepadView.exe') + ',0')) {
            $changed = $true
        }
        if (Remove-AssociationValue $Classes $contextMenu '' '用 NotepadView 打开') { $changed = $true }
        foreach ($suffix in @('\command', '')) {
            Remove-EmptyAssociationKey $Classes ($contextMenu + $suffix)
        }
    }
    return $changed
}

function Send-AssociationChangeNotification {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class NotepadViewAssociationShell {
    [DllImport("shell32.dll")]
    public static extern void SHChangeNotify(uint eventId, uint flags, IntPtr item1, IntPtr item2);
}
'@
    [NotepadViewAssociationShell]::SHChangeNotify(0x08000000, 0, [IntPtr]::Zero, [IntPtr]::Zero)
}

function Show-UninstallDialog([string]$Message, [switch]$Confirm) {
    Add-Type -AssemblyName System.Windows.Forms
    $form = New-Object Windows.Forms.Form
    try {
        $form.Text = '卸载 Notepad View'
        $form.ClientSize = New-Object Drawing.Size(480, 170)
        $form.FormBorderStyle = 'FixedDialog'
        $form.StartPosition = 'CenterScreen'
        $form.MinimizeBox = $false
        $form.MaximizeBox = $false
        $form.Font = New-Object Drawing.Font('Microsoft YaHei UI', 10)
        $label = New-Object Windows.Forms.Label
        $label.Text = $Message
        $label.SetBounds(20, 20, 440, 85)
        $form.Controls.Add($label)
        $accept = New-Object Windows.Forms.Button
        $accept.Text = '确定'
        if ($Confirm) { $accept.Text = '卸载' }
        $accept.DialogResult = [Windows.Forms.DialogResult]::OK
        $accept.SetBounds(260, 120, 90, 32)
        $form.Controls.Add($accept)
        $form.AcceptButton = $accept
        if ($Confirm) {
            $cancel = New-Object Windows.Forms.Button
            $cancel.Text = '取消'
            $cancel.DialogResult = [Windows.Forms.DialogResult]::Cancel
            $cancel.SetBounds(370, 120, 90, 32)
            $form.Controls.Add($cancel)
            $form.CancelButton = $cancel
            $form.ActiveControl = $cancel
        } else {
            $accept.Left = 370
            $form.CancelButton = $accept
        }
        return $form.ShowDialog()
    } finally {
        $form.Dispose()
    }
}

function Get-ChineseUninstallError($Failure) {
    $errorObject = $Failure.Exception
    while ($null -ne $errorObject) {
        if ($errorObject -is [IO.InvalidDataException]) { return $errorObject.Message }
        if ($errorObject -is [UnauthorizedAccessException] -or $errorObject -is [Security.SecurityException]) {
            return '没有权限访问安装文件，请检查目录权限后重试。'
        }
        if ($errorObject -is [IO.FileNotFoundException] -or $errorObject -is [IO.DirectoryNotFoundException] -or
            $errorObject -is [Management.Automation.ItemNotFoundException]) {
            return '找不到所需的安装文件或目录，请检查安装位置后重试。'
        }
        if ($errorObject -is [IO.IOException]) {
            return '无法访问安装文件，文件可能正被占用，请关闭相关程序后重试。'
        }
        $errorObject = $errorObject.InnerException
    }
    return '操作未完成，请检查安装文件和目录权限后重试。'
}

try {
    $installRoot = [IO.Path]::GetFullPath($PSScriptRoot)
    $manifestPath = Join-Path $installRoot 'notepadview-install.json'
    if (-not (Test-Path -LiteralPath $manifestPath)) {
        throw [IO.InvalidDataException]::new('找不到安装清单，无法安全卸载。请重新安装后再卸载。')
    }
    try {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        throw [IO.InvalidDataException]::new('无法读取安装清单，请检查文件权限及清单内容，或重新安装后再卸载。')
    }
    if ($manifest.app -ne 'NotepadView' -or $null -eq $manifest.files -or $manifest.files -isnot [array]) {
        throw [IO.InvalidDataException]::new('安装清单无效，无法安全卸载。请重新安装后再卸载。')
    }
    $prefix = $installRoot.TrimEnd('\') + '\'
    $files = @($manifest.files | ForEach-Object {
        if ($_ -isnot [string] -or [string]::IsNullOrWhiteSpace($_)) {
            throw [IO.InvalidDataException]::new('安装清单包含无效文件路径，无法安全卸载。')
        }
        $resolved = [IO.Path]::GetFullPath((Join-Path $installRoot $_))
        if (-not $resolved.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw [IO.InvalidDataException]::new('安装清单包含安装目录之外的路径，已停止卸载以保护您的文件。')
        }
        $resolved
    })
    $running = @(Get-Process -Name 'NotepadView' -ErrorAction SilentlyContinue | Where-Object {
        $_.Path -eq (Join-Path $installRoot 'NotepadView.exe')
    })
    if ($running.Count -gt 0) {
        throw [IO.InvalidDataException]::new('请先关闭 Notepad View，然后重新卸载。')
    }
    if (-not $Silent) {
        $answer = Show-UninstallDialog '确定卸载 Notepad View 吗？您的文档将会保留。' -Confirm
        if ($answer -ne [Windows.Forms.DialogResult]::OK) { exit 0 }
    }
    $directories = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($file in $files) {
        if ((Test-Path -LiteralPath $file) -and $file -ne $PSCommandPath) {
            Remove-Item -LiteralPath $file -Force
        }
        $parent = [IO.Path]::GetDirectoryName($file)
        while ($parent.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
            $directories.Add($parent) | Out-Null
            $parent = [IO.Path]::GetDirectoryName($parent)
        }
    }
    foreach ($directory in ($directories | Sort-Object Length -Descending)) {
        if ((Test-Path -LiteralPath $directory) -and @(Get-ChildItem -LiteralPath $directory -Force).Count -eq 0) {
            Remove-Item -LiteralPath $directory
        }
    }
    if (-not $NoRegister) {
        $classes = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Software\Classes', $true)
        if ($null -ne $classes) {
            try { $associationsChanged = Remove-FileAssociations -InstallRoot $installRoot -Classes $classes }
            finally { $classes.Dispose() }
            if ($associationsChanged) { Send-AssociationChangeNotification }
        }
        $shell = New-Object -ComObject WScript.Shell
        foreach ($folder in @([Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Desktop'))) {
            $shortcut = Join-Path $folder 'Notepad View.lnk'
            if (Test-Path -LiteralPath $shortcut) {
                if ($shell.CreateShortcut($shortcut).TargetPath -eq (Join-Path $installRoot 'NotepadView.exe')) {
                    Remove-Item -LiteralPath $shortcut -Force
                }
            }
        }
        $key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\NotepadView'
        if (Test-Path -LiteralPath $key) {
            if ((Get-ItemProperty -LiteralPath $key).InstallLocation -eq $installRoot) {
                Remove-Item -LiteralPath $key
            }
        }
    }
    Remove-Item -LiteralPath $manifestPath -Force
    Remove-Item -LiteralPath $PSCommandPath -Force
    if (@(Get-ChildItem -LiteralPath $installRoot -Force).Count -eq 0) {
        Remove-Item -LiteralPath $installRoot
    }
    if (-not $Silent) { Show-UninstallDialog 'Notepad View 卸载完成。您的文档已保留。' | Out-Null }
} catch {
    $message = '卸载失败：' + (Get-ChineseUninstallError $_)
    [Console]::Error.WriteLine($message)
    if (-not $Silent) {
        try { Show-UninstallDialog $message | Out-Null } catch { }
    }
    exit 1
}
