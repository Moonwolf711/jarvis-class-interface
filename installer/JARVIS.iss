; JARVIS // PERSONA-CLASS INTERFACE — Inno Setup script
; Compile: iscc installer/JARVIS.iss

#define MyAppName      "JARVIS"
#define MyAppFullName  "JARVIS — Persona-Class Interface"
#define MyAppVersion   "1.0.0"
#define MyAppPublisher "Moonwolf711 / Tyler Yianacopolus"
#define MyAppURL       "https://github.com/Moonwolf711/jarvis-class-interface"
#define MyAppExeName   "JARVIS.exe"

[Setup]
AppId={{2B7A4E6C-9D8F-4E1A-A5C3-3F6A8E0D2C45}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppFullName}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\JARVIS
DefaultGroupName=JARVIS
DisableProgramGroupPage=yes
DisableDirPage=no
OutputDir=.
OutputBaseFilename=JARVIS-Setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\launcher\jarvis.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64compatible
WizardImageFile=
WizardSmallImageFile=
DisableWelcomePage=no

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &Desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked
Name: "startmenuicon"; Description: "Create a &Start Menu shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
; The launcher EXE — the heart of the install
Source: "..\launcher\JARVIS.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\launcher\jarvis.ico"; DestDir: "{app}"; Flags: ignoreversion
; Bundle the runtime project (code, personas, processors, docker-compose, etc.)
; — excludes secrets, venv, build artifacts, git data
Source: "..\code\*";              DestDir: "{app}\runtime\code";        Flags: recursesubdirs ignoreversion createallsubdirs
Source: "..\docker-compose.yml";  DestDir: "{app}\runtime";              Flags: ignoreversion
Source: "..\Dockerfile";          DestDir: "{app}\runtime";              Flags: ignoreversion
Source: "..\entrypoint.sh";       DestDir: "{app}\runtime";              Flags: ignoreversion
Source: "..\requirements.txt";    DestDir: "{app}\runtime";              Flags: ignoreversion
Source: "..\README.md";           DestDir: "{app}\runtime";              Flags: ignoreversion
Source: "..\tasks\*";             DestDir: "{app}\runtime\tasks";        Flags: recursesubdirs ignoreversion createallsubdirs
; .env template — user must fill in API keys post-install
Source: ".env.template";          DestDir: "{app}\runtime"; DestName: ".env"; Flags: onlyifdoesntexist

[Icons]
Name: "{group}\{#MyAppName}";                Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\jarvis.ico"; Tasks: startmenuicon
Name: "{group}\Uninstall {#MyAppName}";      Filename: "{uninstallexe}"; Tasks: startmenuicon
Name: "{commondesktop}\{#MyAppName}";        Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\jarvis.ico"; Tasks: desktopicon

[Run]
Filename: "notepad.exe"; Parameters: """{app}\runtime\.env"""; \
  Description: "Open .env to add ANTHROPIC_API_KEY + ELEVENLABS_API_KEY"; \
  Flags: postinstall skipifsilent shellexec runascurrentuser
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
function InitializeSetup(): Boolean;
var
  ErrCode: Integer;
  Resp: Integer;
begin
  // Lightweight Docker check — if docker.exe is on PATH we assume Docker Desktop is installed.
  if not Exec(ExpandConstant('{cmd}'), '/c where docker > nul 2>&1', '', SW_HIDE, ewWaitUntilTerminated, ErrCode) or (ErrCode <> 0) then
  begin
    Resp := MsgBox(
      'Docker Desktop was not found on your system.' + #13#10 + #13#10 +
      'JARVIS requires Docker Desktop to run.' + #13#10 +
      'Install it from https://docker.com/products/docker-desktop' + #13#10 + #13#10 +
      'Continue installation anyway?',
      mbConfirmation, MB_YESNO);
    Result := (Resp = IDYES);
  end
  else
  begin
    Result := True;
  end;
end;
