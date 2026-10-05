; Inno Setup script for MD Viewer.
;
; Build (see build-installer.bat):
;   ISCC /DAppVersion=<ver> installer\MDViewer.iss
; It expects dist\MDViewer\ to exist (produced by build.bat) and writes
; dist\MDViewer-Setup-<ver>.exe next to it.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName "MD Viewer"
#define AppPublisher "MD Viewer"
#define AppExeName "MDViewer.exe"

[Setup]
; Keep this AppId stable: Inno uses it to find and upgrade an installed copy.
AppId={{8F3C1E52-4A7B-4C9D-9E21-6B0D5A2F7C11}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\MDViewer
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
OutputDir=..\dist
OutputBaseFilename=MDViewer-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile=..\assets\icon.ico

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "associate"; Description: "Сделать MD Viewer приложением по умолчанию для .md"; GroupDescription: "Дополнительно:"
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительно:"

[Files]
Source: "..\dist\MDViewer\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Удалить {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
; Registers the ProgID/OpenWithProgids; the OS still owns the final .md choice.
Filename: "{app}\{#AppExeName}"; Parameters: "--install-default --no-prompt"; Tasks: associate; Flags: runascurrentuser
Filename: "{app}\{#AppExeName}"; Description: "Запустить {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\{#AppExeName}"; Parameters: "--uninstall-default"; Flags: runascurrentuser; RunOnceId: "UnregisterAssoc"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
