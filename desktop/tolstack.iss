; tolstack for Windows - per-user installer (no admin rights). build.py compiles it with:
;   iscc /DAppVersion=1.0.0 /DSourceDir=<dist\tolstack> /DOutputDir=<dist> /DIconFile=<build\tolstack.ico> tolstack.iss
; Installs to %LOCALAPPDATA%\Programs\tolstack, adds tolstack to the Start menu (and optionally the desktop) and lists it
; in Settings > Apps. The window keeps its data (what you saved in it) in %LOCALAPPDATA%\tolstack, which
; uninstalling leaves in place so a reinstall finds it again.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "dist\tolstack"
#endif
#ifndef OutputDir
  #define OutputDir "dist"
#endif
#ifndef IconFile
  #define IconFile "build\tolstack.ico"
#endif

[Setup]
AppId={{E81247BA-698B-569E-A6DE-E1E1C366EEFB}
AppName=tolstack
AppVersion={#AppVersion}
AppVerName=tolstack {#AppVersion}
AppPublisher=Juwen Lie
AppPublisherURL=https://juw12.github.io/tolstack/
AppSupportURL=https://github.com/juw12/tolstack
DefaultDirName={autopf}\tolstack
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename=tolstack-Windows-Setup
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\tolstack.exe
UninstallDisplayName=tolstack
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=no
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\tolstack"; Filename: "{app}\tolstack.exe"; Comment: "1-D tolerance stack-up from a STEP assembly and 2D PDF drawings"
Name: "{userdesktop}\tolstack"; Filename: "{app}\tolstack.exe"; Comment: "1-D tolerance stack-up from a STEP assembly and 2D PDF drawings"; Tasks: desktopicon

[Run]
Filename: "{app}\tolstack.exe"; Description: "{cm:LaunchProgram,tolstack}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\tolstack.exe"; Parameters: "--stop"; Flags: runhidden waituntilterminated; RunOnceId: "Stoptolstack"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Result := '';
  { Updating: close an open tolstack window first so its files can be replaced. }
  if FileExists(ExpandConstant('{app}\tolstack.exe')) then
    Exec(ExpandConstant('{app}\tolstack.exe'), '--stop', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;
