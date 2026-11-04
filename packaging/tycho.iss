; The Tycho installer (#9), built with Inno Setup 6 from PyInstaller's dist\tycho:
;
;   iscc /DAppVersion=0.4.0 packaging\tycho.iss
;
; For the current user only, so no administrator prompt. Settings, keys, the speech
; model and the CUDA libraries live elsewhere (%APPDATA%\tycho, Windows Credential
; Manager, %LOCALAPPDATA%\tycho) and are kept when Tycho is uninstalled.
; Unsigned for now (Ben, 2026-11-02): Windows SmartScreen will warn on first run.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6F0E2C1D-7A5B-4C3E-9B8D-2E4F1A6C9D30}
AppName=Tycho
AppVersion={#AppVersion}
AppVerName=Tycho {#AppVersion}
AppPublisher=Tycho (an unofficial, open-source Star Citizen voice co-pilot)
AppPublisherURL=https://github.com/benshapyro/tycho-ai
AppSupportURL=https://github.com/benshapyro/tycho-ai/issues
LicenseFile=..\LICENSE
; The credits and the unofficial-fan-project notice (CIG's fankit, ADR 3 and 15). The
; build's copy: tools/notices.py writes it with the byte-order mark Inno Setup needs.
InfoBeforeFile=..\dist\tycho\NOTICE.txt
DefaultDirName={localappdata}\Programs\Tycho
DisableDirPage=yes
DefaultGroupName=Tycho
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
ChangesEnvironment=yes
OutputDir=..\dist
OutputBaseFilename=TychoSetup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=Tycho
UninstallDisplayIcon={app}\tycho.exe

[InstallDelete]
; An upgrade starts from a clean _internal: otherwise the old version's package
; metadata stays beside the new one and the app reports the old version (and the
; update check keeps offering the upgrade). Nothing of the player's lives here.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\tycho\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Through cmd /k, so if Tycho stops with a problem the window stays open to read it.
Name: "{group}\Tycho"; Filename: "{cmd}"; Parameters: "/k ""{app}\tycho.exe"" run"; IconFilename: "{app}\tycho.exe"; Comment: "Start listening"
Name: "{group}\Tycho check"; Filename: "{cmd}"; Parameters: "/k ""{app}\tycho.exe"" check"; IconFilename: "{app}\tycho.exe"; Comment: "Check the setup, in plain words"
Name: "{group}\Uninstall Tycho"; Filename: "{uninstallexe}"

[Registry]
; `tycho` on the user's PATH, so `tycho key set anthropic` works in any terminal.
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}"; Check: NeedsAddPath(ExpandConstant('{app}'))

[Run]
Filename: "{cmd}"; Parameters: "/k ""{app}\tycho.exe"" check"; Description: "Check the setup now"; Flags: postinstall nowait skipifsilent

[Code]
function NeedsAddPath(Folder: string): Boolean;
var
  Path: string;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', Path) then
    Result := True
  else
    Result := Pos(';' + Uppercase(Folder) + ';', ';' + Uppercase(Path) + ';') = 0;
end;

procedure RemovePath(Folder: string);
var
  Path: string;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', Path) then
    exit;
  { Wrapped in ';' so the folder matches as a whole entry, wherever it is. }
  Path := ';' + Path + ';';
  if StringChangeEx(Path, ';' + Folder + ';', ';', True) > 0 then
  begin
    while (Length(Path) > 0) and (Path[1] = ';') do
      Delete(Path, 1, 1);
    while (Length(Path) > 0) and (Path[Length(Path)] = ';') do
      Delete(Path, Length(Path), 1);
    RegWriteExpandStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', Path);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RemovePath(ExpandConstant('{app}'));
end;
