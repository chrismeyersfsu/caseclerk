; CaseClerk-Setup.exe: a real Windows installer around the PyInstaller onedir
; bundle, for the attorney's machine. Per-user, no UAC, no admin rights --
; this is a single-user utility, not something that needs to touch shared
; system state. Deliberately does NOT run `init`/`share setup` on its own;
; the on-site setup visit can run those from caseclerk-tray.exe's Settings
; window, or by hand on the command line (see the README). The Start Menu's
; primary "CaseClerk" shortcut launches the tray app; CaseClerk-Status.bat
; (runs `doctor` + `share status`) is still there as a read-only diagnostic.
; the finish page's "Launch CaseClerk" checkbox (checked by default) starts
; the tray app once install completes; the "start automatically" [Tasks]
; entry (unchecked by default) writes the same HKCU Run key caseclerk-tray's
; own Settings > "Start CaseClerk when Windows starts" checkbox does --
; uninstall always removes that value regardless of which one wrote it.
;
; Both install (upgrading over a running copy) and uninstall best-effort
; terminate caseclerk.exe/caseclerk-tray.exe before touching files: a real
; failure report showed installing v0.4.1 over a running `caseclerk.exe
; serve` (sharing was on) silently kept the old binaries in place --
; Setup "succeeded" but --version still reported the old build, because the
; running process still had the file open. CloseApplications below (Restart
; Manager) is Windows' own, more precise mechanism for this -- it detects
; processes holding a *specific file about to be replaced* open, not just
; anything by that name, and (per Inno's docs) closes them silently in
; /SILENT and /VERYSILENT modes rather than prompting, so unattended
; installs behave the same as interactive ones. Restart Manager should
; reliably catch our case: a running process always keeps its own EXE file
; handle open for as long as it runs, regardless of whether it has a
; window, a console, or was launched detached (this is fundamental to how
; Windows loads a PE image, not a GUI-specific mechanism) -- but
; PrepareToInstall below still does a direct, unconditional taskkill as a
; backstop in case Restart Manager is ever unavailable (e.g. its service is
; disabled by policy), mirroring exactly what uninstall already does.
; RestartApplications is off: Restart Manager's own "restart what I closed"
; would relaunch `caseclerk.exe serve` with its bare original command line,
; completely bypassing share.py's pidfile bookkeeping (share.json would
; still point at the now-dead old PID) -- silently leaving an
; un-tracked server running is worse than leaving sharing off after an
; upgrade for the user (or the tray, via its own menu) to restart deliberately.
;
; [InstallDelete] below wipes the whole _internal directory before every
; install: another real failure report found an old, version-suffixed
; caseclerk_cli-0.4.0.dist-info sitting right alongside the new
; caseclerk_cli-0.4.1.dist-info after an upgrade -- Inno's [Files] overwrites
; same-named files in place but never deletes ones a newer release simply
; stopped shipping, so an upgrade only ever *adds/updates* files, never
; *removes* stale ones. importlib.metadata found the old dist-info first, so
; `caseclerk --version` kept reporting the old version forever (and the
; updater thought an update was perpetually still pending). See the
; [InstallDelete] entry's own comment for the full explanation.
;
; Build with (from the repo root, matching release.yml):
;   iscc /DMyAppVersion=X.Y.Z scripts\installer.iss
; MyAppVersion defaults to a placeholder for standalone/dev compiles.
; SourceDir=".." below (relative to this script's own directory, scripts\,
; so it resolves to the repo root regardless of the compiler's own working
; directory) makes every relative [Files] Source: path resolve against the
; repo root -- so this always picks up scripts/build_windows.py's default
; output layout (dist-windows\caseclerk\). OutputDir, once SourceDir is set,
; is ALSO resolved relative to SourceDir (not to this script's directory --
; confirmed the hard way, via a real windows-latest CI run misplacing the
; installer one level up), hence no "..\" prefix on it below.

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-dev"
#endif

