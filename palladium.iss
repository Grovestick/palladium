; Palladium, wrapped for somebody who has never heard of Python.
;
; Installs into this account rather than Program Files, so no administrator is asked
; for and no elevation prompt appears. What the server writes - settings, the library,
; invitations, logs - goes to %APPDATA%\Palladium and is left alone by the uninstaller
; unless it is asked to take that too.
;
; Built by pd-build-installer.py, which passes the version in.

#ifndef MyVersion
  #define MyVersion "0.0.0"
#endif

[Setup]
AppId={{7F3B6A2E-4D51-4E6F-9E77-PALLADIUM01}
AppName=Palladium
AppVersion={#MyVersion}
AppPublisher=Grovestick Studios
AppPublisherURL=https://palladium.video
DefaultDirName={code:WhereTo}
DefaultGroupName=Palladium
DisableProgramGroupPage=yes
; No folder page. It asks where the program goes, and on a first install it reads as
; "where are your films" - which is asked later, in Settings, where it belongs. The
; program is small and goes where every per-user program goes.
DisableDirPage=yes
PrivilegesRequired=lowest
; And the choice, on the page Setup draws itself: for me, or for everybody on this
; machine. Silent installs take the first, which is what every update does - an
; update that raised an administrator prompt on a television could never install
; itself. Choosing the second puts it in Program Files and asks once.
PrivilegesRequiredOverridesAllowed=dialog
; The server is running while this replaces it, so its files are always in use.
; Without these two, Setup stops and asks somebody to close it - which is a dialog
; waiting for a person in what was meant to be a silent install.
CloseApplications=force
RestartApplications=no
OutputDir=build
OutputBaseFilename=Palladium-Setup-{#MyVersion}
SetupIconFile=static\palladium.ico
UninstallDisplayIcon={app}\palladium-server.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "build\Palladium\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; the tray program of earlier builds: the server shows the icon now
Type: files; Name: "{app}\palladium.exe"

[Icons]
Name: "{group}\Palladium"; Filename: "{app}\palladium-server.exe"; Parameters: "--no-open"

[Run]
; Nothing is asked and nothing is arranged here. Starting at sign-in and letting the
; house in are both settings, and both live in Settings - This computer - where they
; can be changed and where the second one can ask for the administrator it needs.
; No skipifsilent: an install that replaces a running server should leave one
; running. Silently installed, this is the only thing that starts it again.
Filename: "{app}\palladium-server.exe"; Parameters: "--no-open"; Description: "Start Palladium now"; Flags: nowait postinstall

[UninstallRun]
Filename: "netsh"; Parameters: "advfirewall firewall delete rule name=""Palladium"""; Flags: runhidden; RunOnceId: "DropFirewallRule"

[UninstallDelete]
; the program's own leavings, not the library and not anybody's settings
Type: filesandordirs; Name: "{app}"

[Code]
// Where the program goes. For everybody means Program Files; for me means the folder
// every per-user program uses - and the same one Palladium has always used, so an
// install that has been here for months is updated where it stands rather than a
// second copy appearing beside it.
// Ask the running server to close before its files are replaced.
//
// Setup closes what holds its files by force, which on Windows is a termination: the
// program never runs a line of its own again, and the icon it put in the tray stays
// drawn until somebody happens to hover over it. taskkill without /F posts a close to
// its windows instead, which it does handle - it takes its own icon down and stops.
// Force is still there behind this for anything that will not go.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  Exec(ExpandConstant('{sys}	askkill.exe'), '/IM palladium-server.exe',
       '', SW_HIDE, ewWaitUntilTerminated, Code);
  if Code = 0 then
    Sleep(2500);          // let it take its icon down and let go of its files
  Result := '';
end;

function WhereTo(Param: String): String;
begin
  if IsAdminInstallMode then
    Result := ExpandConstant('{autopf}\Palladium')
  else
    Result := ExpandConstant('{localappdata}\Palladium');
end;

// Offer to take the settings and the library as well, but only when asked: somebody
// reinstalling wants their invitations and their watched marks to survive.
procedure CurUninstallStepChanged(CurStep: TUninstallStep);
var
  Papers: String;
begin
  // A silent uninstall asks nobody anything and keeps everything: it is run by a
  // script or by an installer replacing this one, and a dialog nobody can see is an
  // uninstall that never finishes.
  if (CurStep = usPostUninstall) and (not UninstallSilent) then
  begin
    Papers := ExpandConstant('{userappdata}\Palladium');
    if DirExists(Papers) then
      if MsgBox('Remove your settings, invitations and the library index as well?'#13#10 +
                'Your films are not touched either way.',
                mbConfirmation, MB_YESNO) = IDYES then
        DelTree(Papers, True, True, True);
  end;
end;
