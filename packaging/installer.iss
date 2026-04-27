; Inno Setup script for Densa Deck
;
; Build order:
;   1. `python packaging/build_installer.ps1` or run the two steps manually:
;      a. `pyinstaller densa-deck.spec --clean --noconfirm`
;         -> produces `dist/densa-deck/` (folder-mode bundle)
;      b. `ISCC packaging/installer.iss`
;         -> produces `dist/Densa-Deck-Setup-<version>.exe`
;
; Inno Setup is free: https://jrsoftware.org/isinfo.php
; The ISCC compiler gets added to PATH on install.
;
; Code signing is NOT wired here (Windows SmartScreen will show an "unknown
; publisher" warning). To sign: get a code-signing cert (~$200-400/yr),
; use `SignTool sign /f ... /tr http://timestamp.digicert.com "<ExeFile>"`
; as a post-build step. Add [Setup] SignTool=mytool to sign the installer.

#define AppName "Densa Deck"
; AppId uniquely identifies this product to Inno Setup's installer + uninstaller.
; Generated fresh when the product rebranded from the earlier mtg-deck-engine
; prototype — changing it means the new installer treats this as a fresh
; product (no in-place upgrades from the pre-rename prototype builds).
#define AppId "{{DENSA-DECK-DB8A4F1E-7C3B-4D92-9A6E-5F3C1B7E8A2D}}"
#define AppVersion "0.4.1"
#define AppPublisher "Densanon LLC"
#define AppURL "https://toolkit.densanon.com/densa-deck.html"
#define AppExeName "densa-deck.exe"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
; Current-user install by default — avoids the UAC prompt and lets the app
; write to %USERPROFILE% for the card DB without permission gymnastics.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
OutputDir=..\dist
OutputBaseFilename=Densa-Deck-Setup-{#AppVersion}
SetupIconFile=densa-deck.ico
; DisableDirPage=yes  ; uncomment if you want a one-click install
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"
Name: "registerprotocol"; Description: "Register the densa-deck:// URL scheme (enables one-click license activation from the Stripe success page)"; GroupDescription: "Integrations:"

[Files]
; Ship the entire PyInstaller folder. `\*` plus `recursesubdirs` picks up
; the _internal/ dir, bundled Python DLLs, and the analyst/static assets.
Source: "..\dist\densa-deck\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Parameters: "app"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Parameters: "app"; Tasks: desktopicon

[Registry]
; URI scheme registration — mirrors what `densa-deck register-protocol` writes
; at runtime, but done once at install time so the user doesn't have to
; execute a separate command. Using {app} instead of the Python interpreter
; means the packaged .exe handles the deep link directly.
Root: HKCU; Subkey: "Software\Classes\densa-deck"; ValueType: string; ValueName: ""; ValueData: "URL:Densa Deck Protocol"; Flags: uninsdeletekey; Tasks: registerprotocol
Root: HKCU; Subkey: "Software\Classes\densa-deck"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""; Flags: uninsdeletekey; Tasks: registerprotocol
Root: HKCU; Subkey: "Software\Classes\densa-deck\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExeName}"" app ""%1"""; Flags: uninsdeletekey; Tasks: registerprotocol

[Run]
Filename: "{app}\{#AppExeName}"; Parameters: "app"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
