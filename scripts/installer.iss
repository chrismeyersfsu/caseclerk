; CaseClerk-Setup.exe: a real Windows installer around the PyInstaller onedir
; bundle, for the attorney's machine. Per-user, no UAC, no admin rights --
; this is a single-user utility, not something that needs to touch shared
; system state. Deliberately does NOT run `init`/`share setup` on its own;
; the on-site setup visit still runs those by hand (see the README). The
; only opt-in [Run] action is a read-only diagnostic (CaseClerk-Status.bat,
; which runs `doctor` + `share status`), unchecked by default.
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
SourceDir=..
OutputDir=dist-installer
OutputBaseFilename=CaseClerk-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}

[Files]
Source: "dist-windows\caseclerk\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "scripts\CaseClerk-Status.bat"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\CaseClerk Status"; Filename: "{app}\CaseClerk-Status.bat"; WorkingDir: "{app}"
Name: "{group}\CaseClerk folder"; Filename: "{app}"
Name: "{group}\Uninstall CaseClerk"; Filename: "{uninstallexe}"

[Run]
; Opt-in, unchecked, and skipped entirely in a silent install -- doctor and
; share status are read-only, but this installer still shouldn't run
; anything by default; the setup visit runs its own commands deliberately.
Filename: "{app}\CaseClerk-Status.bat"; Description: "Run caseclerk doctor"; Flags: postinstall skipifsilent unchecked

[Code]
const
  EnvironmentKey = 'Environment';

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

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    EnvAddPath(ExpandConstant('{app}'));
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    EnvRemovePath(ExpandConstant('{app}'));
end;