#define MyAppName "CaseClerk"
#define MyAppPublisher "CaseClerk"
#define MyAppExeName "caseclerk.exe"
#define MyAppTrayExeName "caseclerk-tray.exe"
; Fixed for the lifetime of the app -- changing this would orphan the old
; uninstall registration (HKCU\...\Uninstall\{AppId}_is1) instead of letting
; a re-run of Setup upgrade/repair the existing install. Braces are baked
; into the value itself (not added at the AppId= call site) since ISPP
; substitutes {#MyAppId} as plain text -- see the AppId= comment below for
; why the escaping has to be split exactly this way.
#define MyAppId "{6A3B09B5-E95C-4D9F-AEC6-67AB9A91414C}"

[Setup]
; {#MyAppId} expands (via ISPP, at preprocess time) to the literal text
; "{6A3B09B5-...}" -- braces included, since that's what's in the #define
; above. Inno's OWN compiler-level constant syntax then sees that leading
; "{" and would try to look it up as a {constant} unless escaped, so this
; needs exactly one extra "{" prepended (making the final compiler-visible
; text start with "{{", Inno's escape for a literal "{") and zero extra "}"
; appended (the value's own trailing "}" already closes it correctly).
AppId={{#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\CaseClerk
DefaultGroupName=CaseClerk
DisableProgramGroupPage=yes
; No UAC prompt, ever: installs under the current user's own profile.
PrivilegesRequired=lowest
; Built-in Inno Setup 6 mechanism that notifies Explorer/the shell of
; environment-variable changes (our PATH addition below) once install
; finishes, so a freshly-opened PowerShell picks it up without a reboot.
ChangesEnvironment=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; See the header comment above: Restart Manager detects+closes whatever
; holds our files open (works silently, unattended, in /SILENT and
; /VERYSILENT); RestartApplications is off so Inno never relaunches
; caseclerk.exe/caseclerk-tray.exe itself -- our own [Run]/[Tasks]/tray menu
; own that decision instead.
CloseApplications=yes
RestartApplications=no
SourceDir=..
OutputDir=dist-installer
OutputBaseFilename=CaseClerk-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}

[InstallDelete]
; Wipe the whole _internal support directory before every install (fresh or
; upgrade) rather than letting the [Files] copy below merge into whatever's
; already there. A real user hit exactly this: after upgrading, importlib.metadata
; found an OLD, version-suffixed caseclerk_cli-0.4.0.dist-info sitting right
; alongside the new caseclerk_cli-0.4.1.dist-info -- Inno overwrites same-named
; files in place but never deletes ones a newer release simply stopped shipping,
; and PyInstaller's _internal is full of version-suffixed names -- so `--version`
; kept reporting the old version forever, and the auto-updater thought an update
; was perpetually still pending. Runs (per Inno's install sequence) after
; PrepareToInstall below has already best-effort closed anything that might still
; have these files open, and before [Files] extracts the fresh copy. A no-op on a
; first-time install, where _internal doesn't exist yet.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
; dist-windows\caseclerk already contains BOTH caseclerk.exe and
; caseclerk-tray.exe sharing one _internal (see scripts/build_windows.py) --
; nothing tray-specific needed here beyond that single recursive copy.
Source: "dist-windows\caseclerk\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "scripts\CaseClerk-Status.bat"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\CaseClerk"; Filename: "{app}\{#MyAppTrayExeName}"; WorkingDir: "{app}"
Name: "{group}\CaseClerk Status"; Filename: "{app}\CaseClerk-Status.bat"; WorkingDir: "{app}"
Name: "{group}\CaseClerk folder"; Filename: "{app}"
Name: "{group}\Uninstall CaseClerk"; Filename: "{uninstallexe}"

[Tasks]
; Unchecked by default -- same posture as the [Run] diagnostic below: this
; installer doesn't turn anything automatic on unless asked to.
Name: "autostart"; Description: "Start CaseClerk automatically when Windows starts"; Flags: unchecked

[Run]
; Checked by default -- the tray is now the primary interface, so most
; installs should end with it running. `nowait` since it's a background app
; that must not block the finish page; skipped entirely in a silent install.
Filename: "{app}\{#MyAppTrayExeName}"; Description: "Launch CaseClerk"; Flags: postinstall skipifsilent nowait
; Opt-in, unchecked, and skipped entirely in a silent install -- doctor and
; share status are read-only, but this installer still shouldn't run
; anything by default; the setup visit runs its own commands deliberately.
Filename: "{app}\CaseClerk-Status.bat"; Description: "Run caseclerk doctor"; Flags: postinstall skipifsilent unchecked

[Code]
const
  EnvironmentKey = 'Environment';
  // Must match caseclerk_tray.autostart.RUN_KEY_PATH / VALUE_NAME exactly --
  // this is the same per-user Run key both the installer's opt-in [Tasks]
  // entry and the tray app's own Settings checkbox write.
  AutostartRunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';
  AutostartValueName = 'CaseClerk';

// Adds {app} to HKCU's PATH, de-duplicating against an existing entry from
// a previous install/repair (case-insensitive, like Windows' own PATH
// lookup). Reference pattern: the widely-used community modpath.iss
// (legroom.net), adapted to a single fixed directory.
procedure EnvAddPath(Path: string);
var
  Paths: string;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, EnvironmentKey, 'Path', Paths) then
    Paths := '';

  if Pos(';' + Uppercase(Path) + ';', ';' + Uppercase(Paths) + ';') > 0 then
    exit;

  if (Length(Paths) > 0) and (Paths[Length(Paths)] <> ';') then
    Paths := Paths + ';';
  Paths := Paths + Path + ';';

  // HKCU\Environment\Path is a REG_EXPAND_SZ by Windows convention (other
  // software's entries may contain unexpanded %VARS%); RegWriteStringValue
  // would silently downgrade it to a plain REG_SZ and break that expansion,
  // so this has to be RegWriteExpandStringValue, not RegWriteStringValue.
  if RegWriteExpandStringValue(HKEY_CURRENT_USER, EnvironmentKey, 'Path', Paths) then
    Log(Format('Added to PATH: [%s]', [Path]))
  else
    Log(Format('Failed to add to PATH: [%s]', [Path]));
end;

// Removes exactly the {app} segment from HKCU's PATH on uninstall, leaving
// every other entry the user (or other software) added untouched. Normalizes
// to a leading+trailing semicolon first so first/middle/last entries are all
// bounded the same way -- no special-casing the ends, no off-by-one on the
// boundary semicolons.
procedure EnvRemovePath(Path: string);
var
  Paths: string;
  Needle: string;
  P: Integer;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, EnvironmentKey, 'Path', Paths) then
    exit;

  if (Length(Paths) = 0) or (Paths[Length(Paths)] <> ';') then
    Paths := Paths + ';';
  if (Length(Paths) = 0) or (Paths[1] <> ';') then
    Paths := ';' + Paths;

  Needle := ';' + Path + ';';
  P := Pos(Uppercase(Needle), Uppercase(Paths));
  if P = 0 then
    exit;

  { Remove the needle but leave one semicolon behind as the new boundary
    between whatever entries are now adjacent. }
  Delete(Paths, P, Length(Needle) - 1);

  { Strip the normalization semicolons back off before writing back. }
  if (Length(Paths) > 0) and (Paths[1] = ';') then
    Delete(Paths, 1, 1);
  if (Length(Paths) > 0) and (Paths[Length(Paths)] = ';') then
    Delete(Paths, Length(Paths), 1);

  // See the matching comment in EnvAddPath: must preserve REG_EXPAND_SZ.
  if RegWriteExpandStringValue(HKEY_CURRENT_USER, EnvironmentKey, 'Path', Paths) then
    Log(Format('Removed from PATH: [%s]', [Path]))
  else
    Log(Format('Failed to remove from PATH: [%s]', [Path]));
end;

// Best-effort, unconditional backstop alongside CloseApplications/Restart
// Manager (see the header + [Setup] comments): /F force-kills, run hidden,
// and a nonzero exit (e.g. the process simply isn't running, the overwhelmingly
// common case) is not an error worth surfacing -- shared by both the
// pre-install and uninstall call sites so there is exactly one place that
// lists which processes matter.
procedure KillRunningProcesses;
var
  ResultCode: Integer;
begin
  Exec('taskkill.exe', '/IM {#MyAppExeName} /F', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec('taskkill.exe', '/IM {#MyAppTrayExeName} /F', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

// Runs after Setup's own disk-space checks but strictly before any file is
// extracted/copied -- the same "before files are touched" timing as
// CurUninstallStepChanged's usUninstall below, just on the install side.
// Returning '' means "proceed"; a non-empty string would abort Setup with
// that message, which we never want here (a failed taskkill is not worth
// blocking the install over).
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  KillRunningProcesses;
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    EnvAddPath(ExpandConstant('{app}'));
    // Same HKCU Run key caseclerk-tray's own Settings > "Start CaseClerk
    // when Windows starts" checkbox writes (caseclerk_tray.autostart) --
    // checking this [Tasks] entry here and toggling that checkbox later
    // converge on the exact same registry value.
    if WizardIsTaskSelected('autostart') then
      RegWriteStringValue(HKEY_CURRENT_USER, AutostartRunKey, AutostartValueName,
        '"' + ExpandConstant('{app}\{#MyAppTrayExeName}') + '"');
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    // Fires before Inno removes any files -- same KillRunningProcesses
    // PrepareToInstall uses, so a running `caseclerk.exe serve` (which
    // actually holds files open) and a running tray icon (which doesn't,
    // but would otherwise linger after its Start Menu entry and install
    // directory vanish) are both handled the same way on both paths.
    KillRunningProcesses;
  if CurUninstallStep = usPostUninstall then
  begin
    EnvRemovePath(ExpandConstant('{app}'));
    // Removed unconditionally, not just when the [Tasks] entry wrote it --
    // the tray app's own Settings checkbox can also have enabled autostart
    // after install, and uninstall must not leave that key pointing at a
    // now-deleted exe either way.
    RegDeleteValue(HKEY_CURRENT_USER, AutostartRunKey, AutostartValueName);
  end;
end;
